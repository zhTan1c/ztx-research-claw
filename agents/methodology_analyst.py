"""
ztx-research-claw / agents / methodology_analyst.py
MethodologyAnalyst — uses DeepSeek V4 Pro to analyze method evolution and generate
a taxonomy of techniques found across the reading notes.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import httpx
from openai import OpenAI, APIError, APIConnectionError, RateLimitError

from models import (
    AgentCheckpoint,
    MethodAnalysis,
    MethodEntry,
    ReadingNotes,
)

logger = logging.getLogger(__name__)

# Max characters for the combined notes before we start truncating.
# ~60k chars ≈ 15k tokens — leaves room for the prompt + response within 32k context.
_MAX_NOTES_CHARS = 60_000


class MethodologyAnalyst:
    """Analyze method evolution across reading notes and produce a taxonomy.

    Reads from config dict which must contain ``llm.deepseek_v4_pro`` and
    ``agents.methodology_analyst`` sections.
    """

    def __init__(self, config: dict) -> None:
        self.config = config

        # LLM config for DeepSeek V4 Pro
        ds_cfg: dict = config.get("llm", {}).get("deepseek_v4_pro", {})
        http_client = httpx.Client(trust_env=True)
        self.client = OpenAI(
            base_url="https://api.deepseek.com",
            api_key=ds_cfg.get("api_key", ""),
            http_client=http_client,
        )
        self.model: str = ds_cfg.get("model", "deepseek-v4-pro")
        self.max_tokens: int = ds_cfg.get("max_tokens", 8192)
        self.temperature: float = ds_cfg.get("temperature", 0.6)

        # Agent-specific config
        agent_cfg: dict = config.get("agents", {}).get("methodology_analyst", {})
        self.analysis_scope: list[str] = agent_cfg.get("analysis_scope", [])
        self.reasoning_effort: str = agent_cfg.get("reasoning_effort", "high")

        # Checkpoint
        ckpt_cfg = config.get("checkpoint", {})
        self.ckpt_dir = Path(ckpt_cfg.get("dir", "./outputs/checkpoints"))
        self.ckpt_file = self.ckpt_dir / "methodology_analyst.json"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, reading_notes: list[ReadingNotes]) -> MethodAnalysis:
        """Group reading notes by chapter tags, call DeepSeek, and return analysis."""

        logger.info(
            "MethodologyAnalyst: starting analysis with %d reading notes", len(reading_notes)
        )

        if not reading_notes:
            logger.warning("No reading notes provided — returning empty MethodAnalysis")
            return MethodAnalysis(raw_analysis="")

        # 1. Group by chapter_tags (just informational for the prompt)
        chapter_groups: dict[str, list[ReadingNotes]] = defaultdict(list)
        for note in reading_notes:
            tags = note.chapter_tags or ["untagged"]
            for tag in tags:
                chapter_groups[tag].append(note)

        logger.info(
            "Grouped notes into %d chapter tag groups: %s",
            len(chapter_groups),
            list(chapter_groups.keys()),
        )

        # Save checkpoint: running
        self.save_checkpoint(
            AgentCheckpoint(
                agent_name="methodology_analyst",
                status="running",
                phase="build_prompt",
                progress=0.2,
                timestamp=datetime.now().isoformat(),
            )
        )

        # 2. Build prompt
        prompt = self._build_analysis_prompt(reading_notes)
        logger.info("Prompt built (%d chars)", len(prompt))

        # Save checkpoint: calling LLM
        self.save_checkpoint(
            AgentCheckpoint(
                agent_name="methodology_analyst",
                status="running",
                phase="call_llm",
                progress=0.4,
                timestamp=datetime.now().isoformat(),
            )
        )

        # 3. Call LLM
        try:
            raw_response = self._call_llm(prompt, max_tokens=self.max_tokens)
        except (APIError, APIConnectionError, RateLimitError) as exc:
            logger.error("DeepSeek API error: %s", exc)
            self.save_checkpoint(
                AgentCheckpoint(
                    agent_name="methodology_analyst",
                    status="failed",
                    phase="call_llm",
                    progress=0.5,
                    error=str(exc),
                    timestamp=datetime.now().isoformat(),
                )
            )
            return MethodAnalysis(raw_analysis=f"ERROR: {exc}")
        except Exception as exc:
            logger.error("Unexpected error calling LLM: %s", exc)
            self.save_checkpoint(
                AgentCheckpoint(
                    agent_name="methodology_analyst",
                    status="failed",
                    phase="call_llm",
                    progress=0.5,
                    error=str(exc),
                    timestamp=datetime.now().isoformat(),
                )
            )
            return MethodAnalysis(raw_analysis=f"ERROR: {exc}")

        logger.info("Received LLM response (%d chars)", len(raw_response))

        # Save checkpoint: parsing
        self.save_checkpoint(
            AgentCheckpoint(
                agent_name="methodology_analyst",
                status="running",
                phase="parse_response",
                progress=0.8,
                timestamp=datetime.now().isoformat(),
            )
        )

        # 4. Parse response
        analysis = self._parse_response(raw_response)

        # Save checkpoint: completed
        self.save_checkpoint(
            AgentCheckpoint(
                agent_name="methodology_analyst",
                status="completed",
                phase="done",
                progress=1.0,
                timestamp=datetime.now().isoformat(),
            )
        )

        logger.info(
            "MethodologyAnalyst: done — taxonomy=%d entries, %d evolution chains, %d chapter mappings",
            len(analysis.taxonomy),
            len(analysis.evolution_chains),
            len(analysis.chapter_mapping),
        )
        return analysis

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------

    def _build_analysis_prompt(self, notes: list[ReadingNotes]) -> str:
        """Build a comprehensive prompt for taxonomy + evolution analysis.

        The prompt includes all reading notes (truncated if too large) and asks
        the LLM to produce a JSON response with taxonomy, evolution_chains, and
        chapter_mapping.
        """

        # Serialize each note into a compact text block
        note_blocks: list[str] = []
        for i, note in enumerate(notes, 1):
            block_lines = [
                f"--- Paper {i}: {note.title} (id: {note.paper_id}) ---",
                f"Chapter tags: {', '.join(note.chapter_tags) if note.chapter_tags else 'none'}",
                f"Key contributions: {'; '.join(note.key_contributions) if note.key_contributions else 'N/A'}",
                f"Methodology: {note.methodology_summary or 'N/A'}",
                f"Experimental results: {note.experimental_results or 'N/A'}",
                f"Limitations: {note.limitations or 'N/A'}",
                f"Relevance: {note.relevance_to_survey or 'N/A'}",
            ]
            note_blocks.append("\n".join(block_lines))

        notes_text = "\n\n".join(note_blocks)

        # Truncate if too large
        if len(notes_text) > _MAX_NOTES_CHARS:
            logger.warning(
                "Notes text (%d chars) exceeds limit (%d); truncating",
                len(notes_text),
                _MAX_NOTES_CHARS,
            )
            notes_text = notes_text[:_MAX_NOTES_CHARS] + "\n\n[... TRUNCATED ...]"

        # Build the analysis scope instructions
        scope_instructions = ""
        if self.analysis_scope:
            scope_instructions = (
                "Your analysis should cover the following aspects:\n"
                + "\n".join(f"- {s}" for s in self.analysis_scope)
                + "\n\n"
            )

        prompt = f"""You are an expert research methodology analyst specializing in robotics and
