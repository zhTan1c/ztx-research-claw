"""
ztx-research-claw / models.py
数据结构定义 — 所有 Agent 共用的类型
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Optional


# ============================================================
# 1. 论文元数据
# ============================================================

@dataclass
class Paper:
    """一篇论文的完整元数据，在整个 pipeline 中流转。"""
    paper_id: str                    # 唯一标识 (S2 paperId / arXiv ID / DOI)
    title: str
    abstract: str = ""
    year: Optional[int] = None
    citation_count: int = 0
    influential_citation_count: int = 0
    authors: list[str] = field(default_factory=list)
    venue: str = ""
    doi: Optional[str] = None
    arxiv_id: Optional[str] = None
    # PDF 相关
    pdf_url: Optional[str] = None    # OA PDF 直链
    local_path: Optional[str] = None # 下载后的本地路径
    # 来源
    source: str = ""                 # "semantic_scholar" / "openalex" / "arxiv"
    # 开源项目
    has_code: bool = False           # 是否有开源代码仓库
    code_url: Optional[str] = None   # 代码仓库链接
    # 阅读笔记 (paper_reader 填充)
    reading_notes: Optional[str] = None
    # 初步引用元数据 (pdf_downloader 填充)
    preliminary_bib: Optional[str] = None

    @property
    def key(self) -> str:
        """生成 BibTeX key: 第一作者姓 + 年份，如 'Li2024'。"""
        if self.authors:
            last_name = self.authors[0].split()[-1] if self.authors[0] else "Unknown"
            # 只保留字母
            last_name = "".join(c for c in last_name if c.isalpha())
        else:
            last_name = "Unknown"
        year = self.year or 0
        return f"{last_name}{year}"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Paper":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ============================================================
# 2. 章节结构
# ============================================================

@dataclass
class Section:
    """大纲中的一个子节。"""
    section_id: str          # "1.1", "3.3.1"
    title: str               # "研究背景与意义"
    level: int               # heading level (2 = ##, 3 = ###)
    full_title: str = ""     # "## 1.1 研究背景与意义"


@dataclass
class Chapter:
    """大纲中的一个章节，包含若干子节。"""
    chapter_id: str          # "introduction", "deep_rl_method"
    chapter_num: int         # 1-7
    title: str               # "引言"
    full_title: str          # "# 第1章 引言"
    sections: list[Section] = field(default_factory=list)
    prompt_file: str = ""    # "prompts/introduction.txt"
    target_citations: int = 0  # 预期引用数
    # 写作输出 (writer 填充)
    content: str = ""
    citations_used: list[str] = field(default_factory=list)  # 使用的 paper_id 列表

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Chapter":
        sections = [Section(**s) for s in d.pop("sections", [])]
        return cls(sections=sections, **{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ============================================================
# 3. 检索结果
# ============================================================

@dataclass
class SearchResult:
    """单个检索源返回的结果集合。"""
    source: str              # "semantic_scholar" / "openalex" / "arxiv"
    query: str               # 原始查询
    papers: list[Paper] = field(default_factory=list)
    total_found: int = 0
    errors: list[str] = field(default_factory=list)


# ============================================================
# 4. 阅读笔记
# ============================================================

@dataclass
class ReadingNotes:
    """paper_reader 的输出：一篇论文的结构化阅读笔记。"""
    paper_id: str
    title: str
    # 提取的核心信息
    key_contributions: list[str] = field(default_factory=list)
    methodology_summary: str = ""
    experimental_results: str = ""
    limitations: str = ""
    relevance_to_survey: str = ""  # 与本综述主题的关联
    # 对应综述章节的标签
    chapter_tags: list[str] = field(default_factory=list)  # ["deep_rl_method", "sim_to_real"]
    raw_notes: str = ""            # 完整阅读笔记文本

    def to_dict(self) -> dict:
        return asdict(self)


# ============================================================
# 5. 方法演进分析
# ============================================================

@dataclass
class MethodEntry:
    """方法图谱中的一个条目。"""
    method_name: str
    category: str            # "non_rl" / "deep_rl" / "hybrid" / "foundation_model"
    subcategory: str = ""    # "actor_critic" / "mbrl" / "diffusion" etc.
    representative_papers: list[str] = field(default_factory=list)  # paper_id 列表
    key_technique: str = ""
    evolution_notes: str = ""  # 与前序方法的演进关系


@dataclass
class MethodAnalysis:
    """methodology_analyst 的输出：方法分类与演进图谱。"""
    taxonomy: list[MethodEntry] = field(default_factory=list)
    evolution_chains: list[str] = field(default_factory=list)  # 演进脉络描述
    chapter_mapping: dict[str, list[str]] = field(default_factory=dict)
    # chapter_id -> [method_name] 映射，供 writer 按章节组织内容
    raw_analysis: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ============================================================
# 6. 引用条目
# ============================================================

@dataclass
class Citation:
    """BibTeX 引用条目。"""
    key: str                 # BibTeX key, e.g., "Li2024"
    entry_type: str = "article"  # article / inproceedings / misc
    title: str = ""
    authors: str = ""        # "Li, Wei and Zhang, Hao"
    year: int = 0
    journal: str = ""        # 期刊名
    booktitle: str = ""      # 会议名
    volume: str = ""
    number: str = ""
    pages: str = ""
    doi: str = ""
    url: str = ""
    arxiv_id: str = ""
    abstract: str = ""

    def to_bibtex(self) -> str:
        """生成 BibTeX 格式字符串。"""
        fields = []
        if self.title:
            fields.append(f"  title = {{{self.title}}}")
        if self.authors:
            fields.append(f"  author = {{{self.authors}}}")
        if self.year:
            fields.append(f"  year = {{{self.year}}}")
        if self.journal:
            fields.append(f"  journal = {{{self.journal}}}")
        if self.booktitle:
            fields.append(f"  booktitle = {{{self.booktitle}}}")
        if self.volume:
            fields.append(f"  volume = {{{self.volume}}}")
        if self.number:
            fields.append(f"  number = {{{self.number}}}")
        if self.pages:
            fields.append(f"  pages = {{{self.pages}}}")
        if self.doi:
            fields.append(f"  doi = {{{self.doi}}}")
        if self.url:
            fields.append(f"  url = {{{self.url}}}")
        if self.arxiv_id:
            fields.append(f"  eprint = {{{self.arxiv_id}}}")
            fields.append(f"  archivePrefix = {{arXiv}}")

        fields_str = ",\n".join(fields)
        return f"@{self.entry_type}{{{self.key},\n{fields_str}\n}}"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Citation":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ============================================================
# 7. Agent 输出/Checkpoint
# ============================================================

@dataclass
class AgentCheckpoint:
    """Agent 断点续跑的 checkpoint 数据。"""
    agent_name: str
    status: str = "pending"   # pending / running / completed / failed
    phase: str = ""           # 当前执行到的阶段
    progress: float = 0.0     # 0.0 - 1.0
    data: dict = field(default_factory=dict)  # agent 特有的 checkpoint 数据
    error: Optional[str] = None
    timestamp: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, text: str) -> "AgentCheckpoint":
        d = json.loads(text)
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "AgentCheckpoint":
        p = Path(path)
        if not p.exists():
            return cls(agent_name=p.stem)
        text = p.read_text(encoding="utf-8").strip()
        if not text:
            return cls(agent_name=p.stem)
        return cls.from_json(text)


# ============================================================
# 8. 章节草稿 (writer 输出 + citation_formatter 输入)
# ============================================================

@dataclass
class ChapterDraft:
    """writer 生成的章节草稿，含占位引用标记。"""
    chapter_id: str
    title: str
    content: str                   # Markdown 正文，含 [cite:paper_id] 占位符
    citations: list[str] = field(default_factory=list)  # 引用的 paper_id 列表
    word_count: int = 0
    status: str = "draft"          # draft / cited / polished

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ChapterDraft":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
