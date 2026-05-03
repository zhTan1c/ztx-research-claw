"""
ztx-research-claw / agents / outline_parser.py
解析 data/outline.md 大纲文件，输出 Chapter/Section 结构。
纯解析，不涉及 LLM 调用。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from models import Chapter, Section, AgentCheckpoint

logger = logging.getLogger(__name__)

# ============================================================
# 常量映射
# ============================================================

CHAPTER_ID_MAP: dict[int, str] = {
    1: "introduction",
    2: "non_rl_method",
    3: "deep_rl_method",
    4: "mixed_and_SOTA_method",
    5: "experiment_and_performance",
    6: "challenges_and_trends",
    7: "conclusion",
}

PROMPT_FILE_MAP: dict[str, str] = {
    "introduction": "prompts/introduction.txt",
    "non_rl_method": "prompts/non_rl_method.txt",
    "deep_rl_method": "prompts/deep_rl_method.txt",
    "mixed_and_SOTA_method": "prompts/mixed_and_SOTA_method.txt",
    "experiment_and_performance": "prompts/experiment_and_performance.txt",
    "challenges_and_trends": "prompts/challenges_and_trends.txt",
    "conclusion": "prompts/conclusion.txt",
}

TARGET_CITATIONS_MAP: dict[str, int] = {
    "introduction": 10,
    "non_rl_method": 10,
    "deep_rl_method": 35,
    "mixed_and_SOTA_method": 35,
    "experiment_and_performance": 15,
    "challenges_and_trends": 10,
    "conclusion": 5,
}

# 正则：# 第N章 TITLE
_RE_CHAPTER = re.compile(r"^#\s+第(\d+)章\s+(.*)")
# 正则：## N.M TITLE  或  ### N.M.K TITLE
_RE_SECTION = re.compile(r"^##\s+(\d+(?:\.\d+)+)\s+(.*)")


class OutlineParser:
    """解析 Markdown 大纲文件，产出 Chapter/Section 数据结构。"""

    def __init__(self, config: dict) -> None:
        # 从 config['agents']['outline_parser'] 或直接传入子配置均可
        agent_cfg = config
        if "agents" in config and "outline_parser" in config["agents"]:
            agent_cfg = config["agents"]["outline_parser"]

        self.input_file: str = agent_cfg.get("input_file", "./data/outline.md")
        self.structure_validation: bool = agent_cfg.get("structure_validation", True)
        logger.info(
            "OutlineParser 初始化: input_file=%s, structure_validation=%s",
            self.input_file,
            self.structure_validation,
        )

    # ----------------------------------------------------------
    # 核心解析
    # ----------------------------------------------------------
    def run(self) -> list[Chapter]:
        """读取大纲 Markdown，解析并返回 Chapter 列表。"""
        path = Path(self.input_file)
        if not path.exists():
            logger.error("大纲文件不存在: %s", path)
            raise FileNotFoundError(f"大纲文件不存在: {path}")

        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()

        chapters: list[Chapter] = []
        current_chapter: Optional[Chapter] = None

        for lineno, raw_line in enumerate(lines, start=1):
            line = raw_line.strip()
            if not line:
                continue

            # --- 章标题 (# 第N章 ...) ---
            m_ch = _RE_CHAPTER.match(line)
            if m_ch:
                chapter_num = int(m_ch.group(1))
                title = m_ch.group(2).strip()
                full_title = line

                chapter_id = CHAPTER_ID_MAP.get(chapter_num)
                if chapter_id is None:
                    logger.warning(
                        "第 %d 行: 章号 %d 不在已知映射中，跳过: %s",
                        lineno,
                        chapter_num,
                        line,
                    )
                    current_chapter = None
                    continue

                current_chapter = Chapter(
                    chapter_id=chapter_id,
                    chapter_num=chapter_num,
                    title=title,
                    full_title=full_title,
                    sections=[],
                    prompt_file=PROMPT_FILE_MAP.get(chapter_id, ""),
                    target_citations=TARGET_CITATIONS_MAP.get(chapter_id, 0),
                )
                chapters.append(current_chapter)
                logger.debug("解析章节: 第%d章 -> %s", chapter_num, chapter_id)
                continue

            # --- 节标题 (## N.M ...) 或 子节标题 (### N.M.K ...) ---
            m_sec = _RE_SECTION.match(line)
            if m_sec:
                section_id = m_sec.group(1)
                title = m_sec.group(2).strip()
                full_title = line

                # 判断 level: "1.1" -> 2, "1.1.1" -> 3
                level = section_id.count(".") + 1

                section = Section(
                    section_id=section_id,
                    title=title,
                    level=level,
                    full_title=full_title,
                )

                if current_chapter is None:
                    logger.warning(
                        "第 %d 行: 节 '%s' 没有父章节，已忽略",
                        lineno,
                        section_id,
                    )
                    continue

                # 可选：校验 section_id 前缀是否与当前章节号匹配
                if self.structure_validation:
                    expected_prefix = str(current_chapter.chapter_num)
                    if not section_id.startswith(expected_prefix + "."):
                        logger.warning(
                            "第 %d 行: 节 '%s' 前缀与当前章节 %d 不匹配，"
                            "仍归属当前章节",
                            lineno,
                            section_id,
                            current_chapter.chapter_num,
                        )

                current_chapter.sections.append(section)
                logger.debug(
                    "  解析节: %s -> 归属 %s", section_id, current_chapter.chapter_id
                )
                continue

            # 其他行（空行已跳过，这里处理正文或注释等）忽略
            logger.debug("第 %d 行: 非标题行，跳过: %s", lineno, line[:60])

        logger.info("大纲解析完成: %d 个章节", len(chapters))
        for ch in chapters:
            logger.info(
                "  第%d章 [%s] %s (%d 个子节)",
                ch.chapter_num,
                ch.chapter_id,
                ch.title,
                len(ch.sections),
            )

        return chapters

    # ----------------------------------------------------------
    # Checkpoint 序列化
    # ----------------------------------------------------------
    def save_checkpoint(self, chapters: list[Chapter], path: str) -> None:
        """将解析结果保存为 JSON checkpoint 文件。"""
        checkpoint = AgentCheckpoint(
            agent_name="outline_parser",
            status="completed",
            phase="parse_outline",
            progress=1.0,
            data={
                "chapters": [ch.to_dict() for ch in chapters],
                "chapter_count": len(chapters),
                "total_sections": sum(len(ch.sections) for ch in chapters),
            },
            error=None,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(checkpoint.to_json(), encoding="utf-8")
        logger.info("Checkpoint 已保存: %s (%d 章节)", path, len(chapters))

    def load_checkpoint(self, path: str) -> Optional[list[Chapter]]:
        """从 JSON checkpoint 文件恢复 Chapter 列表；文件不存在则返回 None。"""
        p = Path(path)
        if not p.exists():
            logger.info("Checkpoint 文件不存在: %s", path)
            return None

        try:
            raw = p.read_text(encoding="utf-8")
            ckpt = AgentCheckpoint.from_json(raw)
        except (json.JSONDecodeError, TypeError, KeyError) as exc:
            logger.error("Checkpoint 文件损坏: %s — %s", path, exc)
            return None

        if ckpt.status != "completed":
            logger.warning("Checkpoint 状态为 '%s'，非 completed，忽略", ckpt.status)
            return None

        chapters_data = ckpt.data.get("chapters", [])
        if not chapters_data:
            logger.warning("Checkpoint 中无章节数据")
            return None

        chapters = []
        for ch_dict in chapters_data:
            try:
                ch = Chapter.from_dict(ch_dict)
                chapters.append(ch)
            except (TypeError, KeyError) as exc:
                logger.error("反序列化 Chapter 失败: %s", exc)
                continue

        logger.info(
            "从 Checkpoint 恢复: %d 章节, %d 子节",
            len(chapters),
            sum(len(ch.sections) for ch in chapters),
        )
        return chapters
