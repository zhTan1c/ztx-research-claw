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
from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from models import AgentCheckpoint, Chapter, Paper, SearchResult
from agents.seed_paper_parser import parse_seed_papers

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

        # LLM client for paper recommendation
        llm_cfg: dict = config.get("llm", {}).get("mimo_v25_pro", {})
        import httpx as _httpx
        _http_client = _httpx.Client(trust_env=True)
        self.llm_client = OpenAI(
            base_url=llm_cfg.get("base_url", ""),
            api_key=llm_cfg.get("api_key", ""),
            http_client=_http_client,
        )
        self.llm_model: str = llm_cfg.get("model", "mimo-v2.5-pro")
        self.prompts_cfg: dict = config.get("prompts", {})

        # Rate-limit semaphores per source
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        for source_name, source_cfg in self.search_cfg.items():
            if isinstance(source_cfg, dict) and source_cfg.get("enabled", True):
                rate = source_cfg.get("rate_limit", 10)
                self._semaphores[source_name] = asyncio.Semaphore(rate)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(
        self,
        chapters: list[Chapter],
        seed_papers_file: str | None = None,
    ) -> list[Paper]:
        """Search papers for all chapters and return deduplicated, ranked list.
 
        If *seed_papers_file* is given (e.g. ``data/引用论文.md``), parse it
        for user-curated seed papers, enrich them via Semantic Scholar, then
        expand via citation/references to discover related work along the
        timeline.
        """
        logger.info("LiteratureSearcher: starting search for %d chapters", len(chapters))

        # Load checkpoint if available
        checkpoint = self.load_checkpoint()
        already_done: set[str] = set(checkpoint.data.get("completed_queries", []))
        all_papers: list[Paper] = []
        for p_data in checkpoint.data.get("papers", []):
            all_papers.append(Paper.from_dict(p_data))

        # ── Seed papers ─────────────────────────────────────────────
        if seed_papers_file:
            seed_papers = parse_seed_papers(seed_papers_file)
            if seed_papers:
                logger.info("Loaded %d seed papers, enriching via S2...", len(seed_papers))
                enriched_seeds = await self._enrich_seeds_via_s2(seed_papers)
                all_papers.extend(enriched_seeds)
                logger.info("Enriched seeds: %d papers added to pool", len(enriched_seeds))

                # Citation expansion from seeds
                expansion_cfg = self.search_cfg.get("semantic_scholar", {}).get(
                    "citation_expansion", {}
                )
                if expansion_cfg.get("enabled", True):
                    depth = expansion_cfg.get("depth", 1)
                    expanded = await self._expand_citations(enriched_seeds, depth)
                    all_papers.extend(expanded)
                    logger.info("Citation expansion: %d papers discovered from seeds", len(expanded))

        # Generate queries for every chapter (now async, calls LLM)
        chapter_queries: list[tuple[str, str]] = []  # (query, chapter_id)
        for chapter in chapters:
            queries = await self._generate_queries(chapter)
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

        # Cross-source enrichment: prefer arXiv PDF links over publisher links
        deduped = self._enrich_pdf_links(deduped)
        logger.info("After PDF link enrichment: %d papers with pdf_url", sum(1 for p in deduped if p.pdf_url))

        # Filter by relevance to topic first
        relevant = self._filter_by_relevance(deduped)
        logger.info("After relevance filtering: %d papers", len(relevant))

        # Filter by citation thresholds
        filtered = self._apply_citation_filter(relevant)
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

    def _is_english_query(self, query: str) -> bool:
        """Check if query is primarily in English."""
        ascii_chars = sum(1 for c in query if ord(c) < 128)
        return ascii_chars / len(query) > 0.5 if query else False

    async def _ask_llm_for_papers(self, chapter: Chapter) -> list[str]:
        """Ask LLM to recommend paper titles based on chapter prompt.
        
        Returns a list of paper titles that should be cited in this chapter.
        """
        # Read chapter prompt file
        prompt_file = chapter.prompt_file
        prompt_content = ""
        if prompt_file:
            prompt_path = Path(prompt_file)
            if prompt_path.exists():
                prompt_content = prompt_path.read_text(encoding="utf-8")
        
        if not prompt_content:
            logger.warning("No prompt file found for chapter %s, using chapter title only", chapter.chapter_id)
            prompt_content = f"章节主题: {chapter.title}"
        
        # Build LLM prompt
        system_prompt = """你是一位学术论文推荐专家。你的任务是根据综述章节的写作要求，推荐该章节应该引用的论文。

要求：
1. 推荐的论文必须是真实存在的、已发表的学术论文
2. 优先推荐高引用、有影响力的经典论文
3. 推荐近3年的最新研究进展
4. 每个推荐必须包含完整的论文标题（英文）
5. 不要编造论文，只推荐你确信存在的论文

输出格式：每行一篇论文的标题，不要编号，不要其他内容。
例如：
Deep Reinforcement Learning for Robotic Manipulation with Asynchronous Off-Policy Updates
Soft Actor-Critic: Off-Policy Maximum Entropy Deep Reinforcement Learning with a Stochastic Actor"""

        user_prompt = f"""以下是综述第 {chapter.chapter_num} 章的写作要求：

{prompt_content}

请推荐 {chapter.target_citations} 篇应该在本章引用的论文标题。
只输出论文标题，每行一篇，不要其他内容。"""

        try:
            logger.info("Calling LLM to recommend papers for chapter %s...", chapter.chapter_id)
            response = self.llm_client.chat.completions.create(
                model=self.llm_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=4000,
                temperature=0.7,
            )
            content = response.choices[0].message.content or ""
            
            # Parse paper titles (one per line)
            titles = []
            for line in content.strip().split("\n"):
                line = line.strip()
                # Skip empty lines, numbered items, or lines that look like headers
                if not line or line.startswith("#") or line.startswith("以下是"):
                    continue
                # Remove numbering like "1. " or "- "
                line = re.sub(r"^[\d]+\.\s*", "", line)
                line = re.sub(r"^[-*]\s*", "", line)
                if len(line) > 10:  # Minimum reasonable title length
                    titles.append(line)
            
            logger.info("LLM recommended %d papers for chapter %s", len(titles), chapter.chapter_id)
            return titles
            
        except Exception as exc:
            logger.error("Failed to get paper recommendations from LLM: %s", exc)
            return []

    async def _generate_queries(self, chapter: Chapter) -> list[str]:
        """Generate search queries by asking LLM for paper recommendations.
        
        Instead of using keyword-based queries, we ask the LLM to recommend
        specific paper titles based on the chapter's prompt file, then use
        those titles for precise search on Semantic Scholar/arXiv.
        """
        # Ask LLM for paper recommendations
        paper_titles = await self._ask_llm_for_papers(chapter)
        
        if not paper_titles:
            logger.warning("LLM returned no paper titles for chapter %s, falling back to keyword search", chapter.chapter_id)
            # Fallback: use topic keywords
            return ["deformable object grasping", "dexterous manipulation"]
        
        return paper_titles

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
            async with httpx.AsyncClient(timeout=self.timeout, trust_env=True) as client:
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
            async with httpx.AsyncClient(timeout=self.timeout, trust_env=True) as client:
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
            async with httpx.AsyncClient(timeout=self.timeout, trust_env=True) as client:
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
    # Seed paper enrichment & citation expansion
    # ------------------------------------------------------------------

    async def _enrich_seeds_via_s2(self, seeds: list[Paper]) -> list[Paper]:
        """Enrich seed papers with metadata from Semantic Scholar.

        For each seed paper, try to find it in S2 by arXiv ID or title,
        then fill in abstract, citation_count, authors, venue, etc.
        """
        s2_cfg = self.search_cfg.get("semantic_scholar", {})
        base_url = s2_cfg.get("base_url", "https://api.semanticscholar.org/graph/v1")
        fields = s2_cfg.get(
            "fields",
            "paperId,title,abstract,year,citationCount,referenceCount,"
            "authors,venue,openAccessPdf,tldr,influentialCitationCount,"
            "externalIds,references,references.paperId,references.title,"
            "references.externalIds",
        )

        sem = self._semaphores.get("semantic_scholar", asyncio.Semaphore(5))
        enriched: list[Paper] = []

        async with httpx.AsyncClient(timeout=self.timeout, trust_env=True) as client:
            for i, seed in enumerate(seeds):
                paper_data = None
                try:
                    async with sem:
                        # Strategy 1: lookup by arXiv ID
                        if seed.arxiv_id:
                            paper_data = await self._s2_lookup(
                                client, f"ARXIV:{seed.arxiv_id}", base_url, fields
                            )
                        # Strategy 2: lookup by DOI
                        if not paper_data and seed.doi:
                            paper_data = await self._s2_lookup(
                                client, f"DOI:{seed.doi}", base_url, fields
                            )
                        # Strategy 3: search by title
                        if not paper_data and seed.title:
                            paper_data = await self._s2_search_by_title(
                                client, seed.title, base_url, fields
                            )
                except Exception as exc:
                    logger.warning(
                        "S2 enrichment failed for seed '%s': %s — keeping original metadata",
                        seed.title[:50], exc,
                    )
                    # paper_data stays None, seed keeps its original metadata

                if paper_data:
                    # Merge S2 metadata into seed paper
                    seed.paper_id = paper_data.get("paperId", seed.paper_id)
                    seed.abstract = paper_data.get("abstract") or seed.abstract
                    seed.citation_count = paper_data.get("citationCount", 0) or 0
                    seed.influential_citation_count = (
                        paper_data.get("influentialCitationCount", 0) or 0
                    )
                    seed.authors = [
                        a.get("name", "")
                        for a in paper_data.get("authors", [])
                    ]
                    seed.venue = paper_data.get("venue") or seed.venue
                    seed.year = paper_data.get("year") or seed.year

                    ext = paper_data.get("externalIds") or {}
                    if not seed.doi and ext.get("DOI"):
                        seed.doi = ext["DOI"]
                    if not seed.arxiv_id and ext.get("ArXiv"):
                        seed.arxiv_id = ext["ArXiv"]

                    oa = paper_data.get("openAccessPdf")
                    if oa and isinstance(oa, dict) and oa.get("url"):
                        seed.pdf_url = oa["url"]

                    logger.debug("Enriched seed: %s (citations=%d)", seed.title[:50], seed.citation_count)
                else:
                    logger.debug("Keeping original metadata for seed: %s", seed.title[:50])

                enriched.append(seed)

                # Rate limit: sleep between requests to avoid 429
                if i < len(seeds) - 1:
                    await asyncio.sleep(1.0)  # 1 req/sec to stay under S2 limits

        return enriched

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def _s2_lookup(
        self,
        client: httpx.AsyncClient,
        paper_id: str,
        base_url: str,
        fields: str,
    ) -> Optional[dict]:
        """Lookup a single paper on S2 by ID (ARXIV:xxx or DOI:xxx)."""
        url = f"{base_url}/paper/{paper_id}"
        try:
            resp = await client.get(url, params={"fields": fields})
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 429:
                logger.warning("S2 rate limited for %s, will retry", paper_id)
                raise Exception("Rate limited")
        except Exception as exc:
            if "Rate limited" in str(exc):
                raise
            logger.debug("S2 lookup failed for %s: %s", paper_id, exc)
        return None

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def _s2_search_by_title(
        self,
        client: httpx.AsyncClient,
        title: str,
        base_url: str,
        fields: str,
    ) -> Optional[dict]:
        """Search S2 by title and return the top match if similarity is high."""
        url = f"{base_url}/paper/search"
        try:
            resp = await client.get(
                url,
                params={"query": title, "fields": fields, "limit": 3},
            )
            if resp.status_code == 200:
                data = resp.json()
                for item in data.get("data", []):
                    ratio = SequenceMatcher(
                        None, title.lower(), (item.get("title") or "").lower()
                    ).ratio()
                    if ratio >= 0.90:
                        return item
                return None
            if resp.status_code == 429:
                logger.warning("S2 rate limited for title search, will retry")
                raise Exception("Rate limited")
        except Exception as exc:
            if "Rate limited" in str(exc):
                raise
            logger.debug("S2 title search failed for '%s': %s", title[:50], exc)
        return None

    async def _expand_citations(
        self, seeds: list[Paper], depth: int = 1
    ) -> list[Paper]:
        """Expand from seed papers by following references and citations.

        For each seed, fetch its references (papers it cites) and citations
        (papers that cite it) from Semantic Scholar.  This discovers the
        "timeline" of related work that the user wants.
        """
        s2_cfg = self.search_cfg.get("semantic_scholar", {})
        base_url = s2_cfg.get("base_url", "https://api.semanticscholar.org/graph/v1")
        fields = "paperId,title,abstract,year,citationCount,authors,venue,openAccessPdf,externalIds"

        sem = self._semaphores.get("semantic_scholar", asyncio.Semaphore(5))
        expanded: list[Paper] = []
        visited: set[str] = set()

        # Only expand seeds that have a valid S2 paper_id
        expandable = [s for s in seeds if s.paper_id and s.source != "seed"]
        if not expandable:
            # Try with original seed IDs
            expandable = [s for s in seeds if s.paper_id]

        logger.info("Expanding citations from %d seed papers (depth=%d)", len(expandable), depth)

        async with httpx.AsyncClient(timeout=self.timeout, trust_env=True) as client:
            for seed in expandable:
                if seed.paper_id in visited:
                    continue
                visited.add(seed.paper_id)

                # Fetch references (papers this seed cites)
                refs = await self._fetch_s2_relations(
                    client, seed.paper_id, "references", base_url, fields, sem
                )
                expanded.extend(refs)

                # Fetch citations (papers that cite this seed)
                cites = await self._fetch_s2_relations(
                    client, seed.paper_id, "citations", base_url, fields, sem
                )
                expanded.extend(cites)

                # Rate limit: be polite
                await asyncio.sleep(0.2)

        logger.info("Citation expansion discovered %d raw papers", len(expanded))
        return expanded

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def _fetch_s2_relations(
        self,
        client: httpx.AsyncClient,
        paper_id: str,
        relation: str,  # "references" or "citations"
        base_url: str,
        fields: str,
        sem: asyncio.Semaphore,
    ) -> list[Paper]:
        """Fetch references or citations for a paper from S2."""
        url = f"{base_url}/paper/{paper_id}/{relation}"
        papers: list[Paper] = []
        try:
            async with sem:
                resp = await client.get(url, params={"fields": fields, "limit": 100})
                if resp.status_code != 200:
                    if resp.status_code == 429:
                        logger.warning("S2 rate limited for %s %s, will retry", relation, paper_id)
                        raise Exception("Rate limited")
                    logger.debug("S2 %s failed for %s: HTTP %d", relation, paper_id, resp.status_code)
                    return []
                data = resp.json()

            for item in data.get("data", []):
                p = item.get("citedPaper") or item.get("paper") or item
                if not p or not p.get("paperId"):
                    continue

                oa = p.get("openAccessPdf")
                pdf_url = oa.get("url") if oa and isinstance(oa, dict) else None
                ext = p.get("externalIds") or {}

                paper = Paper(
                    paper_id=p.get("paperId", ""),
                    title=p.get("title") or "",
                    abstract=p.get("abstract") or "",
                    year=p.get("year"),
                    citation_count=p.get("citationCount", 0) or 0,
                    influential_citation_count=p.get("influentialCitationCount", 0) or 0,
                    authors=[a.get("name", "") for a in p.get("authors", [])],
                    venue=p.get("venue") or "",
                    doi=ext.get("DOI"),
                    arxiv_id=ext.get("ArXiv"),
                    pdf_url=pdf_url,
                    source=f"s2_{relation}",
                )
                if paper.title:
                    papers.append(paper)

        except Exception as exc:
            logger.debug("S2 %s request failed for %s: %s", relation, paper_id, exc)

        return papers

    # ------------------------------------------------------------------
    # Deduplication
    # ------------------------------------------------------------------
    def _enrich_pdf_links(self, papers: list[Paper]) -> list[Paper]:
        """Post-dedup enrichment: prefer arXiv PDF links over publisher links.

        If a paper has an arxiv_id, construct the direct arXiv PDF URL and
        override any publisher-hosted pdf_url.  arXiv PDFs are always free
        and don't require authentication, unlike publisher sites.
        """
        for paper in papers:
            if paper.arxiv_id:
                arxiv_pdf = f"https://arxiv.org/pdf/{paper.arxiv_id}.pdf"
                if paper.pdf_url != arxiv_pdf:
                    logger.debug(
                        "Enriching PDF link for '%s': %s -> %s",
                        paper.title[:50],
                        paper.pdf_url,
                        arxiv_pdf,
                    )
                    paper.pdf_url = arxiv_pdf
        return papers

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
    # Relevance filtering
    # ------------------------------------------------------------------

    def _filter_by_relevance(self, papers: list[Paper]) -> list[Paper]:
        """Filter papers to ensure they are related to the main topic."""
        topic_terms = [
            "grasp", "grasping", "manipulation", "dexterous", "deformable",
            "soft object", "cloth", "tactile", "compliant", "robotic hand",
            "gripper", "prehension", "pick and place",
            "contact-rich", "sim-to-real", "reinforcement learning",
            "policy learning", "diffusion policy"
        ]
        
        filtered = []
        for paper in papers:
            text = f"{paper.title} {paper.abstract}".lower()
            if any(term in text for term in topic_terms):
                filtered.append(paper)
            else:
                logger.debug("Filtered out (irrelevant): %s", paper.title[:60])
        
        logger.info(
            "Relevance filtering: %d -> %d papers",
            len(papers), len(filtered)
        )
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
