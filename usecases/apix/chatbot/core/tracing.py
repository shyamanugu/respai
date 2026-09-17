"""
chatbot/core/tracing.py — Lightweight, in-process span/trace instrumentation.

Gives the LangGraph orchestrator OpenTelemetry-style observability (one trace
per request, one span per node, one event per edge) **without** any network
exporter — everything is emitted to the standard logger, so it adds no
measurable latency to the request path.

Usage
-----
    trace = Trace("chat", user_id=42)
    with trace.span("classify") as sp:
        intent = classify(...)
        sp["intent"] = intent          # attach attributes to the span
    trace.edge("classify", "scope", reason="on_topic")
    ...
    log.info(trace.summary())          # one-line waterfall at the end

Every span/edge is timestamped and correlated by a short ``trace_id`` so a full
request can be reconstructed from the logs (grep the id).
"""

from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

from chatbot.core.config import get_logger

# Dedicated logger so traces can be filtered/forwarded independently.
log = get_logger("chatbot.trace")


def _fmt_attrs(attrs: dict[str, Any]) -> str:
    """Render span attributes as a compact ``k=v`` string for logs."""
    if not attrs:
        return ""
    parts = []
    for k, v in attrs.items():
        if isinstance(v, str) and len(v) > 80:
            v = v[:77] + "..."
        parts.append(f"{k}={v}")
    return " ".join(parts)


class Trace:
    """A single request trace holding an ordered list of spans and edges."""

    __slots__ = ("trace_id", "name", "t0", "events")

    def __init__(self, name: str, **attrs: Any) -> None:
        self.trace_id: str = uuid.uuid4().hex[:12]
        self.name = name
        self.t0 = time.perf_counter()
        # Ordered record of every span/edge for the waterfall summary.
        self.events: list[dict[str, Any]] = []
        log.info("TRACE START id=%s name=%s %s", self.trace_id, name, _fmt_attrs(attrs))

    # ── Spans (nodes) ──────────────────────────────────────────────────────
    @contextmanager
    def span(self, name: str, **attrs: Any) -> Iterator[dict[str, Any]]:
        """Time a unit of work; yields a mutable attrs dict to enrich the span.

        Records duration and status (ok / error) and logs START/END lines.
        Exceptions are logged, marked on the span, and re-raised unchanged.
        """
        sp: dict[str, Any] = dict(attrs)
        start = time.perf_counter()
        log.info("SPAN  START id=%s node=%s %s", self.trace_id, name, _fmt_attrs(sp))
        status = "ok"
        try:
            yield sp
        except Exception as exc:  # noqa: BLE001 — record then re-raise
            status = "error"
            sp["error"] = type(exc).__name__
            raise
        finally:
            dur_ms = (time.perf_counter() - start) * 1000.0
            rec = {"kind": "span", "node": name, "status": status, "ms": round(dur_ms, 1)}
            rec.update(sp)
            self.events.append(rec)
            log.info(
                "SPAN  END   id=%s node=%s status=%s dur_ms=%.1f %s",
                self.trace_id, name, status, dur_ms, _fmt_attrs(sp),
            )

    # ── Edges (transitions) ────────────────────────────────────────────────
    def edge(self, src: str, dst: str, reason: str = "") -> str:
        """Record (and log) a routing decision between two nodes.

        Returns *dst* so it can be used directly as a LangGraph router result::

            return state["trace"].edge("cache", "rewrite_route", "miss")
        """
        self.events.append({"kind": "edge", "src": src, "dst": dst, "reason": reason})
        log.info("EDGE  id=%s %s -> %s (%s)", self.trace_id, src, dst, reason)
        return dst

    # ── Summary ────────────────────────────────────────────────────────────
    def elapsed_ms(self) -> int:
        """Total wall-clock time since the trace started, in milliseconds."""
        return int((time.perf_counter() - self.t0) * 1000)

    def summary(self) -> str:
        """One-line waterfall of the whole trace for the final log entry."""
        steps: list[str] = []
        for e in self.events:
            if e["kind"] == "span":
                flag = "" if e["status"] == "ok" else "!"
                steps.append(f"{e['node']}{flag}({e['ms']}ms)")
            else:
                steps.append(f"→{e['dst']}")
        return (
            f"TRACE END   id={self.trace_id} name={self.name} "
            f"total_ms={self.elapsed_ms()} | " + " ".join(steps)
        )
