"""
ui/pages/manager.py — Manager team overview page
===================================================
"""

import io

import pandas as pd
import streamlit as st

from backend.services.blob_service import get_blob_text, get_selected_week
from backend.config.logging import get_logger
from backend.config.programs import load_program_config, get_cache_ttl, DEFAULT_PROGRAM

logger = get_logger(__name__)


@st.cache_data(ttl=get_cache_ttl())
def _load_week_csv(week: str, _program_prefix: str = None) -> pd.DataFrame:
    """Load the all-employees CSV for a given week from blob storage (cached 5 min)."""
    try:
        if _program_prefix:
            blob_path = f"{_program_prefix}/aggregations/{week}.csv"
        else:
            blob_path = f"aggregations/{week}.csv"
        raw = get_blob_text(blob_path)
        if not raw:
            logger.warning("CSV not found for week=%s path=%s", week, blob_path)
            return pd.DataFrame()
        logger.info("Loaded week CSV: %s", blob_path)
        return pd.read_csv(io.StringIO(raw))
    except Exception as exc:
        logger.error("Failed to load week CSV: %s", exc)
        return pd.DataFrame()


def render_manager_overview(emp_names: dict):
    """Render enterprise-grade manager dashboard with professional aesthetics"""
    logger.info("Rendering manager overview")

    # Load program config
    program_id = st.session_state.get("selected_program", DEFAULT_PROGRAM)
    program_cfg = load_program_config(program_id)
    L = program_cfg.get_label  # shorthand

    # ===== ENTERPRISE-GRADE PROFESSIONAL STYLING =====
    st.markdown("""
    <style>
    /* Manager Dashboard - Enterprise Professional Theme */
    .manager-dashboard {
        background: linear-gradient(135deg, #f8fafc 0%, #f1f5f9 100%);
        padding: 2.5rem;
        border-radius: 8px;
        margin: -2rem -2rem 2rem -2rem;
        border: 1px solid #e2e8f0;
        box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);
    }
    .holo-title {
        font-size: 1.875rem;
        font-weight: 600;
        color: #1e293b;
        text-align: center;
        margin-bottom: 0.5rem;
        letter-spacing: -0.025em;
    }
    .neon-card {
        background: white;
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        padding: 1.5rem;
        transition: all 0.2s ease;
        box-shadow: 0 1px 2px rgba(0, 0, 0, 0.05);
    }
    .neon-card:hover {
        border-color: #cbd5e1;
        box-shadow: 0 4px 6px rgba(0, 0, 0, 0.05);
    }
    .metric-value {
        font-size: 2.25rem;
        font-weight: 600;
        color: #0f172a;
        line-height: 1;
        margin: 0.5rem 0;
    }
    .metric-label {
        font-size: 0.75rem;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        color: #64748b;
        font-weight: 600;
    }
    .metric-subtitle {
        font-size: 0.875rem;
        color: #475569;
        font-weight: 500;
        margin-top: 0.375rem;
    }
    .section-header {
        font-size: 1.125rem;
        font-weight: 600;
        color: #334155;
        margin: 2rem 0 1rem 0;
        padding-bottom: 0.75rem;
        border-bottom: 2px solid #e2e8f0;
    }
    .behavior-card {
        background: white;
        border: 1px solid #e2e8f0;
        border-radius: 6px;
        padding: 1rem;
        margin: 0.5rem 0;
        transition: all 0.2s ease;
    }
    .behavior-card:hover {
        border-color: #cbd5e1;
        box-shadow: 0 2px 4px rgba(0, 0, 0, 0.05);
    }
    .behavior-score {
        font-size: 1.5rem;
        font-weight: 600;
        color: #0f172a;
    }
    .behavior-name {
        font-weight: 500;
        font-size: 0.9375rem;
        color: #475569;
    }
    .priority-alert {
        background: #fef2f2;
        border-left: 3px solid #dc2626;
        border-radius: 6px;
        padding: 1rem 1.25rem;
        margin: 1rem 0;
        color: #991b1b;
        font-size: 0.9375rem;
        font-weight: 500;
    }
    .glow-effect {
        border-color: #3b82f6;
        box-shadow: 0 0 0 1px #3b82f6;
    }
    .dashboard-subtitle {
        text-align: center;
        color: #64748b;
        font-size: 0.875rem;
        font-weight: 500;
        letter-spacing: 0.025em;
    }
    </style>
    """, unsafe_allow_html=True)

    if not emp_names:
        st.warning("No employees assigned to you.")
        return

    # ===== PROFESSIONAL HEADER =====
    st.markdown(f"""
<div class="manager-dashboard">
    <div class="holo-title">{L("manager", "page_title", "Team Performance Overview")}</div>
    <div class="dashboard-subtitle">{L("manager", "page_subtitle", "Comprehensive team analytics and insights")}</div>
</div>
""", unsafe_allow_html=True)

    # ===== LOAD DATA FROM WEEKLY KPI DATAFRAME =====
    week = st.session_state.get("selected_week", get_selected_week())
    df_all = _load_week_csv(week, _program_prefix=program_cfg.blob_prefix)

    if df_all.empty:
        st.warning("No data available for this week.")
        return

    # Filter to manager's employees in one pass
    emp_ids = set(str(eid) for eid in emp_names.values())
    id_to_name = {str(eid): name for name, eid in emp_names.items()}
    df_team = df_all[df_all["employee_id"].astype(str).isin(emp_ids)].copy()

    if df_team.empty:
        st.warning("No data found for your team members.")
        return

    # ===== PIVOT: one wide row per employee with all KPI values =====
    emp_pivot = (
        df_team.pivot_table(
            index="employee_id",
            columns="kpi_key",
            values="kpi_value",
            aggfunc="first",
        )
        .fillna(0)
    )

    def _col(name: str) -> pd.Series:
        return emp_pivot[name] if name in emp_pivot.columns else pd.Series(0, index=emp_pivot.index)

    # ===== VECTORISED METRIC EXTRACTION =====
    _mp_cfg = program_cfg.manager_page
    _perf_key = _mp_cfg.perf_score_key if _mp_cfg else "performance_score"
    _risk_key = _mp_cfg.risk_count_key if _mp_cfg else "risk_count"
    _top_q = _mp_cfg.top_performer_quantile if _mp_cfg else 0.9
    _att_q = _mp_cfg.needs_attention_quantile if _mp_cfg else 0.8
    _risk_thresh = _mp_cfg.risk_count_threshold if _mp_cfg else 2

    perf_scores = _col(_perf_key)
    risk_counts = _col(_risk_key)

    team_size = len(emp_pivot)
    avg_performance = round(perf_scores.mean()) if team_size else 0
    high_performers = int((perf_scores >= perf_scores.quantile(_top_q)).sum())
    needs_attention = int(((perf_scores < perf_scores.quantile(_att_q)) | (risk_counts >= _risk_thresh)).sum())
    # Config-driven summary KPIs — aggregate each metric per its rule
    summary_specs = program_cfg.manager_page.summary_kpis if program_cfg.manager_page else []
    kpi_aggregated: dict = {}
    for spec in summary_specs:
        key = spec["key"]
        agg = spec.get("agg", "sum")
        scale = spec.get("scale", 1)
        source = spec.get("source", "")
        # Filter by kpi_key AND source column when source is specified
        _mask = df_team["kpi_key"] == key
        if source and "source" in df_team.columns:
            _mask = _mask & (df_team["source"] == source)
        series = df_team.loc[_mask, "kpi_value"].dropna()
        if series.empty:
            raw_val = 0
        elif agg == "mean":
            raw_val = series[series > 0].mean() if (series > 0).any() else 0
        elif agg == "max":
            raw_val = series.max()
        elif agg == "min":
            raw_val = series.min()
        else:
            raw_val = series.sum()
        raw_val = raw_val if pd.notna(raw_val) else 0
        if isinstance(scale, (int, float)):
            kpi_aggregated[key] = int(round(raw_val * scale))
        else:
            kpi_aggregated[key] = int(round(raw_val))

    # ===== BUILD TEAM ROSTER from config =====
    roster_cols = {"employee_id": emp_pivot.index.astype(str)}
    roster_cols["Score"] = perf_scores.round().astype(int).values

    # Add config-driven roster KPI columns
    roster_kpi_specs = program_cfg.manager_page.roster_kpis if program_cfg.manager_page else []
    display_cols = ["Employee", "Score"]
    for spec in roster_kpi_specs:
        kpi_key = spec['key']
        kpi_label = spec['label']
        kpi_source = spec.get('source', '')
        pre_scaled = spec.get('pre_scaled', False)
        scale = spec.get('scale', None)

        # Read values filtered by source column when specified
        if kpi_source and "source" in df_team.columns:
            _filt = df_team.loc[
                (df_team["kpi_key"] == kpi_key) & (df_team["source"] == kpi_source)
            ].drop_duplicates(subset="employee_id").set_index("employee_id")["kpi_value"]
            _filt.index = _filt.index.astype(str)
            col_vals = _filt.reindex(emp_pivot.index.astype(str), fill_value=0).fillna(0).astype(float)
        else:
            col_vals = _col(kpi_key).fillna(0)

        # Apply scaling: percentage + pre_scaled flag, or legacy numeric scale
        if scale == "percentage":
            col_vals = col_vals * (1 if pre_scaled else 100)
        elif isinstance(scale, (int, float)) and scale != 1:
            col_vals = col_vals * scale

        roster_cols[kpi_label] = col_vals.round().astype(int).values
        display_cols.append(kpi_label)

    roster = pd.DataFrame(roster_cols)
    roster["Employee"] = roster["employee_id"].map(id_to_name).fillna(roster["employee_id"])

    team_data = roster[display_cols]

    # Behavior score averages — use explicit KPI list from config, fallback to source-based
    _behavior_kpis = _mp_cfg.behavior_kpis if _mp_cfg else []
    if _behavior_kpis:
        _behavior_keys = [k["key"] for k in _behavior_kpis]
        # Build source-aware filter: match kpi_key AND source column per entry
        _has_source_col = "source" in df_team.columns
        _beh_sources = {k["key"]: k.get("source", "") for k in _behavior_kpis}
        _beh_mask = pd.Series(False, index=df_team.index)
        for _bk, _bs in _beh_sources.items():
            _key_match = df_team["kpi_key"] == _bk
            if _bs and _has_source_col:
                _beh_mask |= (_key_match & (df_team["source"] == _bs))
            else:
                _beh_mask |= _key_match
        behavior_avg_series = (
            df_team.loc[_beh_mask]
            .groupby("kpi_key")["kpi_value"]
            .mean()
            .round(2)
        ) * 100
        behavior_avg_series = behavior_avg_series.round().astype(int)
        # Reindex to config order and apply labels
        _key_to_label = {k["key"]: k.get("label", k["key"]) for k in _behavior_kpis}
        behavior_avg_series = behavior_avg_series.reindex(_behavior_keys).dropna().astype(int)
        behavior_avg_series.index = behavior_avg_series.index.map(lambda k: _key_to_label.get(k, k))
    else:
        _behavior_group = _mp_cfg.behavior_kpi_group if _mp_cfg else "behavior_scores"
        behavior_avg_series = (
            df_team.loc[df_team["source"] == _behavior_group]
            .groupby("kpi_key")["kpi_value"]
            .mean()
            .round(2)
        ) * 100
        behavior_avg_series = behavior_avg_series.round().astype(int)

     # filter out zero scores
    # ===== TOP METRICS (config-driven) =====
    _top_metrics = _mp_cfg.top_metrics if _mp_cfg else []

    # Pre-compute all possible metric values
    _pct = round(high_performers / team_size * 100) if team_size > 0 else 0
    _computed_values = {
        "team_size": {"value": team_size, "label_default": "⚡ TEAM SIZE", "subtitle_default": "Active Agents", "subtitle_fmt": None},
        "avg_performance": {"value": avg_performance, "label_default": "📊 AVG PERFORMANCE", "subtitle_default": "Team Average", "subtitle_fmt": None, "glow": True},
        "top_performers": {"value": high_performers, "label_default": "🏆 TOP PERFORMERS", "subtitle_default": "{pct}% of Team", "subtitle_fmt": {"pct": _pct}},
        "needs_focus": {"value": needs_attention, "label_default": "⚠️ NEEDS FOCUS", "subtitle_default": "Immediate Attention", "subtitle_fmt": None},
    }

    if _top_metrics:
        metric_cols = st.columns(len(_top_metrics))
        for idx, tm in enumerate(_top_metrics):
            comp = _computed_values.get(tm.computation, {})
            label = L("manager", tm.label_key, comp.get("label_default", tm.key))
            subtitle_raw = L("manager", tm.subtitle_key, comp.get("subtitle_default", ""))
            subtitle_fmt = comp.get("subtitle_fmt")
            subtitle = subtitle_raw.format(**subtitle_fmt) if subtitle_fmt else subtitle_raw
            glow = "glow-effect" if comp.get("glow") else ""
            with metric_cols[idx]:
                st.markdown(f"""
<div class="neon-card {glow}">
    <div class="metric-label">{label}</div>
    <div class="metric-value">{comp.get('value', 0)}</div>
    <div class="metric-subtitle">{subtitle}</div>
</div>
""", unsafe_allow_html=True)
    else:
        # Fallback: hardcoded 4 metrics
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.markdown(f'<div class="neon-card"><div class="metric-label">{L("manager", "team_size_label", "⚡ TEAM SIZE")}</div><div class="metric-value">{team_size}</div><div class="metric-subtitle">{L("manager", "team_size_subtitle", "Active Agents")}</div></div>', unsafe_allow_html=True)
        with col2:
            st.markdown(f'<div class="neon-card glow-effect"><div class="metric-label">{L("manager", "avg_performance_label", "📊 AVG PERFORMANCE")}</div><div class="metric-value">{avg_performance}</div><div class="metric-subtitle">{L("manager", "avg_performance_subtitle", "Team Average")}</div></div>', unsafe_allow_html=True)
        with col3:
            st.markdown(f'<div class="neon-card"><div class="metric-label">{L("manager", "top_performers_label", "🏆 TOP PERFORMERS")}</div><div class="metric-value">{high_performers}</div><div class="metric-subtitle">{L("manager", "top_performers_subtitle", "{{pct}}% of Team").format(pct=_pct)}</div></div>', unsafe_allow_html=True)
        with col4:
            st.markdown(f'<div class="neon-card"><div class="metric-label">{L("manager", "needs_focus_label", "⚠️ NEEDS FOCUS")}</div><div class="metric-value">{needs_attention}</div><div class="metric-subtitle">{L("manager", "needs_focus_subtitle", "Immediate Attention")}</div></div>', unsafe_allow_html=True)

    # ===== CONFIG-DRIVEN SECTIONS =====
    _enabled_sections = _mp_cfg.sections if _mp_cfg else ["quality_sales", "behavior_analysis", "team_roster", "coaching_priorities"]

    # ===== QUALITY & SALES DASHBOARD =====
    if "quality_sales" in _enabled_sections:
        st.markdown(f'<div class="section-header">{L("manager", "quality_section", "🎯 QUALITY & SALES METRICS")}</div>', unsafe_allow_html=True)

        if summary_specs:
            q_cols = st.columns(len(summary_specs))
            for idx, spec in enumerate(summary_specs):
                with q_cols[idx]:
                    val = kpi_aggregated.get(spec["key"], None)
                    fmt = spec.get("format", "{v}")
                    display_val = fmt.format(v=val) if val is not None else "N/A"
                    st.markdown(f"""
<div class="neon-card">
    <div class="metric-label">{spec["label"]}</div>
    <div class="metric-value">{display_val}</div>
</div>
""", unsafe_allow_html=True)
        else:
            st.info("No quality & sales data available.")

    # ===== BEHAVIOR ANALYSIS =====
    if "behavior_analysis" in _enabled_sections:
        st.markdown(f'<div class="section-header">{L("manager", "behavior_section", "🎭 BEHAVIOR INTELLIGENCE")}</div>', unsafe_allow_html=True)
        if behavior_avg_series.empty:
            st.info("No behavior data available.")

        if not behavior_avg_series.empty:
            _focus_n = _mp_cfg.behavior_focus_count if _mp_cfg else 3
            _excel_n = _mp_cfg.behavior_excellence_count if _mp_cfg else 3

            sorted_behaviors = behavior_avg_series.sort_values()
            needs_improvement = list(sorted_behaviors.head(_focus_n).items())
            strengths = list(sorted_behaviors.tail(_excel_n).items())

            col1, col2 = st.columns(2)

            with col1:
                st.markdown(f"**{L('manager', 'critical_focus', '🔴 CRITICAL FOCUS AREAS')}**")
                for behavior, avg_score in needs_improvement:
                    st.markdown(f"""
<div class="behavior-card">
    <div style="display: flex; justify-content: space-between; align-items: center; text-align: center;">
        <span class="behavior-name">{behavior}</span>
        <span class="behavior-score">{round(avg_score, 2)}</span>
    </div>
</div>
""", unsafe_allow_html=True)

            with col2:
                st.markdown(f"**{L('manager', 'team_excellence', '🟢 TEAM EXCELLENCE')}**")
                for behavior, avg_score in strengths:
                    st.markdown(f"""
<div class="behavior-card">
    <div style="display: flex; justify-content: space-between; align-items: center; text-align: center;">
        <span class="behavior-name">{behavior}</span>
        <span class="behavior-score" style="text-align: center;">{round(avg_score, 2)}</span>
    </div>
</div>
""", unsafe_allow_html=True)

    # ===== TEAM ROSTER =====
    if "team_roster" in _enabled_sections:
        st.markdown(f'<div class="section-header">{L("manager", "roster_section", "👥 TEAM ROSTER")}</div>', unsafe_allow_html=True)

        if team_data.empty:
            st.info("No team roster data available.")
        elif not team_data.empty:
            # Build dynamic column config from roster KPI specs
            col_config = {
                "Score": st.column_config.ProgressColumn(
                    "Performance",
                    format="%d",
                    min_value=0,
                    max_value=100,
                ),
            }
            for spec in roster_kpi_specs:
                col_config[spec['label']] = st.column_config.NumberColumn(spec['label'])

            st.dataframe(
                team_data.sort_values("Score", ascending=False),
                width="stretch",
                hide_index=True,
                column_config=col_config,
            )

    # ===== ACTION PRIORITIES =====
    if "coaching_priorities" in _enabled_sections:
        _esc_factor = _mp_cfg.escalation_threshold_factor if _mp_cfg else 1.5
        st.markdown(f'<div class="section-header">{L("manager", "coaching_section", "🎯 COACHING PRIORITIES")}</div>', unsafe_allow_html=True)

        if needs_attention > 0:
            st.markdown(f"""
<div class="priority-alert">
    <strong>{L("manager", "high_priority_label", "⚡ HIGH PRIORITY:")}</strong> {L("manager", "high_priority_msg", "{n} team member(s) require immediate intervention").format(n=needs_attention)}
</div>
""", unsafe_allow_html=True)

        _esc_kpi = _mp_cfg.escalation_kpi if _mp_cfg else "escalations"
        total_escalations = int(kpi_aggregated.get(_esc_kpi, 0))

        if total_escalations > team_size * _esc_factor:
            st.markdown(f"""
<div class="priority-alert">
    <strong>{L("manager", "escalation_alert_label", "🚨 ESCALATION ALERT:")}</strong> {L("manager", "escalation_alert_msg", "{n} total escalations detected - review de-escalation protocols").format(n=total_escalations)}
</div>
""", unsafe_allow_html=True)

        if not needs_attention and total_escalations <= team_size * _esc_factor:
            st.markdown(f"""
<div style="background: linear-gradient(135deg, rgba(0, 255, 136, 0.25), rgba(0, 255, 136, 0.15));
            border-left: 5px solid #00ff88; border-radius: 14px; padding: 1.25rem 1.75rem; margin: 1.25rem 0;
            color: rgba(255, 255, 255, 1); font-size: 1.05rem; font-weight: 600;">
    <strong>{L("manager", "optimal_label", "✅ OPTIMAL PERFORMANCE:")}</strong> {L("manager", "optimal_msg", "Team is operating at peak efficiency across all metrics")}
</div>
""", unsafe_allow_html=True)
