"""
services/scoring.py — Performance scoring and risk identification
==================================================================
Config-driven scoring — reads weights, targets, and risk rules from
the active ProgramConfig so the same logic works across all programs.
"""

from typing import List, Optional

from backend.config.logging import get_logger

logger = get_logger(__name__)


def _get_program_config(program_config):
    """Lazy-load the default program config if none was supplied."""
    if program_config is not None:
        return program_config
    from backend.config.programs import load_program_config, DEFAULT_PROGRAM
    return load_program_config(DEFAULT_PROGRAM)


def _calculate_tiered_penalty(value: float, tiers: list) -> float:
    """Walk the penalty tiers and return the total penalty for *value*."""
    if not tiers:
        return 0

    penalty = 0.0
    for tier in tiers:
        tier_max = tier.get("max")  # None means unbounded
        if tier_max is not None and value <= tier_max:
            penalty = tier.get("penalty", 0) + tier.get("per_unit", 0) * value
            return penalty

    # Value exceeds all bounded tiers → use the last (unbounded) tier
    last = tiers[-1]
    base = last.get("base", 0)
    per_unit = last.get("per_unit", 0)
    prev_max = tiers[-2].get("max", 0) if len(tiers) >= 2 else 0
    penalty = base + per_unit * (value - prev_max)
    return penalty


def calculate_performance_score(report: dict, program_config=None) -> int:
    """
    Calculate overall performance score (0-100) using the program's
    scoring components.  Falls back to the default program config when
    *program_config* is ``None``.

    Scoring component types:
        quality  — raw 0-100 scores averaged & weighted
        target   — values normalised against targets, averaged & weighted
        trend    — universal delta-based score
        penalty  — tiered deduction for negative indicators
    """
    cfg = _get_program_config(program_config)
    kpis = report.get("kpis", [])
    if not kpis:
        return 0

    # Build per-source lookups so scoring KPIs can reference any blob field
    _source_lookups = {
        "kpis": {k.get("key", ""): k for k in kpis},
    }

    def _get_lookup(source: str) -> dict:
        """Return (or lazily build) a keyed lookup for *source*."""
        if source not in _source_lookups:
            raw = report.get(source, {})
            if isinstance(raw, list):
                _source_lookups[source] = {k.get("key", ""): k for k in raw}
            elif isinstance(raw, dict):
                _source_lookups[source] = {
                    k: {"key": k, "value": v} if not isinstance(v, dict) else v
                    for k, v in raw.items()
                }
            else:
                _source_lookups[source] = {}
        return _source_lookups[source]

    def _resolve_kpi_spec(spec) -> tuple:
        """Return (key, source, pre_scaled) from a kpi spec (str or dict)."""
        if isinstance(spec, dict):
            return spec.get("key", ""), spec.get("source", "kpis"), spec.get("pre_scaled", True)
        return spec, "kpis", True

    kpi_lookup = _get_lookup("kpis")          # default / backward compat

    # ----- If no scoring components configured, return a simple average -----
    if not cfg.has_scoring():
        values = [
            k.get("value", 0)
            for k in kpis
            if isinstance(k.get("value"), (int, float))
        ]
        return round(sum(values) / len(values)) if values else 0

    final_score = 0.0

    for comp in cfg.scoring_components:
        if comp.type == "quality":
            vals = []
            for spec in comp.kpis:
                k, src, pre_scaled = _resolve_kpi_spec(spec)
                lookup = _get_lookup(src)
                if k in lookup:
                    v = lookup[k].get("value", 0)
                    if isinstance(v, (int, float)):
                        if not pre_scaled:
                            v = v * 100
                        vals.append(v)
            if vals:
                final_score += (sum(vals) / len(vals)) * comp.weight

        elif comp.type == "target":
            normalised = []
            # Support both list-of-dicts (new) and dict (legacy) formats
            if isinstance(comp.targets, list):
                for t in comp.targets:
                    kpi_key = t["key"]
                    target_val = t["target"]
                    src = t.get("source", "kpis")
                    lookup = _get_lookup(src)
                    if kpi_key in lookup and target_val > 0:
                        v = lookup[kpi_key].get("value", 0)
                        if isinstance(v, (int, float)):
                            normalised.append(min(100, (v / target_val) * 100))
            else:
                src = comp.source or "kpis"
                lookup = _get_lookup(src)
                for kpi_key, target_val in comp.targets.items():
                    if kpi_key in lookup and target_val > 0:
                        v = lookup[kpi_key].get("value", 0)
                        if isinstance(v, (int, float)):
                            normalised.append(min(100, (v / target_val) * 100))
            if normalised:
                final_score += (sum(normalised) / len(normalised)) * comp.weight

        elif comp.type == "trend":
            src = comp.source or "kpis"
            trend_kpis = report.get(src, kpis)
            if isinstance(trend_kpis, dict):
                trend_kpis = [{"key": k, **(v if isinstance(v, dict) else {"value": v})}
                              for k, v in trend_kpis.items()]
            trend_score = 0.0
            for kpi in trend_kpis:
                delta = kpi.get("delta", 0)
                delta = delta if isinstance(delta, (int, float)) else 0
                if delta > 0:
                    trend_score += min(delta * 5, 20)
                elif delta < 0:
                    trend_score += max(delta * 3, -15)
            trend_norm = ((trend_score + 100) / 200) * 100 * comp.weight
            final_score += max(0, min(comp.weight * 100, trend_norm))

        elif comp.type == "penalty":
            quality_score = 100.0
            for spec in comp.kpis:
                k, src, pre_scaled = _resolve_kpi_spec(spec)
                lookup = _get_lookup(src)
                if k in lookup:
                    v = lookup[k].get("value", 0)
                    if isinstance(v, (int, float)):
                        if not pre_scaled:
                            v = v * 100
                        penalty = _calculate_tiered_penalty(v, comp.tiers)
                        quality_score = max(0, quality_score - penalty)
            final_score += quality_score * comp.weight

    result = max(0, min(100, round(final_score)))
    logger.debug("Performance score calculated: %d", result)
    return result


