"""
ztx-research-claw / agents / literature_searcher.py
LiteratureSearcher — 多源论文检索 + 种子注入 + 综述过滤
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

import httpx
from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from models import AgentCheckpoint, Chapter, Paper
from agents.seed_paper_parser import parse_seed_papers

logger = logging.getLogger(__name__)

# 综述类论文标题关键词
SURVEY_KEYWORDS = [
    "survey", "review", "overview", "progress in", "summary",
    "introduction to", "synthesis", "state of the art", "taxonomy",
    "comprehensive", "systematic review", "meta-analysis",
]

# 代码仓库关键词
CODE_KEYWORDS = [
    "github.com", "gitlab.com", "huggingface.co", "bitbucket.org",
    "code.google.com", "sourceforge.net", "colab.research.google.com",
]


def _is_survey(title: str) -> bool:
    """判断论文标题是否为综述类。"""
    t = title.lower()
    return any(kw in t for kw in SURVEY_KEYWORDS)


def _has_code_link(text: str) -> str | None:
    """从文本中提取代码仓库链接。"""
    for kw in CODE_KEYWORDS:
        if kw in text.lower():
            # 提取 URL
            import re
            m = re.search(rf"https?://[^\s)>\]]*{re.escape(kw)}[^\s)>\]]*", text, re.IGNORECASE)
            if m:
                return m.group(0)
    return None


def _parse_citation_requirement(prompt_file: str) -> tuple[int, int]:
    """从 prompt 文件解析引用要求。

    返回 (target_count, max_surveys)。
    格式示例：
      "引用要求：10篇，允许推荐综述类论文。" → (10, 10)
      "引用要求：35～45篇，最多引用3篇综述类论文。" → (40, 3)
      "引用要求：15～20篇，最多引用1篇综述类论文。" → (17, 1)
    """
    import re
    try:
        text = Path(prompt_file).read_text(encoding="utf-8")
    except Exception:
        return (10, 0)

    target, max_surveys = 10, 0

    # 解析目标引用数
    m = re.search(r"引用要求[：:]\s*(\d+)(?:[～~\-](\d+))?", text)
    if m:
        low = int(m.group(1))
        high = int(m.group(2)) if m.group(2) else low
        target = (low + high) // 2

    # 解析综述限制
    if "允许" in text and "综述" in text:
        max_surveys = target  # 允许全部用综述
    else:
        m2 = re.search(r"最多引用(\d+)篇综述", text)
        if m2:
            max_surveys = int(m2.group(1))
        else:
            max_surveys = 0  # 不允许综述

    return target, max_surveys


class LiteratureSearcher:
    """多源论文检索 Agent。"""

    def __init__(self, config: dict) -> None:
        self.config = config
        self.search_cfg = config.get("search", {})
        self.agent_cfg = config.get("agents", {}).get("literature_searcher", {})
        self.network_cfg = config.get("network", {})

        self.pipeline = self.agent_cfg.get("search_pipeline", ["semantic_scholar", "openalex", "arxiv"])
        self.dedup_threshold = self.agent_cfg.get("dedup_threshold", 0.85)
        self.max_total_papers = self.agent_cfg.get("max_total_papers", 200)
        self.timeout = self.network_cfg.get("timeout", 30)

        # Scoring weights
        scoring = self.agent_cfg.get("relevance_scoring", {})
        self.w_cite = scoring.get("citation_weight", 0.3)
        self.w_recency = scoring.get("recency_weight", 0.2)
        self.w_relevance = scoring.get("abstract_match_weight", 0.5)

        # LLM client
        llm_cfg = config.get("llm", {}).get("mimo_v25_pro", {})
        self.llm = OpenAI(
            base_url=llm_cfg.get("base_url", ""),
            api_key=llm_cfg.get("api_key", ""),
            http_client=httpx.Client(trust_env=True),
        )
        self.llm_model = llm_cfg.get("model", "mimo-v2.5-pro")

        # Rate-limit semaphores
        self._sems: dict[str, asyncio.Semaphore] = {}
        for name, cfg in self.search_cfg.items():
            if isinstance(cfg, dict) and cfg.get("enabled", True):
                self._sems[name] = asyncio.Semaphore(cfg.get("rate_limit", 10))

    # ──────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────

    async def run(
        self,
        chapters: list[Chapter],
        seed_papers_file: str | None = None,
    ) -> list[Paper]:
        """主入口：种子注入 → LLM 推荐查询 → 多源检索 → 去重过滤评分。"""

        # 1. 加载 checkpoint
        ckpt = self._load_checkpoint()
        done_queries: set[str] = set(ckpt.data.get("completed_queries", []))
        all_papers = [Paper.from_dict(d) for d in ckpt.data.get("papers", [])]

        # 2. 种子论文注入
        if seed_papers_file:
            seeds = parse_seed_papers(seed_papers_file)
            if seeds:
                logger.info("Loaded %d seed papers", len(seeds))
                enriched = await self._enrich_seeds(seeds)
                all_papers.extend(enriched)

                # Citation expansion（仅对 enrichment 成功的种子）
                s2_seeds = [s for s in enriched if s.source == "seed_enriched"]
                if s2_seeds:
                    expanded = await self._expand_citations(s2_seeds)
                    all_papers.extend(expanded)
                    logger.info("Citation expansion: %d papers from %d seeds", len(expanded), len(s2_seeds))

        # 3. 为每章生成搜索查询（从 prompt 解析引用要求）
        chapter_queries: list[tuple[str, str, bool]] = []  # (query, chapter_id, allow_survey)
        for ch in chapters:
            # 从 prompt 文件解析引用要求
            target, max_surveys = _parse_citation_requirement(ch.prompt_file)
            ch.target_citations = target  # 更新目标引用数
            allow_survey = max_surveys > 0
            queries = await self._generate_queries(ch, allow_survey, max_surveys)
            for q in queries:
                chapter_queries.append((q, ch.chapter_id, allow_survey))
            logger.info("Chapter %s: target=%d, max_surveys=%d, queries=%d",
                        ch.chapter_id, target, max_surveys, len(queries))

        # 4. 执行搜索
        skipped: set[str] = set()
        total_q = len(chapter_queries)
        for i, (query, ch_id, _) in enumerate(chapter_queries):
            if query in done_queries:
                continue

            for src in self.pipeline:
                if src in skipped:
                    continue
                src_cfg = self.search_cfg.get(src, {})
                if not src_cfg.get("enabled", True):
                    continue
                try:
                    papers = await self._search(src, query, src_cfg)
                    all_papers.extend(papers)
                    logger.info("  [%s] '%s' => %d", src, query[:50], len(papers))
                except Exception:
                    skipped.add(src)
                    logger.warning("  [%s] failed, skipping remaining", src)

            done_queries.add(query)
            if (i + 1) % 5 == 0:
                self._save_ckpt(done_queries, all_papers)
                logger.info("Progress: %d/%d queries (%d papers, skipped: %s)",
                            i + 1, total_q, len(all_papers), ",".join(skipped) or "none")

        # 5. 后处理流水线
        papers = self._pre_filter(all_papers)
        papers = self._deduplicate(papers)
        papers = self._enrich_pdf_links(papers)
        papers = self._filter_relevance(papers)
        papers = self._filter_citations(papers)

        # 6. 按章节分组过滤综述 + 评分排序
        papers = self._filter_surveys_and_score(papers, chapters)

        logger.info("Final: %d papers", len(papers))
        self._save_ckpt(done_queries, papers, status="completed")
        return papers

    # ──────────────────────────────────────────────────────────────
    # LLM 查询生成
    # ──────────────────────────────────────────────────────────────

    async def _generate_queries(self, chapter: Chapter, allow_survey: bool, max_surveys: int = 0) -> list[str]:
        """调用 LLM 推荐该章应引用的论文标题。"""

        prompt_file = chapter.prompt_file
        prompt_content = ""
        if prompt_file and Path(prompt_file).exists():
            prompt_content = Path(prompt_file).read_text(encoding="utf-8")

        if allow_survey:
            survey_hint = f"\n\n本章最多引用 {max_surveys} 篇综述类论文，其余必须是具体方法/实验论文。"
        else:
            survey_hint = "\n\n【重要】本章只引用实验创新类论文，不要推荐任何综述类论文（标题含 survey/review/overview 的不要推荐）。"

        system_msg = (
            "你是学术论文推荐专家。根据综述章节的写作要求，推荐应引用的真实论文。\n"
            "要求：1) 论文必须真实存在 2) 优先高引用经典论文 3) 包含近3年新进展\n"
            "4) 每行一篇论文英文标题 5) 不要编造论文" + survey_hint
        )

        user_msg = (
            f"综述第 {chapter.chapter_num} 章《{chapter.title}》的写作要求：\n\n"
            f"{prompt_content}\n\n"
            f"请推荐 {chapter.target_citations} 篇论文标题，每行一篇。"
        )

        try:
            resp = self.llm.chat.completions.create(
                model=self.llm_model,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_msg},
                ],
                max_tokens=4000,
                temperature=0.7,
            )
            content = resp.choices[0].message.content or ""
            titles = []
            for line in content.strip().split("\n"):
                line = re.sub(r"^[\d]+[\.\)]\s*", "", line.strip())
                line = re.sub(r"^[-*]\s*", "", line)
                if len(line) > 10:
                    titles.append(line)

                    # 额外检查：如果章节不允许综述，过滤掉 LLM 推荐的综述
                    if not allow_survey and _is_survey(line):
                        titles.pop()
                        logger.debug("Filtered survey from LLM: %s", line[:50])

            logger.info("LLM recommended %d papers for %s", len(titles), chapter.chapter_id)
            return titles or ["deformable object grasping", "dexterous manipulation"]
        except Exception as exc:
            logger.error("LLM query failed: %s", exc)
            return ["deformable object grasping", "dexterous manipulation"]

    # ──────────────────────────────────────────────────────────────
    # 搜索分发
    # ──────────────────────────────────────────────────────────────

    async def _search(self, source: str, query: str, cfg: dict) -> list[Paper]:
        dispatch = {
            "semantic_scholar": self._search_s2,
            "openalex": self._search_oa,
            "arxiv": self._search_ax,
        }
        handler = dispatch.get(source)
        if not handler:
            return []
        return await handler(query, cfg)

    # ── Semantic Scholar ──

    @retry(stop=stop_after_attempt(1), wait=wait_exponential(multiplier=1, min=1, max=2))
    async def _search_s2(self, query: str, cfg: dict) -> list[Paper]:
        base_url = cfg.get("base_url", "https://api.semanticscholar.org/graph/v1")
        params = {
            "query": query,
            "fields": "paperId,title,abstract,year,citationCount,authors,venue,openAccessPdf,externalIds,influentialCitationCount",
            "limit": cfg.get("max_results_per_query", 100),
        }
        fos = cfg.get("fields_of_study", [])
        if fos:
            params["fieldsOfStudy"] = ",".join(fos)

        async with httpx.AsyncClient(timeout=self.timeout, trust_env=True) as client:
            resp = await client.get(f"{base_url}/paper/search", params=params)
            resp.raise_for_status()
            data = resp.json()

        return [self._parse_s2_paper(item) for item in data.get("data", []) if item.get("title")]

    def _parse_s2_paper(self, item: dict) -> Paper:
        ext = item.get("externalIds") or {}
        oa = item.get("openAccessPdf")
        abstract = item.get("abstract") or ""
        code_url = _has_code_link(abstract)
        return Paper(
            paper_id=item.get("paperId", ""),
            title=item.get("title", ""),
            abstract=abstract,
            year=item.get("year"),
            citation_count=item.get("citationCount", 0) or 0,
            influential_citation_count=item.get("influentialCitationCount", 0) or 0,
            authors=[a.get("name", "") for a in item.get("authors", [])],
            venue=item.get("venue") or "",
            doi=ext.get("DOI"),
            arxiv_id=ext.get("ArXiv"),
            pdf_url=oa.get("url") if oa and isinstance(oa, dict) else None,
            source="semantic_scholar",
            has_code=bool(code_url),
            code_url=code_url,
        )

    # ── OpenAlex ──

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def _search_oa(self, query: str, cfg: dict) -> list[Paper]:
        base_url = cfg.get("base_url", "https://api.openalex.org")
        params = {"search": query, "per_page": cfg.get("max_results_per_query", 100)}
        mailto = cfg.get("mailto", "")
        if mailto:
            params["mailto"] = mailto
        topics = cfg.get("filter_topics", [])
        if topics:
            params["filter"] = f"concepts.id:{'|'.join(topics)}"

        async with httpx.AsyncClient(timeout=self.timeout, trust_env=True) as client:
            resp = await client.get(f"{base_url}/works", params=params)
            resp.raise_for_status()
            data = resp.json()

        return [self._parse_oa_paper(item) for item in data.get("results", []) if item.get("title")]

    def _parse_oa_paper(self, item: dict) -> Paper:
        authors = [a.get("author", {}).get("display_name", "") for a in item.get("authorships", []) if a.get("author")]
        oa = item.get("open_access", {})
        doi_raw = item.get("doi", "") or ""
        arxiv_id = None
        code_url = None
        for loc in item.get("locations", []):
            landing = loc.get("landing_page_url", "") or ""
            if "arxiv.org" in landing:
                m = re.search(r"arxiv\.org/abs/(\d+\.\d+)", landing)
                if m:
                    arxiv_id = m.group(1)
            if not code_url:
                code_url = _has_code_link(landing)
        year = None
        try:
            year = int(item.get("publication_year"))
        except (TypeError, ValueError):
            pass

        abstract = self._oa_abstract(item.get("abstract_inverted_index"))
        if not code_url:
            code_url = _has_code_link(abstract)

        return Paper(
            paper_id=item.get("id", ""),
            title=item.get("title") or "",
            abstract=abstract,
            year=year,
            citation_count=item.get("cited_by_count", 0),
            authors=authors,
            venue=item.get("primary_location", {}).get("source", {}).get("display_name", "") if item.get("primary_location", {}).get("source") else "",
            doi=doi_raw.replace("https://doi.org/", "") if doi_raw else None,
            arxiv_id=arxiv_id,
            pdf_url=oa.get("oa_url") if oa else None,
            source="openalex",
            has_code=bool(code_url),
            code_url=code_url,
        )

    @staticmethod
    def _oa_abstract(inv_idx: dict | None) -> str:
        if not inv_idx:
            return ""
        pairs = [(pos, word) for word, positions in inv_idx.items() for pos in positions]
        pairs.sort()
        return " ".join(w for _, w in pairs)

    # ── arXiv ──

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def _search_ax(self, query: str, cfg: dict) -> list[Paper]:
        base_url = cfg.get("base_url", "https://export.arxiv.org/api/query")
        cats = cfg.get("categories", ["cs.RO"])
        cat_filter = "+OR+".join(f"cat:{c}" for c in cats)

        params = {
            "search_query": f"all:{query}+AND+{cat_filter}",
            "max_results": cfg.get("max_results_per_query", 100),
            "sortBy": cfg.get("sort_by", "submittedDate"),
            "sortOrder": cfg.get("sort_order", "descending"),
        }

        async with httpx.AsyncClient(timeout=self.timeout, trust_env=True) as client:
            resp = await client.get(base_url, params=params)
            resp.raise_for_status()

        ns = "{http://www.w3.org/2005/Atom}"
        try:
            root = ET.fromstring(resp.text)
        except ET.ParseError as exc:
            logger.error("arXiv XML parse error: %s", exc)
            return []

        papers = []
        for entry in root.findall(f"{ns}entry"):
            title = self._ax_text(entry, f"{ns}title")
            if not title:
                continue

            arxiv_id, pdf_url = None, None
            for link in entry.findall(f"{ns}link"):
                href = link.get("href", "")
                if link.get("title") == "pdf" or link.get("type") == "application/pdf":
                    pdf_url = href
                if "/abs/" in href:
                    m = re.search(r"(\d{4}\.\d{4,5})(v\d+)?$", href)
                    if m:
                        arxiv_id = m.group(1)
            if not arxiv_id:
                id_el = entry.find(f"{ns}id")
                if id_el is not None and id_el.text:
                    m = re.search(r"(\d{4}\.\d{4,5})(v\d+)?$", id_el.text)
                    if m:
                        arxiv_id = m.group(1)

            doi_el = entry.find("{http://arxiv.org/schemas/atom}doi")
            year = None
            pub = entry.find(f"{ns}published")
            if pub is not None and pub.text:
                try:
                    year = int(pub.text[:4])
                except (ValueError, TypeError):
                    pass

            papers.append(Paper(
                paper_id=arxiv_id or title,
                title=title,
                abstract=self._ax_text(entry, f"{ns}summary"),
                year=year,
                citation_count=0,
                authors=[a.find(f"{ns}name").text.strip() for a in entry.findall(f"{ns}author") if a.find(f"{ns}name") is not None and a.find(f"{ns}name").text],
                venue="arXiv",
                doi=doi_el.text.strip() if doi_el is not None and doi_el.text else None,
                arxiv_id=arxiv_id,
                pdf_url=pdf_url,
                source="arxiv",
            ))
        return papers

    @staticmethod
    def _ax_text(parent, tag: str) -> str:
        el = parent.find(tag)
        return el.text.strip().replace("\n", " ") if el is not None and el.text else ""

    # ──────────────────────────────────────────────────────────────
    # 种子论文 enrichment
    # ──────────────────────────────────────────────────────────────

    async def _enrich_seeds(self, seeds: list[Paper]) -> list[Paper]:
        """用 S2 补全种子论文元数据。429 快速失败。"""
        s2_cfg = self.search_cfg.get("semantic_scholar", {})
        base_url = s2_cfg.get("base_url", "https://api.semanticscholar.org/graph/v1")
        fields = "paperId,title,abstract,year,citationCount,authors,venue,openAccessPdf,externalIds"

        enriched = []
        consecutive_429 = 0

        async with httpx.AsyncClient(timeout=self.timeout, trust_env=True) as client:
            for i, seed in enumerate(seeds):
                if consecutive_429 >= 5:
                    enriched.extend(seeds[i:])
                    break

                data = None
                if seed.arxiv_id:
                    try:
                        resp = await client.get(f"{base_url}/paper/ARXIV:{seed.arxiv_id}", params={"fields": fields})
                        if resp.status_code == 200:
                            data = resp.json()
                            consecutive_429 = 0
                        elif resp.status_code == 429:
                            consecutive_429 += 1
                            await asyncio.sleep(2)
                    except Exception:
                        pass

                if data:
                    seed.paper_id = data.get("paperId", seed.paper_id)
                    seed.abstract = data.get("abstract") or seed.abstract
                    seed.citation_count = data.get("citationCount", 0) or 0
                    seed.authors = [a.get("name", "") for a in data.get("authors", [])]
                    seed.venue = data.get("venue") or seed.venue
                    seed.year = data.get("year") or seed.year
                    ext = data.get("externalIds") or {}
                    if not seed.doi and ext.get("DOI"):
                        seed.doi = ext["DOI"]
                    oa = data.get("openAccessPdf")
                    if oa and isinstance(oa, dict) and oa.get("url"):
                        seed.pdf_url = oa["url"]
                    seed.source = "seed_enriched"

                enriched.append(seed)
                if consecutive_429 == 0 and i < len(seeds) - 1:
                    await asyncio.sleep(0.5)

        logger.info("Enrichment: %d/%d enriched", sum(1 for s in enriched if s.source == "seed_enriched"), len(seeds))
        return enriched

    # ──────────────────────────────────────────────────────────────
    # Citation expansion
    # ──────────────────────────────────────────────────────────────

    async def _expand_citations(self, seeds: list[Paper]) -> list[Paper]:
        """从种子论文出发，获取 references + citations。"""
        s2_cfg = self.search_cfg.get("semantic_scholar", {})
        base_url = s2_cfg.get("base_url", "https://api.semanticscholar.org/graph/v1")
        fields = "paperId,title,abstract,year,citationCount,authors,venue,openAccessPdf,externalIds"

        expanded = []
        visited = set()

        async with httpx.AsyncClient(timeout=self.timeout, trust_env=True) as client:
            for seed in seeds:
                if seed.paper_id in visited:
                    continue
                visited.add(seed.paper_id)

                for relation in ("references", "citations"):
                    papers = await self._fetch_relation(client, seed.paper_id, relation, base_url, fields)
                    expanded.extend(papers)
                await asyncio.sleep(0.2)

        logger.info("Citation expansion: %d raw papers", len(expanded))
        return expanded

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=1, max=3))
    async def _fetch_relation(self, client, paper_id, relation, base_url, fields) -> list[Paper]:
        resp = await client.get(f"{base_url}/paper/{paper_id}/{relation}", params={"fields": fields, "limit": 100})
        if resp.status_code != 200:
            if resp.status_code == 429:
                raise Exception("Rate limited")
            return []

        papers = []
        for item in resp.json().get("data", []):
            p = item.get("citedPaper") or item.get("paper") or item
            if not p or not p.get("paperId") or not p.get("title"):
                continue
            ext = p.get("externalIds") or {}
            oa = p.get("openAccessPdf")
            papers.append(Paper(
                paper_id=p["paperId"],
                title=p["title"],
                abstract=p.get("abstract") or "",
                year=p.get("year"),
                citation_count=p.get("citationCount", 0) or 0,
                authors=[a.get("name", "") for a in p.get("authors", [])],
                venue=p.get("venue") or "",
                doi=ext.get("DOI"),
                arxiv_id=ext.get("ArXiv"),
                pdf_url=oa.get("url") if oa and isinstance(oa, dict) else None,
                source=f"s2_{relation}",
            ))
        return papers

    # ──────────────────────────────────────────────────────────────
    # 后处理流水线
    # ──────────────────────────────────────────────────────────────

    def _pre_filter(self, papers: list[Paper]) -> list[Paper]:
        """去重前预过滤：按引用数取 top N，避免 O(n²) 去重太慢。"""
        MAX = 3000
        if len(papers) > MAX:
            papers.sort(key=lambda p: p.citation_count or 0, reverse=True)
            papers = papers[:MAX]
            logger.info("Pre-filter: top %d by citations", MAX)
        return papers

    def _deduplicate(self, papers: list[Paper]) -> list[Paper]:
        """标题相似度去重。"""
        unique = []
        for p in papers:
            is_dup = False
            for existing in unique:
                if SequenceMatcher(None, p.title.lower(), existing.title.lower()).ratio() >= self.dedup_threshold:
                    is_dup = True
                    if p.citation_count > existing.citation_count or (not existing.abstract and p.abstract):
                        unique.remove(existing)
                        unique.append(p)
                    break
            if not is_dup:
                unique.append(p)
        logger.info("Dedup: %d -> %d", len(papers), len(unique))
        return unique

    def _enrich_pdf_links(self, papers: list[Paper]) -> list[Paper]:
        """强制将有 arxiv_id 的论文 pdf_url 覆盖为 arXiv 直链。"""
        for p in papers:
            if p.arxiv_id:
                p.pdf_url = f"https://arxiv.org/pdf/{p.arxiv_id}.pdf"
        return papers

    def _filter_relevance(self, papers: list[Paper]) -> list[Paper]:
        """关键词相关性过滤。"""
        terms = [
            "grasp", "grasping", "manipulation", "dexterous", "deformable",
            "soft object", "cloth", "tactile", "compliant", "robotic hand",
            "gripper", "prehension", "pick and place", "contact-rich",
            "sim-to-real", "reinforcement learning", "policy learning", "diffusion policy",
        ]
        filtered = [p for p in papers if any(t in f"{p.title} {p.abstract}".lower() for t in terms)]
        logger.info("Relevance: %d -> %d", len(papers), len(filtered))
        return filtered

    def _filter_citations(self, papers: list[Paper]) -> list[Paper]:
        """年份加权引用过滤。"""
        cfg = self.search_cfg.get("semantic_scholar", {}).get("citation_scoring", {})
        base_th = cfg.get("base_threshold", 5)
        recent_y = cfg.get("recent_years", 3)
        recent_th = cfg.get("recent_threshold", 2)
        cur = datetime.now().year

        filtered = []
        for p in papers:
            if p.year is None:
                filtered.append(p)
                continue
            age = cur - p.year
            th = recent_th if age <= recent_y else base_th
            if p.citation_count >= th:
                filtered.append(p)
        logger.info("Citation filter: %d -> %d", len(papers), len(filtered))
        return filtered

    def _filter_surveys_and_score(self, papers: list[Paper], chapters: list[Chapter]) -> list[Paper]:
        """按章节类型过滤综述 + 评分排序。

        - 综述类章节（introduction, challenges_and_trends, conclusion）：保留综述
        - 非综述类章节：移除综述
        最终合并去重后按分数排序取 top N。
        """
        # 分类
        surveys = [p for p in papers if _is_survey(p.title)]
        non_surveys = [p for p in papers if not _is_survey(p.title)]

        # 综述类章节可以用综述 + 非综述
        # 非综述类章节只能用非综述
        # 最终结果 = 非综述（所有章节共用）+ 综述（仅综述章节用）
        # 但因为我们输出的是一个扁平列表，后续 writer 按 chapter_tags 分配
        # 所以这里保留两种，让 paper_reader 和 writer 自己按 tags 过滤

        # 评分
        keywords = self.config.get("project", {}).get("keywords", [])
        scored = [(self._score(p, keywords), p) for p in papers]
        scored.sort(key=lambda x: x[0], reverse=True)

        result = [p for _, p in scored[:self.max_total_papers]]
        logger.info("Scored & ranked: %d surveys, %d non-surveys, kept %d",
                     len(surveys), len(non_surveys), len(result))
        return result

    def _score(self, paper: Paper, keywords: list[str]) -> float:
        """加权评分：引用数 + 时效性 + 关键词匹配。"""
        norm_cite = min(math.log1p(paper.citation_count) / math.log1p(500), 1.0)
        cur = datetime.now().year
        age = max(cur - (paper.year or cur), 0)
        norm_recency = max(1.0 - age / 10.0, 0.0)
        relevance = 0.0
        if paper.abstract and keywords:
            abstract_lower = paper.abstract.lower()
            relevance = sum(1 for kw in keywords if kw.lower() in abstract_lower) / len(keywords)
        return self.w_cite * norm_cite + self.w_recency * norm_recency + self.w_relevance * relevance

    # ──────────────────────────────────────────────────────────────
    # Checkpoint
    # ──────────────────────────────────────────────────────────────

    def _load_checkpoint(self) -> AgentCheckpoint:
        ckpt_dir = Path(self.config.get("checkpoint", {}).get("dir", "./outputs/checkpoints"))
        return AgentCheckpoint.load(ckpt_dir / "literature_searcher.json")

    def _save_ckpt(self, queries: set[str], papers: list[Paper], status: str = "running"):
        ckpt_dir = Path(self.config.get("checkpoint", {}).get("dir", "./outputs/checkpoints"))
        AgentCheckpoint(
            agent_name="literature_searcher",
            status=status,
            progress=1.0 if status == "completed" else 0.5,
            data={"completed_queries": list(queries), "papers": [p.to_dict() for p in papers]},
            timestamp=datetime.now().isoformat(),
        ).save(ckpt_dir / "literature_searcher.json")
