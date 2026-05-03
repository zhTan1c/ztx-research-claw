"""
ztx-research-claw / agents / literature_searcher.py
LiteratureSearcher — searches papers from Semantic Scholar, OpenAlex, and arXiv.
"""

from __future__ import annotations

import asyncio
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from models import AgentCheckpoint, Chapter, Paper, SearchResult

logger = logging.getLogger(__name__)

# arXiv Atom namespace
ATOM_NS = "{http://www.w3.org/2005/Atom}"


class LiteratureSearcher:
    """Search papers from Semantic Scholar, OpenAlex, and arXiv.

    Reads from config dict which must contain 'search' and
    'agents.literature_searcher' sections.
    """

    def __init__(self, config: dict) -> None:
        self.config = config
        self.search_cfg: dict = config.get("search", {})
        self.agent_cfg: dict = config.get("agents", {}).get("literature_searcher", {})
        self.network_cfg: dict = config.get("network", {})

        # Pipeline order
        self.pipeline: list[str] = self.agent_cfg.get(
            "search_pipeline", ["semantic_scholar", "openalex", "arxiv"]
        )

        # Deduplication
        self.dedup_threshold: float = self.agent_cfg.get("dedup_threshold", 0.85)

        # Scoring weights
        scoring = self.agent_cfg.get("relevance_scoring", {})
        self.citation_weight: float = scoring.get("citation_weight", 0.3)
        self.recency_weight: float = scoring.get("recency_weight", 0.2)
        self.abstract_match_weight: float = scoring.get("abstract_match_weight", 0.5)

        # Max results
        self.max_total_papers: int = self.agent_cfg.get("max_total_papers", 200)

        # HTTP timeout
        self.timeout: float = self.network_cfg.get("timeout", 30)

        # Rate-limit semaphores per source
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        for source_name, source_cfg in self.search_cfg.items():
            if isinstance(source_cfg, dict) and source_cfg.get("enabled", True):
                rate = source_cfg.get("rate_limit", 10)
                self._semaphores[source_name] = asyncio.Semaphore(rate)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self, chapters: list[Chapter]) -> list[Paper]:
        """Search papers for all chapters and return deduplicated, ranked list."""
        logger.info("LiteratureSearcher: starting search for %d chapters", len(chapters))

        # Load checkpoint if available
        checkpoint = self.load_checkpoint()
        already_done: set[str] = set(checkpoint.data.get("completed_queries", []))
        all_papers: list[Paper] = []
        for p_data in checkpoint.data.get("papers", []):
            all_papers.append(Paper.from_dict(p_data))

        # Generate queries for every chapter
        chapter_queries: list[tuple[str, str]] = []  # (query, chapter_id)
        for chapter in chapters:
            queries = self._generate_queries(chapter)
            for q in queries:
                chapter_queries.append((q, chapter.chapter_id))

        logger.info("Generated %d search queries total", len(chapter_queries))

        # Run pipeline for each query
        for query, chapter_id in chapter_queries:
            if query in already_done:
                logger.debug("Skipping already-done query: %s", query)
                continue

            for source_name in self.pipeline:
                source_cfg = self.search_cfg.get(source_name, {})
                if not source_cfg.get("enabled", True):
                    continue

                try:
                    papers = await self._dispatch_search(source_name, query, source_cfg)
                    all_papers.extend(papers)
                    logger.info(
                        "Source '%s' query '%s' => %d papers",
                        source_name,
                        query,
                        len(papers),
                    )
                except Exception as exc:
                    logger.error(
                        "Source '%s' query '%s' failed: %s", source_name, query, exc
                    )

            # Mark query as done and periodically checkpoint
            already_done.add(query)
            if len(already_done) % 10 == 0:
                self._save_checkpoint_data(already_done, all_papers)

        # Deduplicate
        deduped = self._deduplicate(all_papers, self.dedup_threshold)
        logger.info("After deduplication: %d papers (from %d)", len(deduped), len(all_papers))

        # Filter by citation thresholds
        filtered = self._apply_citation_filter(deduped)
        logger.info("After citation filtering: %d papers", len(filtered))

        # Score and rank
        scored = [(self._score_paper(p), p) for p in filtered]
        scored.sort(key=lambda x: x[0], reverse=True)

        # Truncate
        result = [p for _, p in scored[: self.max_total_papers]]
        logger.info("Final result: %d papers", len(result))

        # Save final checkpoint
        self._save_checkpoint_data(already_done, result, status="completed")
        return result

    # ------------------------------------------------------------------
    # Checkpoint helpers
    # ------------------------------------------------------------------

    def load_checkpoint(self) -> AgentCheckpoint:
        ckpt_dir = Path(self.config.get("checkpoint", {}).get("dir", "./outputs/checkpoints"))
        ckpt_file = ckpt_dir / "literature_searcher.json"
        return AgentCheckpoint.load(ckpt_file)

    def save_checkpoint(self, checkpoint: AgentCheckpoint) -> None:
        ckpt_dir = Path(self.config.get("checkpoint", {}).get("dir", "./outputs/checkpoints"))
        ckpt_file = ckpt_dir / "literature_searcher.json"
        checkpoint.save(ckpt_file)

    def _save_checkpoint_data(
        self,
        completed_queries: set[str],
        papers: list[Paper],
        status: str = "running",
    ) -> None:
        ckpt = AgentCheckpoint(
            agent_name="literature_searcher",
            status=status,
            phase="search",
            progress=0.5 if status == "running" else 1.0,
            data={
                "completed_queries": list(completed_queries),
                "papers": [p.to_dict() for p in papers],
            },
            timestamp=datetime.now().isoformat(),
        )
        self.save_checkpoint(ckpt)

    # ------------------------------------------------------------------
    # Query generation
    # ------------------------------------------------------------------

    def _generate_queries(self, chapter: Chapter) -> list[str]:
        """Generate 2-3 search queries from chapter title, section titles, and project keywords."""
        queries: list[str] = []

        # Chapter title as primary query
        title = chapter.title.strip()
        if title:
            queries.append(title)

        # Combine top section titles
        section_titles = [s.title for s in chapter.sections[:3] if s.title]
        if section_titles:
            combined = " ".join(section_titles)
            queries.append(combined)

        # Combine chapter title with project keywords for a broader query
        keywords = self.config.get("project", {}).get("keywords", [])
        if keywords and title:
            # Pick first 2 keywords that aren't already in the title
            title_lower = title.lower()
            extra_kw = [kw for kw in keywords if kw.lower() not in title_lower][:2]
            if extra_kw:
                queries.append(f"{title} {' '.join(extra_kw)}")

        # Ensure uniqueness and non-empty
        seen: set[str] = set()
        unique: list[str] = []
        for q in queries:
            q = q.strip()
            if q and q.lower() not in seen:
                seen.add(q.lower())
                unique.append(q)

        return unique[:3]

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    async def _dispatch_search(
        self, source_name: str, query: str, cfg: dict
    ) -> list[Paper]:
        """Dispatch to the correct source search method."""
        dispatch = {
            "semantic_scholar": self._search_semantic_scholar,
            "openalex": self._search_openalex,
            "arxiv": self._search_arxiv,
        }
        handler = dispatch.get(source_name)
        if handler is None:
            logger.warning("Unknown search source: %s", source_name)
            return []
        return await handler(query, cfg)

    # ------------------------------------------------------------------
    # Semantic Scholar
    # ------------------------------------------------------------------

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def _search_semantic_scholar(self, query: str, cfg: dict) -> list[Paper]:
        """Search Semantic Scholar API."""
        base_url = cfg.get("base_url", "https://api.semanticscholar.org/graph/v1")
        fields = cfg.get(
            "fields",
            "paperId,title,abstract,year,citationCount,referenceCount,authors,venue,openAccessPdf,tldr,influentialCitationCount",
        )
        fields_of_study_list = cfg.get("fields_of_study", [])
        # IMPORTANT: fields_of_study must be a comma-separated string
        fields_of_study = ",".join(fields_of_study_list) if fields_of_study_list else ""
        max_results = cfg.get("max_results_per_query", 100)
        api_key = cfg.get("api_key")

        url = f"{base_url}/paper/search"
        params: dict = {
            "query": query,
            "fields": fields,
            "limit": max_results,
        }
        if fields_of_study:
            params["fieldsOfStudy"] = fields_of_study

        headers: dict[str, str] = {}
        if api_key:
            headers["x-api-key"] = api_key

        sem = self._semaphores.get("semantic_scholar", asyncio.Semaphore(1))
        async with sem:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(url, params=params, headers=headers)
                resp.raise_for_status()
                data = resp.json()

        papers: list[Paper] = []
        for item in data.get("data", []):
            authors = [a.get("name", "") for a in item.get("authors", [])]
            pdf_url = None
            oa = item.get("openAccessPdf")
            if oa and isinstance(oa, dict):
                pdf_url = oa.get("url")

            paper = Paper(
                paper_id=item.get("paperId", ""),
                title=item.get("title", ""),
                abstract=item.get("abstract", "") or "",
                year=item.get("year"),
                citation_count=item.get("citationCount", 0),
                influential_citation_count=item.get("influentialCitationCount", 0),
                authors=authors,
                venue=item.get("venue", "") or "",
                doi=item.get("externalIds", {}).get("DOI") if isinstance(item.get("externalIds"), dict) else None,
                arxiv_id=item.get("externalIds", {}).get("ArXiv") if isinstance(item.get("externalIds"), dict) else None,
                pdf_url=pdf_url,
                source="semantic_scholar",
            )
            papers.append(paper)

        return papers

    # ------------------------------------------------------------------
    # OpenAlex
    # ------------------------------------------------------------------

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def _search_openalex(self, query: str, cfg: dict) -> list[Paper]:
        """Search OpenAlex API."""
        base_url = cfg.get("base_url", "https://api.openalex.org")
        max_results = cfg.get("max_results_per_query", 100)
        mailto = cfg.get("mailto", "")
        filter_topics = cfg.get("filter_topics", [])

        # Build concept filter: OR concepts together
        # OpenAlex expects concept IDs but we pass names; the API resolves them
        topic_filter = "|".join(filter_topics) if filter_topics else ""

        url = f"{base_url}/works"
        params: dict = {
            "search": query,
            "per_page": max_results,
        }
        if mailto:
            params["mailto"] = mailto
        if topic_filter:
            params["filter"] = f"concepts.id:{topic_filter}"

        sem = self._semaphores.get("openalex", asyncio.Semaphore(1))
        async with sem:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()

        papers: list[Paper] = []
        for item in data.get("results", []):
            # Parse authors
            authorships = item.get("authorships", [])
            authors = [
                a.get("author", {}).get("display_name", "")
                for a in authorships
                if a.get("author")
            ]

            # PDF URL from open_access
            oa = item.get("open_access", {})
            pdf_url = oa.get("oa_url") if oa else None

            # DOI
            doi_raw = item.get("doi", "") or ""
            doi = doi_raw.replace("https://doi.org/", "") if doi_raw else None

            # Extract arXiv ID from ids if present
            arxiv_id = None
            for loc in item.get("locations", []):
                landing = loc.get("landing_page_url", "") or ""
                if "arxiv.org" in landing:
                    m = re.search(r"arxiv\.org/abs/(\d+\.\d+)", landing)
                    if m:
                        arxiv_id = m.group(1)
                        break

            # Year
            year = None
            pub_year = item.get("publication_year")
            if pub_year is not None:
                try:
                    year = int(pub_year)
                except (ValueError, TypeError):
                    pass

            paper = Paper(
                paper_id=item.get("id", ""),
                title=item.get("title", "") or "",
                abstract=item.get("abstract_inverted_index") and self._openalex_abstract(item.get("abstract_inverted_index", {})) or "",
                year=year,
                citation_count=item.get("cited_by_count", 0),
                authors=authors,
                venue=item.get("primary_location", {}).get("source", {}).get("display_name", "") if item.get("primary_location") and item["primary_location"].get("source") else "",
                doi=doi,
                arxiv_id=arxiv_id,
                pdf_url=pdf_url,
                source="openalex",
            )
            papers.append(paper)

        return papers

    @staticmethod
    def _openalex_abstract(inverted_index: dict) -> str:
        """Reconstruct abstract text from OpenAlex inverted index format.

        Inverted index maps word -> [position1, position2, ...].
        """
        if not inverted_index:
            return ""
        word_positions: list[tuple[int, str]] = []
        for word, positions in inverted_index.items():
            for pos in positions:
                word_positions.append((pos, word))
        word_positions.sort(key=lambda x: x[0])
        return " ".join(w for _, w in word_positions)

    # ------------------------------------------------------------------
    # arXiv
    # ------------------------------------------------------------------

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def _search_arxiv(self, query: str, cfg: dict) -> list[Paper]:
        """Search arXiv API — returns Atom XML."""
        base_url = cfg.get("base_url", "http://export.arxiv.org/api/query")
        categories = cfg.get("categories", ["cs.RO"])
        max_results = cfg.get("max_results_per_query", 100)
        sort_by = cfg.get("sort_by", "submittedDate")
        sort_order = cfg.get("sort_order", "descending")

        # Build category filter: OR categories
        cat_filter = "+OR+".join(f"cat:{c}" for c in categories)
        search_query = f"all:{query}+AND+{cat_filter}"

        params = {
            "search_query": search_query,
            "start": 0,
            "max_results": max_results,
            "sortBy": sort_by,
            "sortOrder": sort_order,
        }

        sem = self._semaphores.get("arxiv", asyncio.Semaphore(1))
        async with sem:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(base_url, params=params)
                resp.raise_for_status()
                xml_text = resp.text

        # Parse Atom XML
        papers: list[Paper] = []
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            logger.error("arXiv XML parse error: %s", exc)
            return papers

        for entry in root.findall(f"{ATOM_NS}entry"):
            title_el = entry.find(f"{ATOM_NS}title")
            title = title_el.text.strip().replace("\n", " ") if title_el is not None and title_el.text else ""

            summary_el = entry.find(f"{ATOM_NS}summary")
            abstract = summary_el.text.strip().replace("\n", " ") if summary_el is not None and summary_el.text else ""

            # Published date -> year
            published_el = entry.find(f"{ATOM_NS}published")
            year = None
            if published_el is not None and published_el.text:
                try:
                    year = int(published_el.text[:4])
                except (ValueError, TypeError):
                    pass

            # Authors
            authors = []
            for author_el in entry.findall(f"{ATOM_NS}author"):
                name_el = author_el.find(f"{ATOM_NS}name")
                if name_el is not None and name_el.text:
                    authors.append(name_el.text.strip())

            # arXiv ID and links
            arxiv_id = None
            pdf_url = None
            for link_el in entry.findall(f"{ATOM_NS}link"):
                href = link_el.get("href", "")
                link_type = link_el.get("type", "")
                link_title = link_el.get("title", "")
                if link_title == "pdf" or (link_type == "application/pdf"):
                    pdf_url = href
                if "/abs/" in href:
                    m = re.search(r"(\d{4}\.\d{4,5})(v\d+)?$", href)
                    if m:
                        arxiv_id = m.group(1)

            # Fallback: extract arXiv ID from id element
            if not arxiv_id:
                id_el = entry.find(f"{ATOM_NS}id")
                if id_el is not None and id_el.text:
                    m = re.search(r"(\d{4}\.\d{4,5})(v\d+)?$", id_el.text)
                    if m:
                        arxiv_id = m.group(1)

            # DOI from arxiv:doi element
            doi = None
            doi_el = entry.find("{http://arxiv.org/schemas/atom}doi")
            if doi_el is not None and doi_el.text:
                doi = doi_el.text.strip()

            if not title:
                continue

            paper = Paper(
                paper_id=arxiv_id or title,
                title=title,
                abstract=abstract,
                year=year,
                citation_count=0,  # arXiv API doesn't provide citation counts
                authors=authors,
                venue="arXiv",
                doi=doi,
                arxiv_id=arxiv_id,
                pdf_url=pdf_url,
                source="arxiv",
            )
            papers.append(paper)

        return papers

    # ------------------------------------------------------------------
    # Deduplication
    # ------------------------------------------------------------------

    def _deduplicate(self, papers: list[Paper], threshold: float) -> list[Paper]:
        """Remove duplicates based on title similarity using SequenceMatcher."""
        if not papers:
            return []

        unique: list[Paper] = []
        for paper in papers:
            is_dup = False
            for existing in unique:
                ratio = SequenceMatcher(
                    None,
                    paper.title.lower().strip(),
                    existing.title.lower().strip(),
                ).ratio()
                if ratio >= threshold:
                    is_dup = True
                    # Prefer the one with more metadata (higher citation count, or has abstract)
                    if paper.citation_count > existing.citation_count or (
                        not existing.abstract and paper.abstract
                    ):
                        unique.remove(existing)
                        unique.append(paper)
                    break
            if not is_dup:
                unique.append(paper)

        return unique

    # ------------------------------------------------------------------
    # Citation filtering
    # ------------------------------------------------------------------

    def _apply_citation_filter(self, papers: list[Paper]) -> list[Paper]:
        """Filter papers by year-weighted citation thresholds.

        - Papers > 3 years old: need citation_count >= base_threshold (default 5)
        - Papers within 3 years: need citation_count >= recent_threshold (default 2)
        """
        s2_cfg = self.search_cfg.get("semantic_scholar", {}).get("citation_scoring", {})
        base_threshold = s2_cfg.get("base_threshold", 5)
        recent_years = s2_cfg.get("recent_years", 3)
        recent_threshold = s2_cfg.get("recent_threshold", 2)

        current_year = datetime.now().year
        filtered: list[Paper] = []

        for paper in papers:
            # Papers without year info pass through (likely arXiv)
            if paper.year is None:
                filtered.append(paper)
                continue

            age = current_year - paper.year
            if age <= recent_years:
                # Recent paper — lower threshold
                if paper.citation_count >= recent_threshold:
                    filtered.append(paper)
            else:
                # Older paper — higher threshold
                if paper.citation_count >= base_threshold:
                    filtered.append(paper)

        return filtered

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def _score_paper(self, paper: Paper) -> float:
        """Score a paper using weighted formula:
        citation_weight * norm_citations + recency_weight * norm_recency + abstract_match_weight * relevance
        """
        # Normalized citation score (log-scale, capped at ~500 citations)
        import math
        max_cites = 500
        norm_citations = min(math.log1p(paper.citation_count) / math.log1p(max_cites), 1.0)

        # Normalized recency score
        current_year = datetime.now().year
        year = paper.year or current_year
        # Papers from current year = 1.0, 10 years ago ≈ 0.0
        age = max(current_year - year, 0)
        norm_recency = max(1.0 - age / 10.0, 0.0)

        # Abstract relevance: fraction of project keywords found in abstract
        relevance = 0.0
        if paper.abstract:
            abstract_lower = paper.abstract.lower()
            keywords = self.config.get("project", {}).get("keywords", [])
            if keywords:
                matches = sum(1 for kw in keywords if kw.lower() in abstract_lower)
                relevance = matches / len(keywords)

        score = (
            self.citation_weight * norm_citations
            + self.recency_weight * norm_recency
            + self.abstract_match_weight * relevance
        )
        return score