def identify_risk_areas(report: dict, program_config=None) -> List[dict]:
    """
    Identify areas needing attention using the program's risk rules.

    Each rule is evaluated against every KPI in the report.  Rules with
    an empty ``kpis`` list (like ``delta_decline``) apply to *all* KPIs.
    """
    cfg = _get_program_config(program_config)
    risks: List[dict] = []
    kpis = report.get("kpis", [])

    if not cfg.risk_rules:
        # No rules configured — apply universal delta-decline default
        for kpi in kpis:
            delta = kpi.get("delta", 0)
            delta = delta if isinstance(delta, (int, float)) else 0
            if delta < -3:
                label = kpi.get("label", "Unknown")
                value = kpi.get("value", 0)
                severity = "critical" if delta < -5 else "high"
                risks.append({
                    "area": label,
                    "severity": severity,
                    "value": value,
                    "delta": delta,
                    "reason": f"Declining trend: {delta} from previous period",
                })
        logger.debug("Identified %d risk areas (default rules)", len(risks))
        return risks

    for kpi in kpis:
        key = kpi.get("key", "")
        label = kpi.get("label", "Unknown")
        value = kpi.get("value", 0)
        delta = kpi.get("delta", 0)
        delta = delta if isinstance(delta, (int, float)) else 0

        for rule in cfg.risk_rules:
            # Skip rules that target specific KPIs if this KPI isn't listed
            if rule.kpis and key not in rule.kpis:
                continue

            triggered = False

            if rule.type == "delta_decline" and delta < rule.threshold:
                triggered = True
            elif rule.type == "low_score" and isinstance(value, (int, float)) and value < rule.threshold:
                triggered = True
            elif rule.type == "high_value" and isinstance(value, (int, float)) and value > rule.threshold:
                triggered = True
            elif rule.type == "below_target" and isinstance(value, (int, float)) and value < rule.threshold:
                triggered = True

            if triggered:
                reason = rule.reason.format(value=value, delta=delta) if rule.reason else (
                    f"{rule.type}: {value} (threshold: {rule.threshold})"
                )
                # Avoid duplicate entries for the same KPI at a lower severity
                existing = [r for r in risks if r["area"] == label]
                if existing:
                    # Keep the higher severity
                    sev_rank = {"critical": 2, "high": 1}
                    if sev_rank.get(rule.severity, 0) > sev_rank.get(existing[-1]["severity"], 0):
                        existing[-1]["severity"] = rule.severity
                        existing[-1]["reason"] = reason
                else:
                    risks.append({
                        "area": label,
                        "severity": rule.severity,
                        "value": value,
                        "delta": delta,
                        "reason": reason,
                    })

    logger.debug("Identified %d risk areas", len(risks))
    return risks


def calculate_trend_velocity(points: List[dict]) -> str:
    """Calculate if trend is improving, declining, or stable"""
    if len(points) < 2:
        return "stable"

    values = [p.get('y', 0) for p in points]
    if values[-1] > values[0]:
        return "improving"
    elif values[-1] < values[0]:
        return "declining"
    return "stable"


def get_trend_color(index: int) -> str:
    """Get color for trend line based on index"""
    colors = ['#2563eb', '#7c3aed', '#059669', '#d97706', '#dc2626', '#0891b2', '#4f46e5']
    return colors[index % len(colors)]
