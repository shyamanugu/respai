"""AI Pipeline Orchestrator.

Usage
-----
    # Single program mode (existing)
    python -m ai_pipeline.main --program telesales
    python -m ai_pipeline.main --program telesales --date 2025-08-28
    python -m ai_pipeline.main --program telesales --step denoise

    # Mode-based (filters by program names from .env)
    python -m ai_pipeline.main --mode telesales
    python -m ai_pipeline.main --mode wcc --date 2025-08-28
    python -m ai_pipeline.main --mode "telesales|wcc" --step analysis

    # Date range
    python -m ai_pipeline.main --program telesales --start 2025-08-01 --end 2025-08-07 --step analysis
    python -m ai_pipeline.main --mode wcc --start 2025-08-01 --end 2025-08-07

Steps
-----
    denoise   → Transcription cleanup via LLM
    analysis  → Per-call evaluation via LLM (structured output)
    summary   → Weekly per-employee aggregation + LLM reflection
    kpi       → Post-summary KPI aggregation → CSV upload
"""

from __future__ import annotations

import argparse
import asyncio
import uuid
import sys
from datetime import date, datetime, timedelta, timezone

from ai_pipeline.programs_config import load_program_config, load_mode_config
from ai_pipeline.logging_config import setup_logging, get_logger
from ai_pipeline.services.storage import StorageService
from ai_pipeline.steps import run_denoise, run_analysis, run_summary, run_individual_metrics
from ai_pipeline.steps.kpi_aggregator import run_kpi_aggregator

STEPS = {
    "denoise": run_denoise,
    "analysis": run_analysis,
    "summary": run_summary,
    "individual_metrics": run_individual_metrics,
    "kpi": run_kpi_aggregator,
}
STEP_ORDER = ["denoise", "analysis", "summary", "individual_metrics", "kpi"]


def _date_range(start: date, end: date) -> list[date]:
    """Return an inclusive list of dates from *start* to *end*."""
    days = []
    d = start
    while d <= end:
        days.append(d)
        d += timedelta(days=1)
    return days


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="AI Pipeline")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--program", default=None, help="Program config id (e.g. telesales, wcc)")
    group.add_argument("--mode", default=None, help="Processing mode(s) — filters by mapped programs from .env. Use | to combine (e.g. telesales|wcc)")
    parser.add_argument("--date", default=None, help="Processing date YYYY-MM-DD (default: today UTC)")
    parser.add_argument("--start", default=None, help="Start date YYYY-MM-DD (inclusive) for date-range mode")
    parser.add_argument("--end", default=None, help="End date YYYY-MM-DD (inclusive) for date-range mode")
    parser.add_argument("--step", default=None, choices=STEP_ORDER, help="Run a single step instead of the full pipeline")
    parser.add_argument("--coach", default=None, help="CoachID allow-list: single (9040400), multiple comma-separated (9040400,3000510), or 'all' for every coach")
    parser.add_argument("--agent", default=None, help="EmployeeID allow-list (for datasets without CoachID): single, comma-separated, or 'all'")
    parser.add_argument("--log-level", default="INFO", help="Logging level")
    return parser.parse_args(argv)


