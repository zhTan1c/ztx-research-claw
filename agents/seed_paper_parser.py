"""
ztx-research-claw / agents / seed_paper_parser.py
解析用户手写的引用论文.md，输出 Paper 对象列表。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

from models import Paper

logger = logging.getLogger(__name__)


# arXiv ID 从 URL 中提取
_RE_ARXIV = re.compile(r"arxiv\.org/abs/(\d{4}\.\d{4,5})(v\d+)?")
# IEEE document ID
_RE_IEEE = re.compile(r"ieeexplore\.ieee\.org/document/(\d+)")
# DOI 从链接中提取
_RE_DOI = re.compile(r"doi\.org/(.+)")


def _extract_ids_from_url(url: str) -> dict:
    """从 URL 提取 arxiv_id / doi 等标识符。"""
    ids: dict[str, str] = {}
    m = _RE_ARXIV.search(url)
    if m:
        ids["arxiv_id"] = m.group(1)
    m = _RE_IEEE.search(url)
    if m:
        ids["ieee_id"] = m.group(1)
    m = _RE_DOI.search(url)
    if m:
        ids["doi"] = m.group(1)
    return ids


def _guess_year(detail: str) -> Optional[int]:
    """从 '详细' 字段中猜测年份。"""
    m = re.search(r"(19|20)\d{2}", detail)
    if m:
        return int(m.group())
    return None


def _guess_venue(detail: str) -> str:
    """从 '详细' 字段中提取会议/期刊名。"""
    # 去掉年份，剩下的大致就是 venue
    cleaned = re.sub(r"(19|20)\d{2}", "", detail).strip()
    cleaned = cleaned.strip(" /,;·")
    return cleaned


def _make_paper_id(url: str, title: str) -> str:
    """根据 URL 生成 paper_id。"""
    ids = _extract_ids_from_url(url)
    if "arxiv_id" in ids:
        return ids["arxiv_id"]
    if "doi" in ids:
        return ids["doi"]
    if "ieee_id" in ids:
        return f"IEEE_{ids['ieee_id']}"
    # Fallback: sanitized title
    return re.sub(r"[^a-zA-Z0-9]", "_", title)[:60]


def parse_seed_papers(md_path: str | Path) -> list[Paper]:
    """解析引用论文.md，返回已填充的 Paper 列表（不含待补充条目）。"""
    path = Path(md_path)
    if not path.is_file():
        logger.error("Seed paper file not found: %s", path)
        return []

    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    papers: list[Paper] = []
    current_chapter = ""
    current_section = ""

    # 状态机：逐行解析
    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # 章节标题
        if line.startswith("## 第") and "章" in line:
            m = re.search(r"第(\d+)章", line)
            if m:
                current_chapter = f"chapter_{m.group(1)}"
            i += 1
            continue

        # 子节标题
        if line.startswith("### "):
            current_section = line[4:].strip()
            i += 1
            continue

        # 论文条目
        if line.startswith("- **论文**:"):
            # 检查是否是待补充
            if "待补充" in line:
                # 跳过这个条目及其子字段
                i += 1
                while i < len(lines) and lines[i].strip().startswith("- **"):
                    i += 1
                continue

            # 提取标题: "- **论文**: [N] Title" 或 "- **论文**: Title"
            title_match = re.match(r"- \*\*论文\*\*:\s*(?:\[\d+\]\s*)?(.+)", line)
            if not title_match:
                i += 1
                continue
            title = title_match.group(1).strip()

            # 读取子字段
            detail = ""
            description = ""
            link = ""
            i += 1
            while i < len(lines) and lines[i].strip().startswith("- **"):
                sub = lines[i].strip()
                if "**详细**:" in sub:
                    detail = sub.split("**详细**:")[1].strip()
                elif "**简介**:" in sub:
                    description = sub.split("**简介**:")[1].strip()
                elif "**链接**:" in sub:
                    link = sub.split("**链接**:")[1].strip()
                i += 1

            # 构造 Paper
            ids = _extract_ids_from_url(link) if link else {}
            year = _guess_year(detail)
            venue = _guess_venue(detail)
            arxiv_id = ids.get("arxiv_id")
            doi = ids.get("doi")

            # 构造 PDF URL
            pdf_url = None
            if arxiv_id:
                pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"

            paper = Paper(
                paper_id=_make_paper_id(link, title),
                title=title,
                abstract=description,  # 用简介作为 abstract 的占位
                year=year,
                citation_count=0,  # 种子论文初始引用数为 0，后续用 S2 补全
                authors=[],
                venue=venue,
                doi=doi,
                arxiv_id=arxiv_id,
                pdf_url=pdf_url,
                source="seed",  # 标记为种子论文
            )
            papers.append(paper)
            logger.debug("Parsed seed paper: [%s] %s", paper.paper_id, title)
            continue

        i += 1

    logger.info("Parsed %d seed papers from %s", len(papers), path)
    return papers
