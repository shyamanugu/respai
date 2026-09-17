"""
ui/components.py — Reusable visualization components
=====================================================
KPI card renderer, radar chart, heatmap, and speedometer gauge.
"""

from typing import List, Optional

import plotly.graph_objects as go
import streamlit as st

from backend.config.programs import DEFAULT_PROGRAM, load_program_config


def _L(section: str, key: str, default: str = "") -> str:
    program_id = st.session_state.get("selected_program", DEFAULT_PROGRAM)
    return load_program_config(program_id).get_label(section, key, default)


def render_kpi_card(
    label: str,
    value,
    delta: Optional[int] = None,
    is_positive: bool = True,
    clickable: bool = False,
) -> str:
    """Render a KPI card with optional delta and clickable state"""
    delta_class = (
        "positive"
        if (delta and ((is_positive and delta > 0) or (not is_positive and delta < 0)))
        else "negative"
    )
    delta_symbol = "↑" if delta and delta > 0 else "↓" if delta and delta < 0 else ""
    delta_html = (
        f'<div class="kpi-delta {delta_class}">{delta_symbol} {abs(delta) if delta else ""}</div>'
        if delta is not None
        else ""
    )

    clickable_class = "clickable" if clickable else ""


    return f"""<div class="kpi-card {clickable_class}">
    <div class="kpi-value">{value}</div>
    <div class="kpi-label">{label}</div>
    {delta_html}
</div>"""


def create_performance_radar(report: dict, radar_metrics=None) -> Optional[go.Figure]:
    """Create radar chart for performance metrics.

    If *radar_metrics* (list of RadarMetric) is provided, only those metrics
    are shown and labels come from config.  Otherwise all comparison entries
    are used.
    """
    comparison = report.get("comparison", [])
    if not comparison:
        return None

    if radar_metrics:
        # Build lookup: metric name -> comparison entry
        comp_map = {c["metric"]: c for c in comparison}
        filtered = []
        suffixes = []
        for rm in radar_metrics:
            entry = comp_map.get(rm.metric)
            pre_scaled = getattr(rm, 'pre_scaled', False)
            mult = 1 if pre_scaled else (100 if rm.scale == "percentage" else 1)
            suffix = "%" if rm.scale == "percentage" else ""
            if entry:
                filtered.append({
                    "label": rm.label or rm.metric,
                    "individual": round((entry.get("individual") or 0) * mult, 2),
                    "teamAvg": round((entry.get("teamAvg") or 0) * mult, 2),
                })
            else:
                # Metric not in data — show as 0
                filtered.append({"label": rm.label or rm.metric, "individual": 0, "teamAvg": 0})
            suffixes.append(suffix)
        if not filtered:
            return None
        categories = [f["label"] for f in filtered]
        individual = [f["individual"] for f in filtered]
        team_avg = [f["teamAvg"] for f in filtered]
    else:
        categories = [c["metric"] for c in comparison]
        individual = [(c["individual"] or 0) for c in comparison]
        team_avg = [(c["teamAvg"] or 0) for c in comparison]
        suffixes = [""] * len(categories)

    fig = go.Figure()

    combined_text = [
        f"Individual: {iv}{s}<br>Team Average: {tv}{s}"
        for iv, tv, s in zip(individual, team_avg, suffixes)
    ]

    fig.add_trace(
        go.Scatterpolar(
            r=team_avg,
            theta=categories,
            fill="toself",
            name=_L("charts", "team_avg_trace", "Team Average"),
            line=dict(color="#7c3aed", width=2),
            fillcolor="rgba(124, 58, 237, 0.15)",
            hovertemplate="%{theta}<br>%{text}<extra></extra>",
            text=combined_text,
        )
    )

    fig.add_trace(
        go.Scatterpolar(
            r=individual,
            theta=categories,
            fill="toself",
            name=_L("charts", "individual_trace", "Individual"),
            line=dict(color="#f97316", width=2),
            fillcolor="rgba(249, 115, 22, 0.15)",
            hovertemplate="%{theta}<br>%{text}<extra></extra>",
            text=combined_text,
        )
    )

    fig.update_layout(
        polar=dict(
            radialaxis=dict(
                visible=True,
                range=[0, max(max(individual, default=0), max(team_avg, default=0)) * 1.2 or 1],
            )
        ),
        hovermode="closest",
        showlegend=True,
        height=400,
    )

    return fig


def create_trend_heatmap(report: dict, trend_metrics=None, trends_field: str = "trends") -> Optional[go.Figure]:
    """Create heatmap of trends.

    If *trend_metrics* (list of TrendMetric) is provided, only those metrics
    are shown and labels come from config.  Otherwise all trend entries
    are used.
    """
    trends = report.get(trends_field, {})
    if not trends:
        return None

    if trend_metrics:
        # Only include configured metrics, in config order
        ordered = []
        for tm in trend_metrics:
            _pre_scaled = getattr(tm, 'pre_scaled', False)
            _mult = 1 if _pre_scaled else 100
            if tm.metric in trends:
                raw = trends[tm.metric]
                if _mult != 1:
                    raw = [{"x": p["x"], "y": round((p.get("y") or 0) * _mult, 2)} for p in raw]
                ordered.append((tm.label or tm.metric, raw))
            else:
                # Metric not in data — include with 0 values
                # Use periods from first available metric as reference
                ref_periods = next((trends[k] for k in trends), [])
                ordered.append((tm.label or tm.metric, [{"x": p["x"], "y": 0} for p in ref_periods]))
        if not ordered:
            return None
        metrics = [o[0] for o in ordered]
        periods = [p["x"] for p in ordered[0][1]]
        z_values = [[p["y"] for p in o[1]] for o in ordered]
    else:
        metrics = list(trends.keys())
        periods = [p["x"] for p in list(trends.values())[0]]
        z_values = []
        for metric in metrics:
            values = [(p.get("y") or 0) for p in trends[metric]]
            z_values.append(values)

    fig = go.Figure(
        data=go.Heatmap(
            z=z_values,
            x=periods,
            y=metrics,
            colorscale="RdYlGn",
            text=z_values,
            texttemplate="%{text}",
            textfont={"size": 12},
            colorbar=dict(title="Value"),
        )
    )

    fig.update_layout(
        title=_L("charts", "heatmap_title", "Performance Heatmap"),
        xaxis_title=_L("charts", "heatmap_xaxis", "Period"),
        yaxis=dict(
            title=_L("charts", "heatmap_yaxis", "Metric"),
            categoryorder="array",
            categoryarray=metrics,
            autorange="reversed",
        ),
        height=400,
    )

    return fig


