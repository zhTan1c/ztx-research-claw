#!/usr/bin/env python3
"""
ztx-research-claw main entry point.
Orchestrates all 7 agents in sequence with checkpoint support.
"""

import argparse
import asyncio
import logging
import os
import re
import signal
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

from agents import (
    CitationFormatter,
    LiteratureSearcher,
    MethodologyAnalyst,
    OutlineParser,
    PDFDownloader,
    PaperReader,
    Writer,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Phase registry: name, checkpoint key, agent class
# ---------------------------------------------------------------------------
PHASES = [
    {"name": "outline_parser",        "checkpoint_key": "outline_parser",        "label": "解析综述框架"},
    {"name": "literature_searcher",   "checkpoint_key": "literature_searcher",   "label": "文献检索"},
    {"name": "pdf_downloader",        "checkpoint_key": "pdf_downloader",        "label": "PDF下载"},
    {"name": "paper_reader",          "checkpoint_key": "paper_reader",          "label": "论文阅读"},
    {"name": "methodology_analyst",   "checkpoint_key": "methodology_analyst",   "label": "方法演进分析"},
    {"name": "writer",                "checkpoint_key": "writer",                "label": "章节写作"},
    {"name": "citation_formatter",    "checkpoint_key": "citation_formatter",    "label": "引用格式化"},
]


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def resolve_env_vars(obj):
    """Recursively resolve ${VAR} placeholders in nested dict/list/str."""
    if isinstance(obj, str):
        def _replace(match):
            var_name = match.group(1)
            return os.environ.get(var_name, match.group(0))
        return re.sub(r'\$\{(\w+)\}', _replace, obj)
    elif isinstance(obj, dict):
        return {k: resolve_env_vars(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [resolve_env_vars(item) for item in obj]
    return obj


def load_config(config_path: str = "config.yaml", dotenv_path: str | None = None) -> dict:
    """Read config.yaml, load .env, resolve ${VAR} placeholders."""
    # Load .env first
    if dotenv_path and Path(dotenv_path).is_file():
        load_dotenv(dotenv_path, override=True)
    else:
        load_dotenv(override=True)  # default .env in cwd

    with open(config_path, "r", encoding="utf-8") as f:
        raw_config = yaml.safe_load(f)

    # Resolve env vars throughout config
    config = resolve_env_vars(raw_config) if raw_config else {}

    # Re-load with explicit dotenv path from config if present
    proj_dotenv = config.get("project", {}).get("dotenv_path")
    if proj_dotenv and Path(proj_dotenv).is_file():
        load_dotenv(proj_dotenv, override=True)
        # Re-resolve after loading project-specific .env
        config = resolve_env_vars(raw_config)

    return config


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging(log_cfg: dict):
    """Configure logging from config['logging'] section."""
    level = getattr(logging, log_cfg.get("level", "INFO").upper(), logging.INFO)
    fmt = log_cfg.get("format", "%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    formatter = logging.Formatter(fmt)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # File handler (if configured)
    log_file = log_cfg.get("file")
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)


# ---------------------------------------------------------------------------
# Checkpoint helpers
# ---------------------------------------------------------------------------

def load_checkpoint(checkpoint_path: Path) -> dict:
    """Load checkpoint file if it exists, return dict (empty if missing)."""
    if checkpoint_path.is_file():
        import pickle
        try:
            with open(checkpoint_path, "rb") as f:
                return pickle.load(f)
        except Exception as exc:
            logger.warning("Failed to load checkpoint (%s), starting fresh: %s", exc, checkpoint_path)
    return {}


def checkpoint_has_phase(checkpoint: dict, phase_key: str) -> bool:
    """Return True if the given phase has a saved checkpoint."""
    return phase_key in checkpoint and checkpoint[phase_key] is not None


def save_checkpoint(checkpoint: dict, checkpoint_path: Path):
    """Persist checkpoint dict to disk."""
    import pickle
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    with open(checkpoint_path, "wb") as f:
        pickle.dump(checkpoint, f)


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def save_outputs(drafts, bib_content: str, config: dict):
    """Save final outputs: per-chapter drafts, concatenated final, and .bib."""
    output_dir = Path(config["project"]["output_dir"])
    draft_dir = Path(config["project"].get("draft_dir", output_dir / "drafts"))
    bib_file = Path(config["project"].get("bib_file", output_dir / "references.bib"))

    draft_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Save individual chapter drafts
    for draft in drafts:
        chapter_id = getattr(draft, "chapter_id", None) or getattr(draft, "id", "unknown")
        chapter_path = draft_dir / f"{chapter_id}.md"
        content = getattr(draft, "content", None) or getattr(draft, "text", "")
        with open(chapter_path, "w", encoding="utf-8") as f:
            f.write(content)
        logger.info("Saved chapter draft: %s", chapter_path)

    # Save concatenated final polished document
    final_path = output_dir / "final_polished.md"
    all_content = []
    for draft in drafts:
        content = getattr(draft, "content", None) or getattr(draft, "text", "")
        all_content.append(content)
    with open(final_path, "w", encoding="utf-8") as f:
        f.write("\n\n---\n\n".join(all_content))
    logger.info("Saved final polished document: %s", final_path)

    # Save bibliography
    bib_file.parent.mkdir(parents=True, exist_ok=True)
    with open(bib_file, "w", encoding="utf-8") as f:
        f.write(bib_content)
    logger.info("Saved bibliography: %s", bib_file)

    print(f"\n[输出] 章节草稿已保存到: {draft_dir}")
    print(f"[输出] 最终文档已保存到: {final_path}")
    print(f"[输出] 参考文献已保存到: {bib_file}")


# ---------------------------------------------------------------------------
# Phase progress printer
# ---------------------------------------------------------------------------

def print_phase(phase_num: int, total: int, label: str):
    """Print a visible phase progress line to stdout."""
    bar_len = 30
    filled = int(bar_len * phase_num / total)
    bar = "█" * filled + "░" * (bar_len - filled)
    print(f"\n{'='*60}")
    print(f"  Phase {phase_num}/{total}: {label}")
    print(f"  [{bar}]")
    print(f"{'='*60}\n")


# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------

_shutdown_event = asyncio.Event() if hasattr(asyncio, "Event") else None


def _handle_sigint(sig, frame):
    """Handle Ctrl+C gracefully."""
    print("\n\n⚠️  收到中断信号 (Ctrl+C)，正在优雅退出...")
    print("   当前进度已保存到 checkpoint 文件，下次运行可使用 --resume 恢复。\n")
    sys.exit(130)


signal.signal(signal.SIGINT, _handle_sigint)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    parser = argparse.ArgumentParser(
        description="ztx-research-claw: 综述研究自动化管线"
    )
    parser.add_argument(
        "--resume", action="store_true", default=False,
        help="从 checkpoint 恢复已完成的阶段"
    )
    parser.add_argument(
        "--config", type=str, default="config.yaml",
        help="配置文件路径 (默认: config.yaml)"
    )
    args = parser.parse_args()

    # --- 1. Load config ---
    config = load_config(args.config)
    print(f"[配置] 已加载配置文件: {args.config}")

    # --- 2. Setup logging ---
    setup_logging(config.get("logging", {}))
    logger.info("ztx-research-claw 启动")

    # --- Resolve paths ---
    output_dir = Path(config["project"]["output_dir"])
    pdf_dir = Path(config["project"].get("pdf_dir", output_dir / "pdfs"))
    draft_dir = Path(config["project"].get("draft_dir", output_dir / "drafts"))
    bib_path = Path(config["project"].get("bib_file", output_dir / "references.bib"))
    # Agents expect str, not Path
    pdf_dir_str = str(pdf_dir)
    bib_path_str = str(bib_path)
    checkpoint_path = Path(config.get("checkpoint", {}).get("path", output_dir / "checkpoint.pkl"))

    # Ensure output directories exist
    for d in (output_dir, pdf_dir, draft_dir):
        d.mkdir(parents=True, exist_ok=True)

    # --- Load checkpoint if resuming ---
    checkpoint = {}
    if args.resume:
        checkpoint = load_checkpoint(checkpoint_path)
        if checkpoint:
            print(f"[检查点] 已加载 checkpoint: {checkpoint_path}")
            completed = [k for k, v in checkpoint.items() if v is not None]
            print(f"[检查点] 已完成的阶段: {', '.join(completed) if completed else '(无)'}")
        else:
            print("[检查点] 未找到有效的 checkpoint，从头开始运行。")

    # --- Phase 1: Parse outline ---
    print_phase(1, 7, "解析综述框架")
    if args.resume and checkpoint_has_phase(checkpoint, "outline_parser"):
        logger.info("Phase 1: 从 checkpoint 恢复 outline_parser")
        print("[跳过] outline_parser 已完成，从 checkpoint 恢复。")
        chapters = checkpoint["outline_parser"]
    else:
        logger.info("Phase 1: 解析综述框架...")
        outline_parser = OutlineParser(config)
        chapters = outline_parser.run()
        checkpoint["outline_parser"] = chapters
        save_checkpoint(checkpoint, checkpoint_path)
        logger.info("Phase 1 完成，checkpoint 已保存。")

    # --- Phase 2: Search literature (async) ---
    print_phase(2, 7, "文献检索")
    if args.resume and checkpoint_has_phase(checkpoint, "literature_searcher"):
        logger.info("Phase 2: 从 checkpoint 恢复 literature_searcher")
        print("[跳过] literature_searcher 已完成，从 checkpoint 恢复。")
        papers = checkpoint["literature_searcher"]
    else:
        logger.info("Phase 2: 文献检索...")
        searcher = LiteratureSearcher(config)
        seed_file = config.get("agents", {}).get("literature_searcher", {}).get("seed_papers_file")
        papers = await searcher.run(chapters, seed_papers_file=seed_file)
        checkpoint["literature_searcher"] = papers
        save_checkpoint(checkpoint, checkpoint_path)
        logger.info("Phase 2 完成，checkpoint 已保存。")

    # --- Phase 3: Download PDFs (async) ---
    print_phase(3, 7, "PDF下载")
    if args.resume and checkpoint_has_phase(checkpoint, "pdf_downloader"):
        logger.info("Phase 3: 从 checkpoint 恢复 pdf_downloader")
        print("[跳过] pdf_downloader 已完成，从 checkpoint 恢复。")
        papers = checkpoint["pdf_downloader"]
    else:
        logger.info("Phase 3: PDF下载...")
        downloader = PDFDownloader(config)
        papers = await downloader.run(papers, pdf_dir_str)
        checkpoint["pdf_downloader"] = papers
        save_checkpoint(checkpoint, checkpoint_path)
        logger.info("Phase 3 完成，checkpoint 已保存。")

    # --- Phase 4: Read papers ---
    print_phase(4, 7, "论文阅读")
    if args.resume and checkpoint_has_phase(checkpoint, "paper_reader"):
        logger.info("Phase 4: 从 checkpoint 恢复 paper_reader")
        print("[跳过] paper_reader 已完成，从 checkpoint 恢复。")
        reading_notes = checkpoint["paper_reader"]
    else:
        logger.info("Phase 4: 论文阅读...")
        reader = PaperReader(config)
        reading_notes = reader.run(papers, mode="tiered")
        checkpoint["paper_reader"] = reading_notes
        save_checkpoint(checkpoint, checkpoint_path)
        logger.info("Phase 4 完成，checkpoint 已保存。")

    # --- Phase 5: Analyze methodology ---
    print_phase(5, 7, "方法演进分析")
    if args.resume and checkpoint_has_phase(checkpoint, "methodology_analyst"):
        logger.info("Phase 5: 从 checkpoint 恢复 methodology_analyst")
        print("[跳过] methodology_analyst 已完成，从 checkpoint 恢复。")
        method_analysis = checkpoint["methodology_analyst"]
    else:
        logger.info("Phase 5: 方法演进分析...")
        analyst = MethodologyAnalyst(config)
        method_analysis = analyst.run(reading_notes)
        checkpoint["methodology_analyst"] = method_analysis
        save_checkpoint(checkpoint, checkpoint_path)
        logger.info("Phase 5 完成，checkpoint 已保存。")

    # --- Phase 6: Write chapters ---
    print_phase(6, 7, "章节写作")
    if args.resume and checkpoint_has_phase(checkpoint, "writer"):
        logger.info("Phase 6: 从 checkpoint 恢复 writer")
        print("[跳过] writer 已完成，从 checkpoint 恢复。")
        drafts = checkpoint["writer"]
    else:
        logger.info("Phase 6: 章节写作...")
        writer = Writer(config)
        drafts = writer.run(chapters, reading_notes, method_analysis)
        checkpoint["writer"] = drafts
        save_checkpoint(checkpoint, checkpoint_path)
        logger.info("Phase 6 完成，checkpoint 已保存。")

    # --- Phase 7: Format citations ---
    print_phase(7, 7, "引用格式化")
    if args.resume and checkpoint_has_phase(checkpoint, "citation_formatter"):
        logger.info("Phase 7: 从 checkpoint 恢复 citation_formatter")
        print("[跳过] citation_formatter 已完成，从 checkpoint 恢复。")
        final_drafts, bib_content = checkpoint["citation_formatter"]
    else:
        logger.info("Phase 7: 引用格式化...")
        formatter = CitationFormatter(config)
        final_drafts, bib_content = formatter.run(drafts, papers, bib_path_str)
        checkpoint["citation_formatter"] = (final_drafts, bib_content)
        save_checkpoint(checkpoint, checkpoint_path)
        logger.info("Phase 7 完成，checkpoint 已保存。")

    # --- Save final outputs ---
    print("\n" + "=" * 60)
    print("  所有阶段完成！正在保存最终输出...")
    print("=" * 60 + "\n")

    save_outputs(final_drafts, bib_content, config)

    # Mark pipeline as complete
    checkpoint["_pipeline_complete"] = True
    save_checkpoint(checkpoint, checkpoint_path)

    logger.info("ztx-research-claw 管线全部完成。")
    print("\n✅ 综述研究管线执行完毕！\n")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n⚠️  管线被用户中断。下次运行可使用 --resume 恢复。\n")
        sys.exit(130)
    except Exception as exc:
        logger.exception("管线执行失败: %s", exc)
        print(f"\n❌ 管线执行失败: {exc}")
        print("   详细错误请查看日志文件。\n")
        sys.exit(1)
