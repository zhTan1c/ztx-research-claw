"""
ztx-research-claw / agents / writer.py
Writer Agent — 使用 DeepSeek V4 Pro 撰写综述各章节。
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from openai import OpenAI

from models import (
    AgentCheckpoint,
    Chapter,
    ChapterDraft,
    MethodAnalysis,
    MethodEntry,
    ReadingNotes,
)

logger = logging.getLogger(__name__)

# 内容章节 ID 集合（跳过 outline_alignment 和 polish）
CONTENT_CHAPTER_IDS: set[str] = {
    "introduction",
    "non_rl_method",
    "deep_rl_method",
    "mixed_and_SOTA_method",
    "experiment_and_performance",
    "challenges_and_trends",
    "conclusion",
}


def _count_words(text: str) -> int:
    """计算混合中英文文本的字数：中文按字符计，英文按单词计。"""
    # 中文字符（含 CJK 标点）
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]", text))
    # 去除中文后按空格分词计数英文单词
    text_no_chinese = re.sub(r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]", " ", text)
    english_words = len(text_no_chinese.split())
    return chinese_chars + english_words


def _extract_citation_ids(content: str) -> list[str]:
    """从文本中提取所有 [cite:paper_id] 占位符的 paper_id。"""
    return list(set(re.findall(r"\[cite:([^\]]+)\]", content)))


class Writer:
    """Writer Agent：基于阅读笔记和方法分析，用 DeepSeek V4 Pro 撰写综述各章节。"""

    def __init__(self, config: dict) -> None:
        # 支持传入完整 config 或仅 writer 子配置
        if "agents" in config and "writer" in config["agents"]:
            agent_cfg = config["agents"]["writer"]
        else:
            agent_cfg = config

        # LLM 配置：从 config["llm"] 取 deepseek_v4_pro
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
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        logger.info(
            "Writer LLM 客户端初始化: model=%s, base_url=%s",
            self.model,
            base_url,
        )

        # Pipeline 配置
        self.pipeline: list[str] = agent_cfg.get("pipeline", [
            "outline_alignment",
            "introduction",
            "non_rl_method",
            "deep_rl_method",
            "mixed_and_SOTA_method",
            "experiment_and_performance",
            "challenges_and_trends",
            "conclusion",
            "polish",
        ])
        self.reasoning_effort_map: dict[str, str] = agent_cfg.get(
            "reasoning_effort_map", {}
        )

        # 提示词配置
        self.prompts_cfg: dict[str, str] = config.get("prompts", {})
        self.system_prompt_path: str = self.prompts_cfg.get(
            "system_prompt", "./prompts/system_prompt.txt"
        )

        # Checkpoint 配置
        ckpt_cfg = config.get("checkpoint", {})
        self.checkpoint_dir: str = ckpt_cfg.get("dir", "./outputs/checkpoints")
        self.checkpoint_file: str = "writer.json"
        self.checkpoint_path: str = str(
            Path(self.checkpoint_dir) / self.checkpoint_file
        )

        logger.info(
            "Writer 初始化完成: pipeline=%s, checkpoint=%s",
            self.pipeline,
            self.checkpoint_path,
        )

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def run(
        self,
        chapters: list[Chapter],
        reading_notes: list[ReadingNotes],
        method_analysis: MethodAnalysis,
    ) -> list[ChapterDraft]:
        """执行完整的写作流程，返回 ChapterDraft 列表。"""

        # 1. 加载 checkpoint，恢复已完成的章节
        completed_drafts, completed_ids = self._load_checkpoint()

        # 2. 读取 system_prompt
        system_prompt = self._read_file_safe(self.system_prompt_path)
        if not system_prompt:
            logger.error("无法读取 system_prompt: %s", self.system_prompt_path)
            raise FileNotFoundError(
                f"system_prompt 文件不存在或为空: {self.system_prompt_path}"
            )

        # 3. 按 pipeline 顺序撰写内容章节
        all_drafts: list[ChapterDraft] = list(completed_drafts)
        chapter_map = {ch.chapter_id: ch for ch in chapters}

        for stage in self.pipeline:
            if stage in ("outline_alignment", "polish"):
                continue  # 后续单独处理 polish

            if stage in completed_ids:
                logger.info("跳过已完成章节: %s", stage)
                continue

            if stage not in CONTENT_CHAPTER_IDS:
                logger.warning("未知的 pipeline stage，跳过: %s", stage)
                continue

            chapter = chapter_map.get(stage)
            if chapter is None:
                logger.warning("chapters 中未找到 chapter_id=%s，跳过", stage)
                continue

            # 读取章节专属提示词
            prompt_file = self._get_prompt_file(stage)
            chapter_prompt = self._read_file_safe(prompt_file)
            if not chapter_prompt:
                logger.error("章节提示词文件不存在或为空: %s", prompt_file)
                raise FileNotFoundError(f"章节提示词文件不存在或为空: {prompt_file}")

            # 按 chapter_tags 筛选相关 reading notes
            relevant_notes = [
                n for n in reading_notes if stage in n.chapter_tags
            ]
            logger.info(
                "章节 %s: 匹配到 %d 篇相关 reading notes",
                stage,
                len(relevant_notes),
            )

            # 撰写章节
            logger.info("开始撰写章节: %s", stage)
            try:
                draft = self._write_chapter(
                    chapter=chapter,
                    system_prompt=system_prompt,
                    chapter_prompt=chapter_prompt,
                    notes=relevant_notes,
                    method_analysis=method_analysis,
                )
                all_drafts.append(draft)
                completed_ids.add(stage)
                logger.info(
                    "章节 %s 撰写完成: %d 字, %d 条引用",
                    stage,
                    draft.word_count,
                    len(draft.citations),
                )
            except Exception as exc:
                logger.error("撰写章节 %s 失败: %s", stage, exc, exc_info=True)
                raise

            # 保存 checkpoint
            self._save_checkpoint(all_drafts)

        # 4. Polish pass：对所有草稿做一致性润色
        if "polish" in self.pipeline and "polish" not in completed_ids:
            logger.info("开始 polish 润色阶段")
            try:
                polished = self._polish_all(all_drafts, system_prompt)
                all_drafts = polished
                completed_ids.add("polish")
                logger.info("Polish 润色完成")
            except Exception as exc:
                logger.error("Polish 润色失败: %s", exc, exc_info=True)
                raise
            self._save_checkpoint(all_drafts)

        logger.info("Writer 全部完成: %d 个章节草稿", len(all_drafts))
        return all_drafts

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _write_chapter(
        self,
        chapter: Chapter,
        system_prompt: str,
        chapter_prompt: str,
        notes: list[ReadingNotes],
        method_analysis: MethodAnalysis,
    ) -> ChapterDraft:
        """撰写单个章节，返回 ChapterDraft。"""

        # 构建上下文信息
        context = self._build_chapter_context(
            chapter_id=chapter.chapter_id,
            notes=notes,
            method_analysis=method_analysis,
        )

        # 构建用户提示词
        user_content = self._assemble_user_prompt(
            chapter=chapter,
            chapter_prompt=chapter_prompt,
            context=context,
        )

        # 确定 max_tokens（核心章节给更多额度）
        reasoning_effort = self.reasoning_effort_map.get(chapter.chapter_id, "high")
        max_tokens = self.default_max_tokens
        if chapter.chapter_id in ("deep_rl_method", "mixed_and_SOTA_method"):
            max_tokens = 12288  # 核心章节给更多 token

        # 调用 LLM
        logger.info(
            "调用 LLM 撰写章节 %s (reasoning_effort=%s, max_tokens=%d)",
            chapter.chapter_id,
            reasoning_effort,
            max_tokens,
        )
        response_text = self._call_llm(
            system_prompt=system_prompt,
            user_content=user_content,
            max_tokens=max_tokens,
            temperature=self.default_temperature,
        )

        # 解析响应
        content = response_text.strip()
        citations = _extract_citation_ids(content)
        word_count = _count_words(content)

        draft = ChapterDraft(
            chapter_id=chapter.chapter_id,
            title=chapter.full_title,
            content=content,
            citations=citations,
            word_count=word_count,
            status="draft",
        )

        return draft

    def _build_chapter_context(
        self,
        chapter_id: str,
        notes: list[ReadingNotes],
        method_analysis: MethodAnalysis,
    ) -> str:
        """将与本章节相关的阅读笔记和方法分析格式化为上下文字符串。"""

        parts: list[str] = []

        # ---- 方法分析部分 ----
        # 演进脉络
        if method_analysis.evolution_chains:
            parts.append("【方法演进脉络】")
            for chain in method_analysis.evolution_chains:
                parts.append(f"- {chain}")
            parts.append("")

        # 本章节对应的方法分类
        mapped_methods = method_analysis.chapter_mapping.get(chapter_id, [])
        if mapped_methods:
            parts.append(f"【本章节 ({chapter_id}) 对应的方法分类】")
            for method_name in mapped_methods:
                # 找到对应的 MethodEntry
                entry = self._find_method_entry(method_analysis.taxonomy, method_name)
                if entry:
                    parts.append(
                        f"- {entry.method_name} [{entry.category}/{entry.subcategory}]"
                    )
                    if entry.key_technique:
                        parts.append(f"  核心技术: {entry.key_technique}")
                    if entry.evolution_notes:
                        parts.append(f"  演进关系: {entry.evolution_notes}")
                    if entry.representative_papers:
                        parts.append(
                            f"  代表论文: {', '.join(entry.representative_papers)}"
                        )
                else:
                    parts.append(f"- {method_name}")
            parts.append("")

        # 原始分析文本（截取相关部分，限制长度）
        if method_analysis.raw_analysis:
            raw = method_analysis.raw_analysis
            if len(raw) > 6000:
                raw = raw[:6000] + "\n...(截断)"
            parts.append("【方法分析原始文本（节选）】")
            parts.append(raw)
            parts.append("")

        # ---- 阅读笔记部分 ----
        if notes:
            parts.append(f"【相关论文阅读笔记 (共 {len(notes)} 篇)】")
            for i, note in enumerate(notes, 1):
                parts.append(f"\n--- 论文 {i}: {note.title} (ID: {note.paper_id}) ---")
                if note.key_contributions:
                    parts.append("主要贡献:")
                    for contrib in note.key_contributions:
                        parts.append(f"  • {contrib}")
                if note.methodology_summary:
                    parts.append(f"方法概述: {note.methodology_summary}")
                if note.experimental_results:
                    parts.append(f"实验结果: {note.experimental_results}")
                if note.limitations:
                    parts.append(f"局限性: {note.limitations}")
                if note.relevance_to_survey:
                    parts.append(f"与综述关联: {note.relevance_to_survey}")
                # 如果有完整原始笔记，也一并提供（限制长度）
                if note.raw_notes and len(note.raw_notes) > 200:
                    raw_notes_trimmed = note.raw_notes[:3000]
                    if len(note.raw_notes) > 3000:
                        raw_notes_trimmed += "\n...(截断)"
                    parts.append(f"原始笔记:\n{raw_notes_trimmed}")
            parts.append("")

        return "\n".join(parts)

    def _assemble_user_prompt(
        self,
        chapter: Chapter,
        chapter_prompt: str,
        context: str,
    ) -> str:
        """组装发送给 LLM 的用户消息。"""

        sections_desc = ""
        if chapter.sections:
            section_lines = []
            for sec in chapter.sections:
                indent = "  " * (sec.level - 2)
                section_lines.append(f"{indent}{sec.full_title}")
            sections_desc = "\n".join(section_lines)

        parts = [
            f"【当前章节】{chapter.full_title}",
            f"【章节 ID】{chapter.chapter_id}",
            f"【目标引用数】约 {chapter.target_citations} 篇",
        ]

        if sections_desc:
            parts.append(f"【章节大纲结构】\n{sections_desc}")

        parts.append(f"\n{chapter_prompt}")

        parts.append(
            "\n【引用规范】"
            "在正文中需要引用论文的位置插入 [cite:paper_id] 占位符，"
            "paper_id 取自下方阅读笔记中的论文 ID。"
            "确保每个论点都有对应的引用支撑，避免无论点的文献罗列。"
        )

        if context:
            parts.append(f"\n{'='*60}\n【参考资料】\n{context}")

        parts.append(
            "\n请根据以上信息撰写本章节的完整正文。"
            "输出纯 Markdown 格式，章节标题用 ##，表格用 Markdown 表格语法。"
            "确保引用占位符 [cite:paper_id] 格式正确。"
        )

        return "\n\n".join(parts)

    def _polish_all(
        self,
        drafts: list[ChapterDraft],
        system_prompt: str,
    ) -> list[ChapterDraft]:
        """最终润色：通读全部草稿，修复一致性问题并润色。"""

        # 组装所有草稿内容
        all_content_parts = []
        for draft in sorted(drafts, key=lambda d: d.chapter_id):
            all_content_parts.append(
                f"=== {draft.title} ({draft.chapter_id}) ===\n{draft.content}"
            )
        all_content = "\n\n".join(all_content_parts)

        polish_prompt = (
            "你正在对一篇完整的学术综述进行最终润色。以下是全部 7 个章节的草稿内容。\n\n"
            "【润色任务】\n"
            "1. 检查各章节之间的逻辑衔接，确保前一章的结尾能自然过渡到下一章的开头。\n"
            "2. 统一术语使用，确保同一概念在全文中的表述一致。\n"
            "3. 检查并修正重复论述（不同章节对同一方法的描述不应矛盾）。\n"
            "4. 保持 [cite:paper_id] 占位符不变，不要删除或修改它们。\n"
            "5. 保持各章节的 Markdown 格式和标题结构不变。\n"
            "6. 修正明显的语法错误或表述不清的句子。\n\n"
            "【输出格式】\n"
            "请按章节分别输出润色后的内容，每个章节用以下分隔标记包裹：\n"
            "---BEGIN CHAPTER: chapter_id---\n"
            "(润色后的章节内容)\n"
            "---END CHAPTER: chapter_id---\n\n"
            f"以下是全部草稿内容：\n\n{all_content}"
        )

        # 如果全文过长，截断并警告
        if len(polish_prompt) > 120000:
            logger.warning(
                "Polish 提示词长度 %d 超过限制，将截断", len(polish_prompt)
            )
            # 截断中间部分，保留首尾
            polish_prompt = (
                polish_prompt[:60000]
                + "\n\n...(中间部分内容过多已截断)...\n\n"
                + polish_prompt[-60000:]
            )

        response = self._call_llm(
            system_prompt=system_prompt,
            user_content=polish_prompt,
            max_tokens=self.default_max_tokens,
            temperature=0.4,  # 润色时降低温度，减少改动幅度
        )

        # 解析润色结果
        polished_map = self._parse_polish_response(response)

        # 将润色内容合并回 drafts
        result: list[ChapterDraft] = []
        for draft in drafts:
            if draft.chapter_id in polished_map:
                new_content = polished_map[draft.chapter_id]
                citations = _extract_citation_ids(new_content)
                word_count = _count_words(new_content)
                polished_draft = ChapterDraft(
                    chapter_id=draft.chapter_id,
                    title=draft.title,
                    content=new_content,
                    citations=citations,
                    word_count=word_count,
                    status="polished",
                )
                result.append(polished_draft)
                logger.info(
                    "章节 %s 润色更新: %d 字 -> %d 字",
                    draft.chapter_id,
                    draft.word_count,
                    word_count,
                )
            else:
                # 未在润色结果中找到，保留原样
                logger.warning(
                    "章节 %s 未在润色结果中找到，保留原草稿", draft.chapter_id
                )
                result.append(draft)

        return result

    def _parse_polish_response(self, response: str) -> dict[str, str]:
        """解析 polish LLM 返回的分章节内容。"""
        pattern = re.compile(
            r"---BEGIN CHAPTER:\s*(\w+)---\s*\n(.*?)\n\s*---END CHAPTER:\s*\1---",
            re.DOTALL,
        )
        result: dict[str, str] = {}
        for match in pattern.finditer(response):
            chapter_id = match.group(1).strip()
            content = match.group(2).strip()
            result[chapter_id] = content
        return result

    def _call_llm(
        self,
        system_prompt: str,
        user_content: str,
        max_tokens: int = 8192,
        temperature: float = 0.6,
    ) -> str:
        """调用 DeepSeek LLM，返回生成的文本。带错误处理和重试。"""

        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                logger.debug(
                    "LLM 调用 attempt %d/%d: system=%d chars, user=%d chars, "
                    "max_tokens=%d, temp=%.2f",
                    attempt,
                    max_retries,
                    len(system_prompt),
                    len(user_content),
                    max_tokens,
                    temperature,
                )
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content},
                    ],
                    max_tokens=max_tokens,
                    temperature=temperature,
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
    # 辅助方法
    # ------------------------------------------------------------------

    def _get_prompt_file(self, chapter_id: str) -> str:
        """获取章节对应的提示词文件路径。"""
        # 优先从 prompts 配置中查找
        path = self.prompts_cfg.get(chapter_id, "")
        if path:
            return path
        # 兜底默认路径
        return f"./prompts/{chapter_id}.txt"

    def _read_file_safe(self, path: str) -> str:
        """安全读取文件内容，文件不存在或为空时返回空字符串。"""
        p = Path(path)
        if not p.exists():
            logger.warning("文件不存在: %s", path)
            return ""
        try:
            content = p.read_text(encoding="utf-8").strip()
            if not content:
                logger.warning("文件内容为空: %s", path)
            return content
        except Exception as exc:
            logger.error("读取文件失败: %s — %s", path, exc)
            return ""

    @staticmethod
    def _find_method_entry(
        taxonomy: list[MethodEntry], method_name: str
    ) -> Optional[MethodEntry]:
        """在 taxonomy 中按 method_name 查找 MethodEntry。"""
        for entry in taxonomy:
            if entry.method_name == method_name:
                return entry
        return None

    # ------------------------------------------------------------------
    # Checkpoint
    # ------------------------------------------------------------------

    def _save_checkpoint(self, drafts: list[ChapterDraft]) -> None:
        """保存 writer checkpoint。"""
        checkpoint = AgentCheckpoint(
            agent_name="writer",
            status="running",
            phase="writing",
            progress=len([d for d in drafts if d.status != "pending"])
            / max(len(drafts), 1),
            data={
                "drafts": [d.to_dict() for d in drafts],
                "completed_chapters": [
                    d.chapter_id for d in drafts
                ],
                "draft_count": len(drafts),
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
                "Writer checkpoint 已保存: %s (%d 章节)",
                self.checkpoint_path,
                len(drafts),
            )
        except Exception as exc:
            logger.error("保存 checkpoint 失败: %s", exc)

    def _load_checkpoint(
        self,
    ) -> tuple[list[ChapterDraft], set[str]]:
        """加载 writer checkpoint，返回 (已完成的 drafts, 已完成的 chapter_ids)。"""
        p = Path(self.checkpoint_path)
        if not p.exists():
            logger.info("Writer checkpoint 文件不存在，从头开始")
            return [], set()

        try:
            raw = p.read_text(encoding="utf-8")
            ckpt = AgentCheckpoint.from_json(raw)
        except (json.JSONDecodeError, TypeError, KeyError) as exc:
            logger.error("Writer checkpoint 文件损坏: %s — %s", self.checkpoint_path, exc)
            return [], set()

        if ckpt.status not in ("running", "completed"):
            logger.warning("Writer checkpoint 状态为 '%s'，忽略", ckpt.status)
            return [], set()

        drafts_data = ckpt.data.get("drafts", [])
        drafts: list[ChapterDraft] = []
        for d_dict in drafts_data:
            try:
                draft = ChapterDraft.from_dict(d_dict)
                drafts.append(draft)
            except (TypeError, KeyError) as exc:
                logger.error("反序列化 ChapterDraft 失败: %s", exc)
                continue

        completed_ids = {d.chapter_id for d in drafts}
        logger.info(
            "从 Writer checkpoint 恢复: %d 章节已完成 (%s)",
            len(drafts),
            ", ".join(sorted(completed_ids)) if completed_ids else "无",
        )
        return drafts, completed_ids
