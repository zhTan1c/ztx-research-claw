"""
ztx-research-claw / agents / citation_formatter.py
CitationFormatter Agent — 将 [cite:paper_id] 占位符替换为 [N] 编号引用，
并生成统一的 references.bib 文件。
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
from openai import OpenAI

from models import (
    AgentCheckpoint,
    ChapterDraft,
    Citation,
    Paper,
)

logger = logging.getLogger(__name__)

# 匹配 [cite:paper_id] 占位符
_CITE_PATTERN = re.compile(r"\[cite:([^\]]+)\]")


class CitationFormatter:
    """CitationFormatter Agent：全局引用编号替换 + BibTeX 生成。"""

    def __init__(self, config: dict) -> None:
        # LLM 配置
        llm_cfg = config.get("llm", {}).get("deepseek_v4_pro", {})
        if not llm_cfg:
            logger.error("config 中未找到 llm.deepseek_v4_pro 配置")
            raise ValueError("config 中未找到 llm.deepseek_v4_pro 配置")

        # 解析环境变量引用（${VAR_NAME}）
        api_key = llm_cfg.get("api_key", "")
        if api_key.startswith("${") and api_key.endswith("}"):
            env_var = api_key[2:-1]
            api_key = os.environ.get(env_var, "")
            if not api_key:
                logger.warning("环境变量 %s 未设置，API key 为空", env_var)

        base_url = llm_cfg.get("base_url", "https://api.deepseek.com")
        self.model: str = llm_cfg.get("model", "deepseek-v4-pro")
        self.default_temperature: float = llm_cfg.get("temperature", 0.6)
        self.default_max_tokens: int = llm_cfg.get("max_tokens", 8192)

        # 创建 OpenAI 客户端（DeepSeek 兼容 OpenAI 接口）
        http_client = httpx.Client(trust_env=True)
        self.client = OpenAI(api_key=api_key, base_url=base_url, http_client=http_client)
        logger.info(
            "CitationFormatter LLM 客户端初始化: model=%s, base_url=%s",
            self.model,
            base_url,
        )

        # Agent 配置
        self.agent_cfg: dict = config.get("agents", {}).get("citation_formatter", {})
        self.bib_format: str = self.agent_cfg.get("bib_format", "bibtex")
        self.citation_style: str = self.agent_cfg.get("citation_style", "ieee")
        self.validation_rules: dict = self.agent_cfg.get("validation_rules", {})

        # Checkpoint 配置
        ckpt_cfg = config.get("checkpoint", {})
        self.checkpoint_dir: str = ckpt_cfg.get("dir", "./outputs/checkpoints")
        self.checkpoint_file: str = "citation_formatter.json"
        self.checkpoint_path: str = str(
            Path(self.checkpoint_dir) / self.checkpoint_file
        )

        logger.info(
            "CitationFormatter 初始化完成: bib_format=%s, citation_style=%s, "
            "validation_rules=%s",
            self.bib_format,
            self.citation_style,
            list(self.validation_rules.keys()) if self.validation_rules else "disabled",
        )

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def run(
        self,
        drafts: list[ChapterDraft],
        papers: list[Paper],
        bib_path: str,
    ) -> tuple[list[ChapterDraft], str]:
        """
        执行完整的引用格式化流程。

        1. 构建 paper_id -> Paper 映射
        2. 全局扫描所有草稿，按首次出现顺序分配编号
        3. 替换各草稿中的 [cite:paper_id] 占位符
        4. 生成 BibTeX 条目
        5. 可选用 LLM 验证/补全 BibTeX 字段
        6. 写入 references.bib
        7. 返回 (updated_drafts, bib_content)
        """

        # 1. 构建 paper_id -> Paper 映射
        paper_map: dict[str, Paper] = {}
        for paper in papers:
            paper_map[paper.paper_id] = paper
        logger.info("构建 paper 映射: %d 篇论文", len(paper_map))

        # 2. 全局分配引用编号（按首次出现顺序）
        citation_map = self._build_citation_map(drafts)
        logger.info(
            "全局引用编号分配完成: 共 %d 条引用", len(citation_map)
        )

        # 3. 替换各草稿中的占位符
        updated_drafts: list[ChapterDraft] = []
        all_cited_ids: set[str] = set()

        for draft in drafts:
            new_draft = self._replace_citations(draft, citation_map)
            # 记录本章节引用了哪些 paper_id
            cited_ids = [
                pid for pid in citation_map
                if re.search(re.escape(f"[{citation_map[pid]}]"), new_draft.content)
                or pid in draft.citations
            ]
            new_draft.citations = cited_ids
            all_cited_ids.update(cited_ids)
            updated_drafts.append(new_draft)
            logger.info(
                "章节 %s: 替换完成，引用 %d 篇论文",
                draft.chapter_id,
                len(cited_ids),
            )

        # 4. 收集被引用的论文，生成 BibTeX
        cited_papers: dict[str, Paper] = {}
        missing_ids: list[str] = []
        for pid in sorted(all_cited_ids, key=lambda x: citation_map.get(x, 0)):
            if pid in paper_map:
                cited_papers[pid] = paper_map[pid]
            else:
                missing_ids.append(pid)
                logger.warning("paper_id=%s 在 papers 列表中未找到，无法生成 BibTeX", pid)

        bib_content = self._generate_bibtex(cited_papers, citation_map)
        logger.info(
            "生成 BibTeX 条目: %d 条 (缺失论文: %d)",
            len(cited_papers),
            len(missing_ids),
        )

        # 5. 可选：用 LLM 验证/补全 BibTeX
        if self.validation_rules and any(self.validation_rules.values()):
            logger.info("启用 BibTeX 验证/补全 (validation_rules=%s)", self.validation_rules)
            bib_content = self._validate_bibtex(bib_content)

        # 6. 写入文件
        bib_path_obj = Path(bib_path)
        bib_path_obj.parent.mkdir(parents=True, exist_ok=True)
        bib_path_obj.write_text(bib_content, encoding="utf-8")
        logger.info("references.bib 已写入: %s (%d 字符)", bib_path, len(bib_content))

        # 7. 保存 checkpoint
        self._save_checkpoint(updated_drafts, bib_content, bib_path)

        return updated_drafts, bib_content

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _build_citation_map(self, drafts: list[ChapterDraft]) -> dict[str, int]:
        """
        扫描所有草稿中的 [cite:paper_id] 占位符，
        按首次出现的全局顺序分配编号 1, 2, 3, ...
        """
        citation_map: dict[str, int] = {}
        next_num = 1

        for draft in drafts:
            matches = _CITE_PATTERN.findall(draft.content)
            for paper_id in matches:
                paper_id = paper_id.strip()
                if paper_id and paper_id not in citation_map:
                    citation_map[paper_id] = next_num
                    next_num += 1

        # 也扫描 draft.citations 列表，确保不遗漏
        for draft in drafts:
            for paper_id in draft.citations:
                paper_id = paper_id.strip()
                if paper_id and paper_id not in citation_map:
                    citation_map[paper_id] = next_num
                    next_num += 1

        return citation_map

    def _replace_citations(
        self, draft: ChapterDraft, citation_map: dict[str, int]
    ) -> ChapterDraft:
        """
        替换单个草稿中的 [cite:paper_id] 为 [N]。
        返回新的 ChapterDraft 实例。
        """
        missing: list[str] = []

        def _replacer(match: re.Match) -> str:
            paper_id = match.group(1).strip()
            if paper_id in citation_map:
                return f"[{citation_map[paper_id]}]"
            else:
                missing.append(paper_id)
                return match.group(0)  # 保留原样

        new_content = _CITE_PATTERN.sub(_replacer, draft.content)

        if missing:
            logger.warning(
                "章节 %s 中有 %d 个未映射的 cite 占位符: %s",
                draft.chapter_id,
                len(missing),
                missing,
            )

        # 更新 word_count（引用编号变化不影响太多，但保持一致）
        # 复用 writer 的计数逻辑
        chinese_chars = len(
            re.findall(r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]", new_content)
        )
        text_no_chinese = re.sub(
            r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]", " ", new_content
        )
        word_count = chinese_chars + len(text_no_chinese.split())

        new_draft = ChapterDraft(
            chapter_id=draft.chapter_id,
            title=draft.title,
            content=new_content,
            citations=draft.citations,
            word_count=word_count,
            status="cited",
        )
        return new_draft

    def _generate_bibtex(
        self, cited_papers: dict[str, Paper], citation_map: dict[str, int]
    ) -> str:
        """
        为所有被引用的论文生成 BibTeX 条目。
        优先使用 Paper.preliminary_bib；其次使用 Citation.to_bibtex()；
        兜底从 Paper 字段手动构造。
        """
        entries: list[str] = []
        used_keys: set[str] = set()

        # 按引用编号排序
        sorted_papers = sorted(
            cited_papers.items(), key=lambda x: citation_map.get(x[0], 999999)
        )

        for paper_id, paper in sorted_papers:
            # 优先使用 preliminary_bib
            if paper.preliminary_bib and paper.preliminary_bib.strip():
                bib_entry = paper.preliminary_bib.strip()
                # 确保有正确的 key（避免重复 key）
                key = self._unique_key(paper.key, used_keys)
                used_keys.add(key)
                # 如果 preliminary_bib 没有标准的 @type{key,...} 格式，包装一下
                if not bib_entry.startswith("@"):
                    bib_entry = self._construct_bibtex_from_paper(paper, key)
                else:
                    # 替换 key 以确保唯一性
                    bib_entry = re.sub(
                        r"(@\w+\{)[^,]+,",
                        rf"\g<1>{key},",
                        bib_entry,
                        count=1,
                    )
                entries.append(bib_entry)
                logger.debug("paper_id=%s: 使用 preliminary_bib (key=%s)", paper_id, key)
                continue

            # 使用 Citation 对象构建
            key = self._unique_key(paper.key, used_keys)
            used_keys.add(key)
            bib_entry = self._construct_bibtex_from_paper(paper, key)
            entries.append(bib_entry)
            logger.debug("paper_id=%s: 从 Paper 字段构造 BibTeX (key=%s)", paper_id, key)

        return "\n\n".join(entries) + "\n" if entries else ""

    def _construct_bibtex_from_paper(self, paper: Paper, key: str) -> str:
        """从 Paper 字段构造 BibTeX 条目。"""
        # 确定 entry_type
        if paper.venue:
            venue_lower = paper.venue.lower()
            if any(
                kw in venue_lower
                for kw in [
                    "conference", "proceedings", "symposium", "workshop",
                    "icml", "neurips", "nips", "iclr", "aaai", "ijcai",
                    "cvpr", "iccv", "eccv", "acl", "emnlp", "naacl",
                    "iros", "icra", "rss", "corl",
                ]
            ):
                entry_type = "inproceedings"
            else:
                entry_type = "article"
        else:
            entry_type = "article"

        citation = Citation(
            key=key,
            entry_type=entry_type,
            title=paper.title,
            authors=" and ".join(paper.authors) if paper.authors else "",
            year=paper.year or 0,
            journal=paper.venue if entry_type == "article" else "",
            booktitle=paper.venue if entry_type == "inproceedings" else "",
            doi=paper.doi or "",
            url=paper.pdf_url or "",
            arxiv_id=paper.arxiv_id or "",
            abstract=paper.abstract[:500] if paper.abstract else "",
        )
        return citation.to_bibtex()

    @staticmethod
    def _unique_key(base_key: str, used_keys: set[str]) -> str:
        """确保 BibTeX key 唯一，重复时追加字母后缀。"""
        if base_key not in used_keys:
            return base_key
        suffix = ord("a")
        while f"{base_key}{chr(suffix)}" in used_keys:
            suffix += 1
            if suffix > ord("z"):
                # 极端情况：追加数字
                suffix_num = 2
                while f"{base_key}{suffix_num}" in used_keys:
                    suffix_num += 1
                return f"{base_key}{suffix_num}"
        return f"{base_key}{chr(suffix)}"

    def _validate_bibtex(self, bib_entries: str) -> str:
        """
        可选步骤：使用 DeepSeek 检查和补全 BibTeX 条目中的缺失字段。
        仅在 validation_rules 中启用了相关检查时执行。
        """
        if not bib_entries.strip():
            return bib_entries

        checks: list[str] = []
        if self.validation_rules.get("check_doi"):
            checks.append("检查并补全缺失的 DOI 字段")
        if self.validation_rules.get("check_url"):
            checks.append("检查并补全缺失的 URL 字段")
        if self.validation_rules.get("check_venue"):
            checks.append("检查 journal/booktitle 字段是否正确")

        if not checks:
            return bib_entries

        prompt = (
            "你是一位 BibTeX 专家。请检查以下 BibTeX 条目，执行以下任务：\n"
            + "\n".join(f"{i+1}. {c}" for i, c in enumerate(checks))
            + "\n\n规则：\n"
            "- 仅修改/补充确实缺失或明显错误的字段\n"
            "- 不要改变 BibTeX key\n"
            "- 不要删除已有条目\n"
            "- 不确定的字段保持原样，不要编造\n"
            "- 输出完整的修正后 BibTeX 内容，不要添加额外解释\n\n"
            f"以下是待检查的 BibTeX 条目：\n\n{bib_entries}"
        )

        logger.info("调用 LLM 验证 BibTeX (%d 条目)...", bib_entries.count("@"))
        try:
            validated = self._call_llm(prompt, max_tokens=self.default_max_tokens)
            # 基本检查：确保返回内容包含 BibTeX 条目
            if "@" in validated and validated.strip():
                logger.info("BibTeX 验证完成，返回 %d 字符", len(validated))
                return validated
            else:
                logger.warning("LLM 返回的 BibTeX 验证结果异常，使用原始内容")
                return bib_entries
        except Exception as exc:
            logger.error("BibTeX 验证失败: %s，使用原始内容", exc)
            return bib_entries

    def _call_llm(self, prompt: str, max_tokens: int = 4096) -> str:
        """调用 DeepSeek LLM，返回生成的文本。带错误处理和重试。"""
        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                logger.debug(
                    "LLM 调用 attempt %d/%d: prompt=%d chars, max_tokens=%d",
                    attempt,
                    max_retries,
                    len(prompt),
                    max_tokens,
                )
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=max_tokens,
                    temperature=self.default_temperature,
                    stream=False,
                )
                text = response.choices[0].message.content or ""
                logger.debug("LLM 返回 %d 字符", len(text))
                return text

            except Exception as exc:
                logger.warning(
                    "LLM 调用失败 (attempt %d/%d): %s",
                    attempt,
                    max_retries,
                    exc,
                )
                if attempt == max_retries:
                    logger.error("LLM 调用在 %d 次重试后仍然失败", max_retries)
                    raise RuntimeError(
                        f"LLM 调用失败，已重试 {max_retries} 次: {exc}"
                    ) from exc

        # 理论上不会到这里
        raise RuntimeError("LLM 调用失败：未知错误")

    # ------------------------------------------------------------------
    # Checkpoint
    # ------------------------------------------------------------------

    def save_checkpoint(
        self,
        drafts: list[ChapterDraft],
        bib_content: str,
        bib_path: str,
    ) -> None:
        """保存 citation_formatter checkpoint。"""
        checkpoint = AgentCheckpoint(
            agent_name="citation_formatter",
            status="completed",
            phase="citation_formatting",
            progress=1.0,
            data={
                "drafts": [d.to_dict() for d in drafts],
                "bib_path": bib_path,
                "bib_content": bib_content,
                "cited_count": sum(len(d.citations) for d in drafts),
            },
            error=None,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        try:
            Path(self.checkpoint_path).parent.mkdir(parents=True, exist_ok=True)
            Path(self.checkpoint_path).write_text(
                checkpoint.to_json(), encoding="utf-8"
            )
            logger.info(
                "CitationFormatter checkpoint 已保存: %s", self.checkpoint_path
            )
        except Exception as exc:
            logger.error("保存 checkpoint 失败: %s", exc)

    def _save_checkpoint(
        self,
        drafts: list[ChapterDraft],
        bib_content: str,
        bib_path: str,
    ) -> None:
        """内部保存 checkpoint（由 run 调用）。"""
        self.save_checkpoint(drafts, bib_content, bib_path)

    def load_checkpoint(
        self,
    ) -> Optional[tuple[list[ChapterDraft], str]]:
        """
        加载 citation_formatter checkpoint。
        返回 (drafts, bib_content) 或 None（无有效 checkpoint）。
        """
        p = Path(self.checkpoint_path)
        if not p.exists():
            logger.info("CitationFormatter checkpoint 文件不存在")
            return None

        try:
            raw = p.read_text(encoding="utf-8")
            ckpt = AgentCheckpoint.from_json(raw)
        except (json.JSONDecodeError, TypeError, KeyError) as exc:
            logger.error(
                "CitationFormatter checkpoint 文件损坏: %s — %s",
                self.checkpoint_path,
                exc,
            )
            return None

        if ckpt.status != "completed":
            logger.warning(
                "CitationFormatter checkpoint 状态为 '%s'，忽略", ckpt.status
            )
            return None

        drafts_data = ckpt.data.get("drafts", [])
        bib_content = ckpt.data.get("bib_content", "")
        drafts: list[ChapterDraft] = []
        for d_dict in drafts_data:
            try:
                draft = ChapterDraft.from_dict(d_dict)
                drafts.append(draft)
            except (TypeError, KeyError) as exc:
                logger.error("反序列化 ChapterDraft 失败: %s", exc)
                continue

        logger.info(
            "从 CitationFormatter checkpoint 恢复: %d 章节",
            len(drafts),
        )
        return drafts, bib_content