def create_speedometer_gauge(
    behavior_scores: dict, overall_score: float = None
) -> Optional[str]:
    """Create an animated JavaScript speedometer gauge for behavior scores"""
    if not behavior_scores:
        return None

    # Use provided overall_score or calculate average if not provided
    if overall_score is None:
        scores = []
        for behavior, data in behavior_scores.items():
            score = data.get("score", 0)
            scores.append(score)

        if not scores:
            return None

        avg_score = sum(scores) / len(scores)
    else:
        avg_score = overall_score

    # Determine color based on score
    if avg_score >= 70:
        color = "#10b981"
    elif avg_score >= 40:
        color = "#f59e0b"
    else:
        color = "#ef4444"

    html_code = f"""
    <div style="width: 100%; height: 280px; position: relative;">
        <svg id="speedometer" viewBox="0 0 200 140" style="width: 100%; height: 100%;">
            <defs>
                <linearGradient id="scoreGradient" x1="0%" y1="0%" x2="100%" y2="0%">
                    <stop offset="0%" style="stop-color:#ef4444;stop-opacity:1" />
                    <stop offset="50%" style="stop-color:#f59e0b;stop-opacity:1" />
                    <stop offset="100%" style="stop-color:#10b981;stop-opacity:1" />
                </linearGradient>
                <filter id="shadow" x="-50%" y="-50%" width="200%" height="200%">
                    <feDropShadow dx="0" dy="2" stdDeviation="3" flood-opacity="0.15"/>
                </filter>
            </defs>
            <!-- Background track -->
            <path d="M 30 100 A 70 70 0 0 1 170 100" fill="none" stroke="#e5e7eb" stroke-width="14" stroke-linecap="round"/>
            <!-- Zone hint (full semicircle, very faint) -->
            <path d="M 30 100 A 70 70 0 0 1 170 100" fill="none" stroke="url(#scoreGradient)" stroke-width="14" stroke-linecap="round" opacity="0.12"/>
            <!-- Active arc (filled portion) -->
            <path id="activeArc" d="M 30 100 A 70 70 0 0 1 30 100" fill="none" stroke="url(#scoreGradient)" stroke-width="14" stroke-linecap="round" opacity="0"/>
            <!-- Center hub -->
            <circle cx="100" cy="100" r="8" fill="#ffffff" filter="url(#shadow)"/>
            <circle cx="100" cy="100" r="5" fill="{color}"/>
            <!-- Needle -->
            <line id="needle" x1="100" y1="100" x2="100" y2="40" stroke="{color}" stroke-width="3" stroke-linecap="round" style="transform-origin: 100px 100px; transform: rotate(-90deg); transition: transform 1.5s cubic-bezier(0.4, 0, 0.2, 1);"/>
            <!-- Tick marks -->
            <g opacity="0.4">
                <line x1="37" y1="100" x2="45" y2="100" stroke="#64748b" stroke-width="2"/>
                <line x1="155" y1="100" x2="163" y2="100" stroke="#64748b" stroke-width="2"/>
                <line x1="100" y1="37" x2="100" y2="45" stroke="#64748b" stroke-width="2"/>
            </g>
            <!-- Score text -->
            <text id="scoreText" x="100" y="95" text-anchor="middle" font-family="Inter, system-ui, sans-serif" font-size="32" font-weight="800" fill="{color}">0</text>
            <text x="100" y="115" text-anchor="middle" font-family="Inter, system-ui, sans-serif" font-size="10" font-weight="600" fill="#64748b">{_L("charts", "behavior_gauge", "BEHAVIOR SCORE")}</text>
        </svg>
    </div>
    <script>
    (function() {{
        const score = {avg_score};
        setTimeout(() => {{
            const needle = document.getElementById('needle');
            if (needle) {{
                const angle = -90 + (score * 1.8);
                needle.style.transform = `rotate(${{angle}}deg)`;
            }}
            const activeArc = document.getElementById('activeArc');
            if (activeArc && score > 0) {{
                const arcAngle = (180 - score * 1.8) * Math.PI / 180;
                const x = 100 + 70 * Math.cos(arcAngle);
                const y = 100 - 70 * Math.sin(arcAngle);
                activeArc.setAttribute('d', `M 30 100 A 70 70 0 0 1 ${{x.toFixed(2)}} ${{y.toFixed(2)}}`);
                activeArc.style.transition = 'opacity 0.6s ease-in';
                activeArc.style.opacity = '1';
            }}
            const scoreText = document.getElementById('scoreText');
            if (scoreText) {{
                let cur = 0;
                const inc = score / 60;
                const timer = setInterval(() => {{
                    cur += inc;
                    if (cur >= score) {{ cur = score; clearInterval(timer); }}
                    scoreText.textContent = Math.round(cur);
                }}, 25);
            }}
        }}, 100);
    }})();
    </script>
    """

    return html_code
