"""
ztx-research-claw / agents / paper_reader.py
PaperReader — uses MiMo V2.5 Pro (OpenAI-compatible API) to read and
extract structured information from academic papers.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import pdfplumber
from openai import OpenAI

from models import AgentCheckpoint, Paper, ReadingNotes

logger = logging.getLogger(__name__)


# ============================================================
# System prompt template for structured paper reading
# ============================================================

READING_SYSTEM_PROMPT = """\
You are an expert academic paper reader and research analyst.
Your task is to carefully read the provided paper content and produce a \
structured JSON analysis.

You MUST output a single valid JSON object (no markdown fences, no extra text) \
with exactly the following fields:

{
  "key_contributions": [
    "contribution 1",
    "contribution 2",
    ...
  ],
  "methodology_summary": "A concise summary of the proposed method / approach, \
including key techniques, architecture, and innovations.",
  "experimental_results": "Summary of the main experimental findings, benchmark \
results, ablation studies, and comparisons with baselines.",
  "limitations": "Identified limitations of the work: assumptions, scope \
restrictions, weaknesses in evaluation, or open issues acknowledged by the authors.",
  "relevance_to_survey": "How this paper relates to the survey topic \
'Deformable Object Dexterous Grasping'. Describe its position in the field, \
connections to other methods, and potential role in the survey narrative.",
  "chapter_tags": [
    "chapter_id_1",
    "chapter_id_2"
  ]
}

For chapter_tags, choose from these chapter identifiers that best match the \
paper's content (select all that apply):
- "introduction" — if the paper is foundational / high-level
- "non_rl_method" — if the method is non-reinforcement-learning (classical, \
optimization, heuristic, model-based without RL)
- "deep_rl_method" — if the method uses deep reinforcement learning
- "mixed_and_SOTA_method" — if the method combines RL with other paradigms, \
or represents state-of-the-art / hybrid / foundation-model approaches
- "experiment_and_performance" — if the paper focuses on benchmarks, datasets, \
or experimental evaluation
- "challenges_and_trends" — if the paper discusses open challenges, future \
directions, or emerging trends
- "conclusion" — if the paper is a concluding or position piece

Be thorough but concise. Extract only information explicitly present in the \
paper content provided."""

CROSS_COMPARE_SYSTEM_PROMPT = """\
You are an expert academic paper reader and research analyst.
You will be given content from MULTIPLE papers. For EACH paper, produce a \
structured JSON analysis.

You MUST output a JSON array of objects, one per paper (no markdown fences, \
no extra text). Each object must have exactly these fields:

{
  "paper_id": "<the paper's unique identifier>",
  "title": "<the paper's title>",
  "key_contributions": ["..."],
  "methodology_summary": "...",
  "experimental_results": "...",
  "limitations": "...",
  "relevance_to_survey": "How this paper relates to the survey topic \
'Deformable Object Dexterous Grasping'. Describe its position in the field, \
connections to other methods, and potential role in the survey narrative.",
  "chapter_tags": ["chapter_id_1", ...]
}

For chapter_tags, choose from:
- "introduction", "non_rl_method", "deep_rl_method",
  "mixed_and_SOTA_method", "experiment_and_performance",
  "challenges_and_trends", "conclusion"

In addition, include a final object with key "_comparison" summarising \
cross-paper themes, method evolution, and complementary findings.