async def run_pipeline(args):
    run_id = uuid.uuid4().hex[:8]
    setup_logging(level=args.log_level, pipeline_run_id=run_id)
    logger = get_logger("main")

    logger.info("Pipeline starting | program=%s mode=%s run_id=%s", args.program, args.mode, run_id)

    # ── LLMOps: init tracer + run attribution (fail-open) ────────────────────
    try:
        from ai_pipeline import llmops

        llmops.init_tracer()
        llmops.set_run_context(run_id, args.program or args.mode or "")
        logger.info(
            "LLMOps: platform=%s tracer=%s env=%s",
            "available" if llmops.PLATFORM_AVAILABLE else "absent (fail-open)",
            __import__("os").environ.get("LLMOPS_TRACER", "jsonl"),
            llmops.current_env(),
        )
    except Exception:
        llmops = None

    if args.mode:
        cfg = load_mode_config(args.mode)
    else:
        cfg = load_program_config(args.program)

    # CLI coach filter overrides the env-derived default when provided.
    # Accept "all"/"*" (case-insensitive) to explicitly process every coach.
    if args.coach:
        if args.coach.strip().lower() in ("all", "*"):
            cfg.coach_filter = None
            logger.info("Coach filter: ALL coaches (no CoachID restriction)")
        else:
            cfg.coach_filter = [int(c.strip()) for c in args.coach.split(",") if c.strip()]
            logger.info("Coach filter set from CLI: %s", cfg.coach_filter)
    if args.agent:
        if args.agent.strip().lower() in ("all", "*"):
            cfg.agent_filter = None
            logger.info("Agent filter: ALL agents (no EmployeeID restriction)")
        else:
            cfg.agent_filter = [int(a.strip()) for a in args.agent.split(",") if a.strip()]
            logger.info("Agent filter set from CLI: %s", cfg.agent_filter)

    storage = StorageService(cfg.storage)

    # Determine date(s) to process
    if args.start and args.end:
        start = datetime.strptime(args.start, "%Y-%m-%d").date()
        end = datetime.strptime(args.end, "%Y-%m-%d").date()
        dates = _date_range(start, end)
        logger.info("Date range mode: %s -> %s (%d days)", start, end, len(dates))
    elif args.date:
        dates = [datetime.strptime(args.date, "%Y-%m-%d").date()]
    else:
        dates = [datetime.now(timezone.utc).date()]

    steps_to_run = [args.step] if args.step else STEP_ORDER
    total = len(dates)
    processed: list[str] = []
    skipped: list[str] = []
    failed: list[str] = []

    for i, date_utc in enumerate(dates, 1):
        tag = f"[{i}/{total}] {date_utc}"
        logger.info("%s | START (steps: %s)", tag, ", ".join(steps_to_run))
        try:
            for step_name in steps_to_run:
                logger.info("%s | step '%s' running...", tag, step_name)
                if llmops is not None:
                    llmops.set_step_context(step_name)
                await STEPS[step_name](date_utc, cfg, storage)
                logger.info("%s | step '%s' done", tag, step_name)
            processed.append(str(date_utc))
            logger.info("%s | DONE", tag)
        except FileNotFoundError as exc:
            skipped.append(str(date_utc))
            logger.warning("%s | SKIPPED (no source data): %s", tag, exc)
        except Exception:
            failed.append(str(date_utc))
            logger.exception("%s | FAILED", tag)

    # ── Final run summary ────────────────────────────────────────────────
    logger.info("===== RUN SUMMARY | program=%s mode=%s run_id=%s =====", args.program, args.mode, run_id)
    logger.info("Processed (%d/%d): %s", len(processed), total, ", ".join(processed) or "none")
    logger.info("Skipped   (%d/%d): %s", len(skipped), total, ", ".join(skipped) or "none")
    logger.info("Failed    (%d/%d): %s", len(failed), total, ", ".join(failed) or "none")
    if failed:
        logger.error("Completed with %d failed date(s) — see traceback(s) above.", len(failed))
    else:
        logger.info("Completed successfully.")

    # ── LLMOps: per-run LLM totals + PipelineEvent ───────────────────────────
    if llmops is not None:
        try:
            totals = llmops.run_totals()
            logger.info(
                "LLMOps totals | llm_calls=%d cost_usd=%.6f latency_ms=%.1f",
                totals.get("steps", 0), totals.get("cost", 0.0), totals.get("latency", 0.0),
            )
            llmops.emit_pipeline_event(run_id, pipeline_name=f"apix.ai_pipeline.{args.program or args.mode or 'run'}")
        except Exception:
            pass


def main():
    args = parse_args()
    asyncio.run(run_pipeline(args))


if __name__ == "__main__":
    main()