machine learning. You are analyzing papers for a survey on deformable object
dexterous grasping.

{scope_instructions}Below are structured reading notes for {len(notes)} papers. Your task:

1. **Taxonomy**: Classify each paper's primary method into one of these categories:
   - `non_rl` — classical / non-reinforcement-learning methods (e.g., analytical planning, optimization, heuristic)
   - `deep_rl` — deep reinforcement learning methods (PPO, SAC, TD3, model-based RL, etc.)
   - `hybrid` — methods combining RL with non-RL techniques (RL + model predictive control, RL + classical planning)
   - `foundation_model` — methods leveraging large pre-trained / foundation models (diffusion policies, vision-language models, GPT-based planners)

   For each paper, also identify a subcategory (e.g., "actor_critic", "mbrl", "diffusion_policy", "grasp_planning", etc.)

2. **Evolution chains**: Identify which methods built upon or extended earlier ones.
   Describe each chain as: "MethodA (Paper X, year) → MethodB (Paper Y, year) → ..."

3. **Chapter mapping**: Map each method name to one or more chapter tags it belongs to.

## Reading Notes

{notes_text}

## Required Output Format

Return ONLY a valid JSON object (no markdown fences, no extra text) with these fields:

{{
  "taxonomy": [
    {{
      "method_name": "short descriptive name",
      "category": "non_rl|deep_rl|hybrid|foundation_model",
      "subcategory": "string",
      "representative_papers": ["paper_id_1", "paper_id_2"],
      "key_technique": "brief description of the core technique",
      "evolution_notes": "how this method relates to predecessors or successors"
    }}
  ],
  "evolution_chains": [
    "MethodA (PaperID, year) → MethodB (PaperID, year) → ..."
  ],
  "chapter_mapping": {{
    "chapter_tag_or_chapter_id": ["method_name_1", "method_name_2"]
  }}
}}