Be thorough but concise."""


class PaperReader:
    """Read academic papers using MiMo V2.5 Pro and produce structured
    ReadingNotes for each paper.

    Supports three reading modes:
      - abstract_only:        brief analysis from abstract only
      - fulltext_deep:        deep reading of full PDF text (chunked)
      - cross_paper_compare:  batch compare multiple papers
    """

    def __init__(self, config: dict) -> None:
        self.config = config

        # LLM configuration
        llm_cfg: dict = config.get("llm", {}).get("mimo_v25_pro", {})
        self.base_url: str = llm_cfg.get("base_url", "")
        self.api_key: str = llm_cfg.get("api_key", "")
        self.model: str = llm_cfg.get("model", "mimo-v2.5-pro")
        self.default_max_tokens: int = llm_cfg.get("max_tokens", 131072)
        self.temperature: float = llm_cfg.get("temperature", 0.3)
        self.top_p: float = llm_cfg.get("top_p", 0.9)

        # Reading strategy
        strategy: dict = llm_cfg.get("reading_strategy", {})
        self.fulltext_chunk_size: int = strategy.get("fulltext_chunk_size", 8000)
        self.overlap_tokens: int = strategy.get("overlap_tokens", 500)
        self.max_papers_per_batch: int = strategy.get("max_papers_per_batch", 5)

        # Agent-level reading modes
        agent_cfg: dict = config.get("agents", {}).get("paper_reader", {})
        self.reading_modes: dict = agent_cfg.get("reading_modes", {})
        self.agent_model: str = agent_cfg.get("model", "mimo_v25_pro")

        # Checkpoint directory
        ckpt_cfg: dict = config.get("checkpoint", {})
        self.ckpt_dir = Path(ckpt_cfg.get("dir", "./outputs/checkpoints"))
        self.ckpt_file = self.ckpt_dir / "paper_reader.json"

        # Create OpenAI client
        self.client = OpenAI(base_url=self.base_url, api_key=self.api_key)

        logger.info(
            "PaperReader initialized — model=%s, chunk_size=%d, modes=%s",
            self.model,
            self.fulltext_chunk_size,
            list(self.reading_modes.keys()),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self, papers: list[Paper], mode: str = "fulltext_deep"
    ) -> list[ReadingNotes]:
        """Read papers and return structured ReadingNotes.

        Args:
            papers: list of Paper objects to read.
            mode: one of 'abstract_only', 'fulltext_deep', 'cross_paper_compare'.

        Returns:
            list of ReadingNotes, one per paper (or per batch for cross compare).
        """
        if mode not in self.reading_modes and mode != "cross_paper_compare":
            logger.warning(
                "Unknown reading mode '%s', falling back to 'abstract_only'", mode
            )
            mode = "abstract_only"

        mode_cfg: dict = self.reading_modes.get(mode, {})
        max_tokens: int = mode_cfg.get("max_tokens", self.default_max_tokens)

        logger.info(
            "PaperReader.run starting — mode=%s, papers=%d, max_tokens=%d",
            mode,
            len(papers),
            max_tokens,
        )

        # Load checkpoint to skip already-read papers
        checkpoint = self._load_checkpoint()
        already_read: set[str] = set(checkpoint.data.get("completed_paper_ids", []))
        all_notes: list[ReadingNotes] = []

        # Restore previous results from checkpoint
        for note_data in checkpoint.data.get("notes", []):
            try:
                all_notes.append(self._notes_from_dict(note_data))
            except Exception as exc:
                logger.warning("Failed to restore checkpoint note: %s", exc)

        # Cross-paper compare: batch all papers together
        if mode == "cross_paper_compare":
            remaining = [p for p in papers if p.paper_id not in already_read]
            if not remaining:
                logger.info("All papers already compared (checkpoint).")
                return all_notes

            system_prompt = self._build_system_prompt(mode, max_tokens)
            batch_notes = self._compare_papers(remaining, system_prompt)
            all_notes.extend(batch_notes)

            # Mark all as completed
            for n in batch_notes:
                already_read.add(n.paper_id)

            self._save_checkpoint(already_read, all_notes, status="completed")
            logger.info(
                "Cross-paper compare done — %d notes produced", len(batch_notes)
            )
            return all_notes

        # Single-paper modes: iterate
        total = len(papers)
        for idx, paper in enumerate(papers):
            if paper.paper_id in already_read:
                logger.debug(
                    "[%d/%d] Skipping already-read paper: %s",
                    idx + 1,
                    total,
                    paper.paper_id,
                )
                continue

            logger.info(
                "[%d/%d] Reading paper: %s — %s",
                idx + 1,
                total,
                paper.paper_id,
                paper.title[:80],
            )

            try:
                system_prompt = self._build_system_prompt(mode, max_tokens)
                notes = self._read_single_paper(paper, mode, system_prompt)
                all_notes.append(notes)
                already_read.add(paper.paper_id)
                logger.info("  -> chapter_tags: %s", notes.chapter_tags)
            except Exception as exc:
                logger.error(
                    "Failed to read paper %s: %s", paper.paper_id, exc, exc_info=True
                )
                # Create a minimal error note so the paper is not lost
                error_note = ReadingNotes(
                    paper_id=paper.paper_id,
                    title=paper.title,
                    raw_notes=f"[ERROR] {exc}",
                )
                all_notes.append(error_note)
                already_read.add(paper.paper_id)

            # Periodic checkpoint every 5 papers
            if (idx + 1) % 5 == 0:
                self._save_checkpoint(already_read, all_notes, status="running")

        self._save_checkpoint(already_read, all_notes, status="completed")
        logger.info(
            "PaperReader.run complete — %d notes total", len(all_notes)
        )
        return all_notes

    # ------------------------------------------------------------------
    # System prompt construction
    # ------------------------------------------------------------------

    def _build_system_prompt(self, mode: str, max_tokens: int) -> str:
        """Build the system prompt, optionally appending section hints."""
        if mode == "cross_paper_compare":
            return CROSS_COMPARE_SYSTEM_PROMPT

        prompt = READING_SYSTEM_PROMPT

        mode_cfg: dict = self.reading_modes.get(mode, {})
        sections: list[str] = mode_cfg.get("extract_sections", [])
        if sections:
            prompt += (
                "\n\nWhen extracting information, pay particular attention "
                "to the following sections (if present): "
                + ", ".join(sections)
                + "."
            )

        return prompt

    # ------------------------------------------------------------------
    # PDF text extraction
    # ------------------------------------------------------------------

    def _extract_text_from_pdf(self, pdf_path: str) -> str:
        """Extract all text from a PDF file using pdfplumber.

        Returns empty string if the file is missing, unreadable, or corrupt.
        """
        path = Path(pdf_path)
        if not path.exists():
            logger.warning("PDF not found: %s", pdf_path)
            return ""
        if path.stat().st_size == 0:
            logger.warning("PDF is empty: %s", pdf_path)
            return ""

        try:
            text_parts: list[str] = []
            with pdfplumber.open(str(path)) as pdf:
                for i, page in enumerate(pdf.pages):
                    try:
                        page_text = page.extract_text()
                        if page_text:
                            text_parts.append(page_text)
                    except Exception as exc:
                        logger.warning(
                            "Failed to extract text from page %d of %s: %s",
                            i + 1,
                            pdf_path,
                            exc,
                        )
            full_text = "\n\n".join(text_parts)
            logger.debug(
                "Extracted %d chars from %d pages (%s)",
                len(full_text),
                len(text_parts),
                pdf_path,
            )
            return full_text
        except Exception as exc:
            logger.error("PDF extraction failed for %s: %s", pdf_path, exc)
            return ""

    # ------------------------------------------------------------------
    # Single paper reading
    # ------------------------------------------------------------------

    def _read_single_paper(
        self, paper: Paper, mode: str, system_prompt: str
    ) -> ReadingNotes:
        """Read a single paper and return structured ReadingNotes.

        For 'abstract_only' mode, only the abstract is used.
        For 'fulltext_deep' mode, the PDF text is extracted and chunked.
        """
        if mode == "abstract_only":
            content = self._format_abstract_content(paper)
            user_content = (
                f"Paper ID: {paper.paper_id}\n"
                f"Title: {paper.title}\n\n"
                f"Abstract:\n{content}"
            )
            raw_response = self._call_llm(
                system_prompt, user_content, max_tokens=4000
            )
            return self._parse_notes_response(raw_response, paper)

        # fulltext_deep mode
        pdf_text = ""
        if paper.local_path:
            pdf_text = self._extract_text_from_pdf(paper.local_path)

        if not pdf_text:
            # Fallback to abstract if PDF is unavailable
            logger.warning(
                "No PDF text for %s, falling back to abstract", paper.paper_id
            )
            content = self._format_abstract_content(paper)
            user_content = (
                f"[PDF unavailable — using abstract only]\n\n"
                f"Paper ID: {paper.paper_id}\n"
                f"Title: {paper.title}\n\n"
                f"Abstract:\n{content}"
            )
            raw_response = self._call_llm(
                system_prompt, user_content, max_tokens=4000
            )
            return self._parse_notes_response(raw_response, paper)

        # Chunk the full text and process each chunk
        chunks = self._split_text_chunks(pdf_text, self.fulltext_chunk_size)
        logger.info(
            "Paper %s: %d chars split into %d chunks",
            paper.paper_id,
            len(pdf_text),
            len(chunks),
        )

        if len(chunks) == 1:
            # Single chunk — process directly
            user_content = (
                f"Paper ID: {paper.paper_id}\n"
                f"Title: {paper.title}\n\n"
                f"Full text:\n{chunks[0]}"
            )
            raw_response = self._call_llm(
                system_prompt, user_content, max_tokens=4000
            )
            return self._parse_notes_response(raw_response, paper)

        # Multiple chunks — process each and merge
        chunk_notes: list[ReadingNotes] = []
        for ci, chunk in enumerate(chunks):
            logger.debug(
                "  Chunk %d/%d (%d chars)", ci + 1, len(chunks), len(chunk)
            )
            chunk_context = (
                f"Paper ID: {paper.paper_id}\n"
                f"Title: {paper.title}\n"
                f"[Chunk {ci + 1}/{len(chunks)} of full text]\n\n"
                f"{chunk}"
            )
            try:
                raw = self._call_llm(system_prompt, chunk_context, max_tokens=4000)
                notes = self._parse_notes_response(raw, paper)
                chunk_notes.append(notes)
            except Exception as exc:
                logger.warning(
                    "Chunk %d/%d failed for paper %s: %s",
                    ci + 1,
                    len(chunks),
                    paper.paper_id,
                    exc,
                )

        if not chunk_notes:
            return ReadingNotes(
                paper_id=paper.paper_id,
                title=paper.title,
                raw_notes="[ERROR] All chunks failed to process",
            )

        return self._merge_chunk_notes(chunk_notes, paper)

    # ------------------------------------------------------------------
    # Cross-paper comparison
    # ------------------------------------------------------------------

    def _compare_papers(
        self, papers: list[Paper], system_prompt: str
    ) -> list[ReadingNotes]:
        """Batch-read multiple papers for cross-comparison.

        Papers are batched in groups of max_papers_per_batch.
        """
        all_notes: list[ReadingNotes] = []

        for batch_start in range(0, len(papers), self.max_papers_per_batch):
            batch = papers[batch_start : batch_start + self.max_papers_per_batch]
            logger.info(
                "Comparing batch %d-%d of %d papers",
                batch_start + 1,
                min(batch_start + self.max_papers_per_batch, len(papers)),
                len(papers),
            )

            # Build combined user content with abstracts + available PDF text
            parts: list[str] = []
            for p in batch:
                part = f"{'=' * 60}\nPaper ID: {p.paper_id}\nTitle: {p.title}\n"

                if p.abstract:
                    part += f"\nAbstract:\n{p.abstract}\n"

                # Try to extract conclusion / final sections from PDF
                if p.local_path:
                    pdf_text = self._extract_text_from_pdf(p.local_path)
                    if pdf_text:
                        conclusion = self._extract_conclusion_section(pdf_text)
                        if conclusion:
                            part += f"\nConclusion / Discussion:\n{conclusion}\n"

                parts.append(part)

            user_content = (
                "Compare and analyse the following papers:\n\n"
                + "\n".join(parts)
            )

            try:
                raw_response = self._call_llm(
                    system_prompt, user_content, max_tokens=8000
                )
                batch_notes = self._parse_compare_response(raw_response, batch)
                all_notes.extend(batch_notes)
            except Exception as exc:
                logger.error(
                    "Cross-compare batch failed: %s", exc, exc_info=True
                )
                # Create error notes for each paper in the batch
                for p in batch:
                    all_notes.append(
                        ReadingNotes(
                            paper_id=p.paper_id,
                            title=p.title,
                            raw_notes=f"[ERROR] Cross-compare failed: {exc}",
                        )
                    )

        return all_notes

    # ------------------------------------------------------------------
    # LLM interaction
    # ------------------------------------------------------------------

    def _call_llm(
        self,
        system_prompt: str,
        user_content: str,
        max_tokens: int = 4000,
    ) -> str:
        """Call the LLM with the given prompts and return the response text.

        Retries up to 3 times on transient errors.
        """
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        last_exc: Optional[Exception] = None
        for attempt in range(1, 4):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=self.temperature,
                    top_p=self.top_p,
                )
                content = response.choices[0].message.content
                if content is None:
                    raise ValueError("LLM returned empty response content")
                return content.strip()
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "LLM call attempt %d/3 failed: %s", attempt, exc
                )

        raise RuntimeError(
            f"LLM call failed after 3 attempts: {last_exc}"
        )

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    def _parse_notes_response(
        self, raw_response: str, paper: Paper
    ) -> ReadingNotes:
        """Parse LLM JSON response into a ReadingNotes dataclass."""
        data = self._extract_json_object(raw_response)
        if data is None:
            logger.warning(
                "Could not parse JSON from LLM response for paper %s; "
                "storing raw notes.",
                paper.paper_id,
            )
            return ReadingNotes(
                paper_id=paper.paper_id,
                title=paper.title,
                raw_notes=raw_response,
            )

        return ReadingNotes(
            paper_id=paper.paper_id,
            title=paper.title,
            key_contributions=data.get("key_contributions", []),
            methodology_summary=data.get("methodology_summary", ""),
            experimental_results=data.get("experimental_results", ""),
            limitations=data.get("limitations", ""),
            relevance_to_survey=data.get("relevance_to_survey", ""),
            chapter_tags=data.get("chapter_tags", []),
            raw_notes=raw_response,
        )

    def _parse_compare_response(
        self, raw_response: str, papers: list[Paper]
    ) -> list[ReadingNotes]:
        """Parse LLM JSON array response from cross-paper comparison."""
        paper_map = {p.paper_id: p for p in papers}

        # Try to extract a JSON array
        items = self._extract_json_array(raw_response)
        if items is None:
            logger.warning(
                "Could not parse JSON array from cross-compare response; "
                "creating raw notes for each paper."
            )
            return [
                ReadingNotes(
                    paper_id=p.paper_id,
                    title=p.title,
                    raw_notes=raw_response,
                )
                for p in papers
            ]

        notes_list: list[ReadingNotes] = []
        for item in items:
            pid = item.get("paper_id", "")
            # Skip comparison summary objects
            if pid == "_comparison":
                continue

            paper = paper_map.get(pid)
            if paper is None:
                # Try matching by title
                title = item.get("title", "").lower()
                for p in papers:
                    if p.title.lower() == title:
                        paper = p
                        break
            if paper is None:
                logger.warning(
                    "Cross-compare response references unknown paper_id '%s'",
                    pid,
                )
                continue

            notes_list.append(
                ReadingNotes(
                    paper_id=paper.paper_id,
                    title=paper.title,
                    key_contributions=item.get("key_contributions", []),
                    methodology_summary=item.get("methodology_summary", ""),
                    experimental_results=item.get("experimental_results", ""),
                    limitations=item.get("limitations", ""),
                    relevance_to_survey=item.get("relevance_to_survey", ""),
                    chapter_tags=item.get("chapter_tags", []),
                    raw_notes=json.dumps(item, ensure_ascii=False),
                )
            )

        # If we got fewer notes than papers, create placeholders for missing ones
        noted_ids = {n.paper_id for n in notes_list}
        for p in papers:
            if p.paper_id not in noted_ids:
                notes_list.append(
                    ReadingNotes(
                        paper_id=p.paper_id,
                        title=p.title,
                        raw_notes="[Missing from LLM cross-compare response]",
                    )
                )

        return notes_list

    # ------------------------------------------------------------------
    # JSON extraction helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_json_object(text: str) -> Optional[dict]:
        """Extract the first JSON object from arbitrary text.

        Strips markdown fences, leading/trailing prose, etc.
        """
        # Remove markdown code fences
        text = re.sub(r"```(?:json)?\s*", "", text)
        text = re.sub(r"```", "", text)
        text = text.strip()

        # Try direct parse first
        try:
            result = json.loads(text)
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

        # Try to find the first { ... } block
        brace_start = text.find("{")
        if brace_start == -1:
            return None

        depth = 0
        for i in range(brace_start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[brace_start : i + 1]
                    try:
                        result = json.loads(candidate)
                        if isinstance(result, dict):
                            return result
                    except json.JSONDecodeError:
                        pass
                    break

        return None

    @staticmethod
    def _extract_json_array(text: str) -> Optional[list]:
        """Extract the first JSON array from arbitrary text."""
        # Remove markdown code fences
        text = re.sub(r"```(?:json)?\s*", "", text)
        text = re.sub(r"```", "", text)
        text = text.strip()

        # Try direct parse
        try:
            result = json.loads(text)
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

        # Find the first [ ... ] block
        bracket_start = text.find("[")
        if bracket_start == -1:
            return None

        depth = 0
        for i in range(bracket_start, len(text)):
            if text[i] == "[":
                depth += 1
            elif text[i] == "]":
                depth -= 1
                if depth == 0:
                    candidate = text[bracket_start : i + 1]
                    try:
                        result = json.loads(candidate)
                        if isinstance(result, list):
                            return result
                    except json.JSONDecodeError:
                        pass
                    break

        return None

    # ------------------------------------------------------------------
    # Text chunking and section extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _split_text_chunks(text: str, chunk_size: int = 8000) -> list[str]:
        """Split text into chunks of approximately chunk_size characters.

        Tries to split at paragraph boundaries when possible.
        """
        if len(text) <= chunk_size:
            return [text]

        chunks: list[str] = []
        paragraphs = text.split("\n\n")
        current_chunk: list[str] = []
        current_len = 0

        for para in paragraphs:
            para_len = len(para) + 2  # +2 for the \n\n separator

            # If a single paragraph exceeds chunk_size, split it by sentences
            if para_len > chunk_size:
                if current_chunk:
                    chunks.append("\n\n".join(current_chunk))
                    current_chunk = []
                    current_len = 0

                # Split long paragraph by sentences
                sentences = re.split(r"(?<=[.!?])\s+", para)
                sent_chunk: list[str] = []
                sent_len = 0
                for sent in sentences:
                    if sent_len + len(sent) + 1 > chunk_size and sent_chunk:
                        chunks.append(" ".join(sent_chunk))
                        sent_chunk = []
                        sent_len = 0
                    sent_chunk.append(sent)
                    sent_len += len(sent) + 1
                if sent_chunk:
                    chunks.append(" ".join(sent_chunk))
                continue

            if current_len + para_len > chunk_size and current_chunk:
                chunks.append("\n\n".join(current_chunk))
                current_chunk = []
                current_len = 0

            current_chunk.append(para)
            current_len += para_len

        if current_chunk:
            chunks.append("\n\n".join(current_chunk))

        return chunks

    @staticmethod
    def _extract_conclusion_section(text: str, max_chars: int = 3000) -> str:
        """Try to extract the conclusion/discussion section from PDF text."""
        # Look for common conclusion headers
        patterns = [
            r"(?i)\n\s*(?:\d+\.?\s*)?(?:conclusion|discussion|summary)\s*s?\s*\n",
            r"(?i)\n\s*(?:\d+\.?\s*)?(?:concluding\s+remarks|final\s+remarks)\s*\n",
        ]

        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                start = match.start()
                # Take from the conclusion header to the end (or max_chars)
                conclusion = text[start:]
                if len(conclusion) > max_chars:
                    conclusion = conclusion[:max_chars]
                return conclusion.strip()

        # Fallback: take the last max_chars of text (often contains conclusion)
        if len(text) > max_chars:
            return text[-max_chars:].strip()

        return ""

    # ------------------------------------------------------------------
    # Chunk merging
    # ------------------------------------------------------------------

    @staticmethod
    def _merge_chunk_notes(
        chunk_notes: list[ReadingNotes], paper: Paper
    ) -> ReadingNotes:
        """Merge notes from multiple chunks into a single ReadingNotes."""
        if len(chunk_notes) == 1:
            return chunk_notes[0]

        # Collect all unique contributions and tags
        all_contributions: list[str] = []
        all_tags: list[str] = []
        seen_contributions: set[str] = set()
        seen_tags: set[str] = set()

        methodology_parts: list[str] = []
        results_parts: list[str] = []
        limitations_parts: list[str] = []
        relevance_parts: list[str] = []
        raw_parts: list[str] = []

        for note in chunk_notes:
            for c in note.key_contributions:
                c_lower = c.lower().strip()
                if c_lower and c_lower not in seen_contributions:
                    seen_contributions.add(c_lower)
                    all_contributions.append(c)

            for t in note.chapter_tags:
                if t not in seen_tags:
                    seen_tags.add(t)
                    all_tags.append(t)

            if note.methodology_summary:
                methodology_parts.append(note.methodology_summary)
            if note.experimental_results:
                results_parts.append(note.experimental_results)
            if note.limitations:
                limitations_parts.append(note.limitations)
            if note.relevance_to_survey:
                relevance_parts.append(note.relevance_to_survey)
            if note.raw_notes:
                raw_parts.append(note.raw_notes)

        return ReadingNotes(
            paper_id=paper.paper_id,
            title=paper.title,
            key_contributions=all_contributions,
            methodology_summary="\n".join(methodology_parts),
            experimental_results="\n".join(results_parts),
            limitations="\n".join(limitations_parts),
            relevance_to_survey="\n".join(relevance_parts),
            chapter_tags=all_tags,
            raw_notes="\n---\n".join(raw_parts),
        )

    # ------------------------------------------------------------------
    # Content formatting helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_abstract_content(paper: Paper) -> str:
        """Format paper abstract with metadata for LLM consumption."""
        parts: list[str] = []
        if paper.abstract:
            parts.append(paper.abstract)
        else:
            parts.append("[Abstract not available]")
        if paper.venue:
            parts.append(f"Venue: {paper.venue}")
        if paper.year:
            parts.append(f"Year: {paper.year}")
        if paper.citation_count:
            parts.append(f"Citations: {paper.citation_count}")
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Checkpoint management
    # ------------------------------------------------------------------

    def _load_checkpoint(self) -> AgentCheckpoint:
        """Load checkpoint from disk if it exists."""
        return AgentCheckpoint.load(self.ckpt_file)

    def _save_checkpoint(
        self,
        completed_ids: set[str],
        notes: list[ReadingNotes],
        status: str = "running",
    ) -> None:
        """Save checkpoint to disk."""
        ckpt = AgentCheckpoint(
            agent_name="paper_reader",
            status=status,
            phase="reading",
            progress=1.0 if status == "completed" else 0.5,
            data={
                "completed_paper_ids": list(completed_ids),
                "notes": [self._notes_to_dict(n) for n in notes],
            },
            timestamp=datetime.now().isoformat(),
        )
        self.ckpt_dir.mkdir(parents=True, exist_ok=True)
        ckpt.save(self.ckpt_file)
        logger.debug(
            "Checkpoint saved — %d completed, %d notes, status=%s",
            len(completed_ids),
            len(notes),
            status,
        )

    # ------------------------------------------------------------------
    # Serialization helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _notes_to_dict(notes: ReadingNotes) -> dict:
        """Serialize ReadingNotes to a dict for checkpoint storage."""
        return {
            "paper_id": notes.paper_id,
            "title": notes.title,
            "key_contributions": notes.key_contributions,
            "methodology_summary": notes.methodology_summary,
            "experimental_results": notes.experimental_results,
            "limitations": notes.limitations,
            "relevance_to_survey": notes.relevance_to_survey,
            "chapter_tags": notes.chapter_tags,
            "raw_notes": notes.raw_notes,
        }

    @staticmethod
    def _notes_from_dict(d: dict) -> ReadingNotes:
        """Deserialize a dict back into ReadingNotes."""
        return ReadingNotes(
            paper_id=d.get("paper_id", ""),
            title=d.get("title", ""),
            key_contributions=d.get("key_contributions", []),
            methodology_summary=d.get("methodology_summary", ""),
            experimental_results=d.get("experimental_results", ""),
            limitations=d.get("limitations", ""),
            relevance_to_survey=d.get("relevance_to_survey", ""),
            chapter_tags=d.get("chapter_tags", []),
            raw_notes=d.get("raw_notes", ""),
        )
