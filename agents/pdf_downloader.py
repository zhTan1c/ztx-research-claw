"""
ztx-research-claw / agents / pdf_downloader.py
PDFDownloader — 下载 PDF 并提取初步 BibTeX 引用元数据
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import Path
from typing import Optional

import httpx

from models import Paper, AgentCheckpoint, Citation

logger = logging.getLogger(__name__)


class PDFDownloader:
    """Download PDFs from multiple sources and extract preliminary citation metadata."""

    def __init__(self, config: dict) -> None:
        self.config = config
        agent_cfg = config.get("agents", {}).get("pdf_downloader", {})
        self.sources_priority: list[str] = agent_cfg.get(
            "sources_priority",
            ["open_access_pdf", "arxiv_pdf", "unpaywall"],
        )
        self.download_timeout: int = agent_cfg.get("download_timeout", 60)
        self.max_concurrent: int = agent_cfg.get("max_concurrent", 5)
        self.extract_citation: bool = agent_cfg.get("extract_citation_on_download", True)

        # Unpaywall config (under search.unpaywall)
        self.unpaywall_cfg: dict = config.get("search", {}).get("unpaywall", {})

        # Network config
        self.network_cfg: dict = config.get("network", {})

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, papers: list[Paper], pdf_dir: str) -> list[Paper]:
        """Download PDFs for all *papers* into *pdf_dir* and return updated list."""

        Path(pdf_dir).mkdir(parents=True, exist_ok=True)
        semaphore = asyncio.Semaphore(self.max_concurrent)

        # Checkpoint: load previous progress
        checkpoint = self._load_checkpoint()
        completed_ids: set[str] = set(checkpoint.data.get("completed_ids", []))

        # Build proxy dict for httpx if configured
        proxy: Optional[str] = None
        if self.network_cfg.get("use_explicit_proxy"):
            proxy = self.network_cfg.get("proxy", {}).get("https")

        async with httpx.AsyncClient(
            timeout=self.download_timeout,
            follow_redirects=True,
            proxy=proxy,
            headers=self.network_cfg.get("headers", {}),
            verify=self.network_cfg.get("verify_ssl", True),
            trust_env=True,
        ) as client:
            tasks = []
            for paper in papers:
                # Skip if already downloaded (resume support)
                if paper.local_path and Path(paper.local_path).is_file():
                    logger.info("Already downloaded: %s — skipping", paper.title)
                    # Still extract citation if missing
                    if self.extract_citation and not paper.preliminary_bib:
                        paper.preliminary_bib = self._extract_citation(paper)
                    completed_ids.add(paper.paper_id)
                    continue
                if paper.paper_id in completed_ids and paper.local_path:
                    continue

                tasks.append(self._process_one(paper, pdf_dir, semaphore, client, completed_ids))

            if tasks:
                await asyncio.gather(*tasks)

        # Save checkpoint
        checkpoint.data["completed_ids"] = list(completed_ids)
        checkpoint.status = "completed"
        checkpoint.progress = 1.0
        checkpoint.timestamp = time.strftime("%Y-%m-%dT%H:%M:%S")
        self._save_checkpoint(checkpoint)

        return papers

    # ------------------------------------------------------------------
    # Process a single paper
    # ------------------------------------------------------------------

    async def _process_one(
        self,
        paper: Paper,
        pdf_dir: str,
        semaphore: asyncio.Semaphore,
        client: httpx.AsyncClient,
        completed_ids: set[str],
    ) -> None:
        async with semaphore:
            filename = self._sanitize_filename(paper.title)
            save_path = str(Path(pdf_dir) / f"{filename}_{paper.paper_id}.pdf")

            downloaded = False
            for source in self.sources_priority:
                url: Optional[str] = None
                try:
                    if source == "open_access_pdf":
                        url = paper.pdf_url
                    elif source == "arxiv_pdf":
                        if paper.arxiv_id:
                            url = f"https://arxiv.org/pdf/{paper.arxiv_id}.pdf"
                    elif source == "unpaywall":
                        if paper.doi:
                            url = await self._try_unpaywall(paper.doi, self.unpaywall_cfg)
                    elif source == "arxiv_search":
                        # Last resort: search arXiv by title
                        url = await self._search_arxiv_by_title(paper.title, client)
                    else:
                        logger.debug("Unknown source %s — skipping", source)
                        continue

                    if not url:
                        logger.debug("No URL from source=%s for %s", source, paper.title)
                        continue

                    logger.info("Trying %s for: %s", source, paper.title)
                    if await self._download_pdf(client, url, save_path, self.download_timeout):
                        paper.local_path = save_path
                        downloaded = True
                        completed_ids.add(paper.paper_id)
                        logger.info("Downloaded via %s: %s", source, paper.title)
                        break
                except Exception as exc:
                    logger.warning("Source %s failed for %s: %s", source, paper.title, exc)
                    continue

            if not downloaded:
                logger.warning("Could not download PDF for: %s", paper.title)

            # Extract preliminary citation regardless of download success
            if self.extract_citation and not paper.preliminary_bib:
                paper.preliminary_bib = self._extract_citation(paper)

    # ------------------------------------------------------------------
    # Download helpers
    # ------------------------------------------------------------------

    async def _download_pdf(
        self,
        client: httpx.AsyncClient,
        url: str,
        save_path: str,
        timeout: int,
    ) -> bool:
        """Stream-download a PDF from *url* to *save_path*. Return True on success."""
        try:
            async with client.stream("GET", url, timeout=timeout) as resp:
                if resp.status_code != 200:
                    logger.debug("HTTP %d for %s", resp.status_code, url)
                    return False
                # Sanity check content-type (allow 'application/pdf' or 'application/octet-stream')
                content_type = resp.headers.get("content-type", "")
                if "pdf" not in content_type.lower() and "octet-stream" not in content_type.lower():
                    # Accept anyway — some servers mislabel
                    logger.debug("Unexpected content-type '%s' for %s — trying anyway", content_type, url)

                Path(save_path).parent.mkdir(parents=True, exist_ok=True)
                with open(save_path, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=65536):
                        f.write(chunk)

            # Quick sanity: file should be > 1 KB and start with %PDF
            file_size = Path(save_path).stat().st_size
            if file_size < 1024:
                Path(save_path).unlink(missing_ok=True)
                logger.debug("File too small (%d bytes) for %s — removing", file_size, url)
                return False
            with open(save_path, "rb") as f:
                header = f.read(4)
            if header != b"%PDF":
                # Not a real PDF
                Path(save_path).unlink(missing_ok=True)
                logger.debug("File does not start with %%PDF for %s — removing", url)
                return False
            return True

        except (httpx.HTTPError, httpx.TimeoutException, OSError) as exc:
            logger.warning("Download failed (%s): %s", type(exc).__name__, url)
            # Clean up partial file
            Path(save_path).unlink(missing_ok=True)
            return False

    async def _try_unpaywall(self, doi: str, cfg: dict) -> Optional[str]:
        """Query Unpaywall API and return the best OA PDF URL, or None."""
        base_url = cfg.get("base_url", "https://api.unpaywall.org/v2")
        email = cfg.get("email", "")
        url = f"{base_url}/{doi}?email={email}"
        try:
            proxy: Optional[str] = None
            if self.network_cfg.get("use_explicit_proxy"):
                proxy = self.network_cfg.get("proxy", {}).get("https")

            async with httpx.AsyncClient(
                timeout=30,
                follow_redirects=True,
                proxy=proxy,
                headers=self.network_cfg.get("headers", {}),
                verify=self.network_cfg.get("verify_ssl", True),
                trust_env=True,
            ) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    logger.debug("Unpaywall HTTP %d for DOI %s", resp.status_code, doi)
                    return None
                data = resp.json()
                best = data.get("best_oa_location")
                if not best:
                    return None
                pdf_url = best.get("url_for_pdf") or best.get("url")
                return pdf_url
        except Exception as exc:
            logger.debug("Unpaywall request failed for DOI %s: %s", doi, exc)
            return None

    # ------------------------------------------------------------------
    # Citation extraction
    # ------------------------------------------------------------------

    async def _search_arxiv_by_title(
        self,
        title: str,
        client: httpx.AsyncClient,
    ) -> Optional[str]:
        """Search arXiv by paper title and return the PDF URL if found."""
        if not title:
            return None
        try:
            base_url = "https://export.arxiv.org/api/query"
            params = {
                "search_query": f'ti:"{title}"',
                "max_results": 1,
            }
            resp = await client.get(base_url, params=params, timeout=30)
            if resp.status_code != 200:
                return None
            import xml.etree.ElementTree as ET
            root = ET.fromstring(resp.text)
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            for entry in root.findall("atom:entry", ns):
                for link in entry.findall("atom:link", ns):
                    if link.get("title") == "pdf" or link.get("type") == "application/pdf":
                        return link.get("href")
                # Fallback: extract ID and construct PDF URL
                id_el = entry.find("atom:id", ns)
                if id_el is not None and id_el.text:
                    m = re.search(r"(\d{4}\.\d{4,5})(v\d+)?$", id_el.text)
                    if m:
                        return f"https://arxiv.org/pdf/{m.group(1)}.pdf"
        except Exception as exc:
            logger.debug("arXiv title search failed for '%s': %s", title[:50], exc)
        return None

    def _extract_citation(self, paper: Paper) -> str:
        """Generate a preliminary BibTeX string from existing paper metadata."""
        entry_type = "article"
        if paper.venue:
            venue_lower = paper.venue.lower()
            # Heuristic: if venue looks like a conference, use inproceedings
            conference_keywords = [
                "conference", "proceedings", "symposium", "workshop",
                "icra", "iros", "rss", "corl", "neurips", "icml", "iclr",
                "cvpr", "iccv", "eccv", "aaai", "ijcai", "emnlp", "acl",
                "siggraph", "safety", "ral", "ieee", "acm",
            ]
            if any(kw in venue_lower for kw in conference_keywords):
                entry_type = "inproceedings"

        # Build authors string: list[str] -> "Last, First and Last, First"
        authors_str = " and ".join(paper.authors) if paper.authors else ""

        # Determine journal vs booktitle
        journal = ""
        booktitle = ""
        if entry_type == "article":
            journal = paper.venue
        else:
            booktitle = paper.venue

        citation = Citation(
            key=paper.key,
            entry_type=entry_type,
            title=paper.title,
            authors=authors_str,
            year=paper.year or 0,
            journal=journal,
            booktitle=booktitle,
            doi=paper.doi or "",
            url=f"https://doi.org/{paper.doi}" if paper.doi else "",
            arxiv_id=paper.arxiv_id or "",
            abstract=(paper.abstract[:500] + "...") if paper.abstract and len(paper.abstract) > 500 else (paper.abstract or ""),
        )
        return citation.to_bibtex()

    # ------------------------------------------------------------------
    # Filename sanitisation
    # ------------------------------------------------------------------

    @staticmethod
    def _sanitize_filename(title: str) -> str:
        """Remove special characters and truncate to 50 characters."""
        if not title:
            return "untitled"
        # Keep only alphanumeric, spaces, hyphens, underscores
        clean = re.sub(r"[^\w\s\-]", "", title, flags=re.UNICODE)
        # Replace whitespace with underscore
        clean = re.sub(r"\s+", "_", clean.strip())
        # Truncate
        clean = clean[:50].rstrip("_")
        return clean or "untitled"

    # ------------------------------------------------------------------
    # Checkpoint helpers
    # ------------------------------------------------------------------

    def _load_checkpoint(self) -> AgentCheckpoint:
        ckpt_dir = self.config.get("checkpoint", {}).get("dir", "./outputs/checkpoints")
        ckpt_path = Path(ckpt_dir) / "pdf_downloader.json"
        return AgentCheckpoint.load(ckpt_path)

    def _save_checkpoint(self, checkpoint: AgentCheckpoint) -> None:
        ckpt_dir = self.config.get("checkpoint", {}).get("dir", "./outputs/checkpoints")
        ckpt_path = Path(ckpt_dir) / "pdf_downloader.json"
        checkpoint.save(ckpt_path)