Ensure every paper from the reading notes appears at least once in the taxonomy.
"""
        return prompt

    # ------------------------------------------------------------------
    # LLM call
    # ------------------------------------------------------------------

    def _call_llm(self, prompt: str, max_tokens: int = 8192) -> str:
        """Call DeepSeek V4 Pro via the OpenAI-compatible API."""

        logger.info(
            "Calling DeepSeek model=%s max_tokens=%d temperature=%.2f",
            self.model,
            max_tokens,
            self.temperature,
        )

        # DeepSeek reasoning_effort: pass as extra body param if supported
        extra_body: dict = {}
        if self.reasoning_effort:
            extra_body["reasoning_effort"] = self.reasoning_effort

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a precise research methodology analyst. "
                        "Always respond with valid JSON only — no markdown fences, "
                        "no commentary outside the JSON object."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            max_tokens=max_tokens,
            temperature=self.temperature,
            extra_body=extra_body if extra_body else None,
        )

        content = response.choices[0].message.content or ""
        return content

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    def _parse_response(self, response: str) -> MethodAnalysis:
        """Parse the LLM's JSON response into a MethodAnalysis dataclass."""

        # Strip markdown fences if the model added them anyway
        cleaned = response.strip()
        if cleaned.startswith("```"):
            # Remove opening ```json or ```
            first_newline = cleaned.index("\n")
            cleaned = cleaned[first_newline + 1 :]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

        # Try to find JSON object boundaries
        json_start = cleaned.find("{")
        json_end = cleaned.rfind("}")
        if json_start == -1 or json_end == -1 or json_end <= json_start:
            logger.error("No JSON object found in LLM response")
            return MethodAnalysis(raw_analysis=response)

        json_str = cleaned[json_start : json_end + 1]

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as exc:
            logger.error("JSON parse error: %s — returning raw analysis", exc)
            return MethodAnalysis(raw_analysis=response)

        # Build taxonomy
        taxonomy: list[MethodEntry] = []
        for item in data.get("taxonomy", []):
            try:
                entry = MethodEntry(
                    method_name=item.get("method_name", "unknown"),
                    category=item.get("category", "unknown"),
                    subcategory=item.get("subcategory", ""),
                    representative_papers=item.get("representative_papers", []),
                    key_technique=item.get("key_technique", ""),
                    evolution_notes=item.get("evolution_notes", ""),
                )
                taxonomy.append(entry)
            except Exception as exc:
                logger.warning("Skipping malformed taxonomy entry: %s — %s", item, exc)

        # Build evolution chains
        evolution_chains: list[str] = data.get("evolution_chains", [])

        # Build chapter mapping
        chapter_mapping: dict[str, list[str]] = data.get("chapter_mapping", {})

        analysis = MethodAnalysis(
            taxonomy=taxonomy,
            evolution_chains=evolution_chains,
            chapter_mapping=chapter_mapping,
            raw_analysis=response,
        )

        logger.info(
            "Parsed MethodAnalysis: %d taxonomy entries, %d evolution chains, %d chapter mappings",
            len(taxonomy),
            len(evolution_chains),
            len(chapter_mapping),
        )
        return analysis

    # ------------------------------------------------------------------
    # Checkpoint helpers
    # ------------------------------------------------------------------

    def save_checkpoint(self, checkpoint: AgentCheckpoint) -> None:
        """Persist checkpoint to disk."""
        self.ckpt_dir.mkdir(parents=True, exist_ok=True)
        checkpoint.save(self.ckpt_file)
        logger.debug("Checkpoint saved: %s (status=%s)", self.ckpt_file, checkpoint.status)

    def load_checkpoint(self) -> AgentCheckpoint:
        """Load checkpoint from disk, or return a fresh one if not found."""
        if not self.ckpt_file.exists():
            logger.info("No checkpoint found at %s — starting fresh", self.ckpt_file)
            return AgentCheckpoint(agent_name="methodology_analyst")
        ckpt = AgentCheckpoint.load(self.ckpt_file)
        logger.info("Loaded checkpoint: status=%s progress=%.1f", ckpt.status, ckpt.progress)
        return ckpt
