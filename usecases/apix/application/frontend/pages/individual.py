"""
ui/pages/individual.py — Individual employee report page
==========================================================
"""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components

from backend.auth.session import log_audit_event
from backend.db.notes import add_note, get_notes, edit_note, hide_note
from backend.services.blob_service import get_selected_week, load_report_for_week
from backend.services.scoring import (
    calculate_performance_score,
    calculate_trend_velocity,
    get_trend_color,
    identify_risk_areas,
)
from frontend.components.charts import (
    create_performance_radar,
    create_speedometer_gauge,
    create_trend_heatmap,
    render_kpi_card,
)
from frontend.components.sections import (
    render_customer_experience_section,
    render_escalations_section,
    render_key_improvements_section,
    render_sales_outcome_section,
)
from backend.config.logging import get_logger
from backend.config.programs import load_program_config, DEFAULT_PROGRAM, find_mode_for_program

logger = get_logger(__name__)


def render_individual_report(emp_id: str, emp_name: str):
    """Render detailed individual report"""
    logger.info("Rendering individual report for emp=%s week=%s", emp_id, st.session_state.get("selected_week"))
    week = st.session_state.get("selected_week", get_selected_week())
    report = load_report_for_week(emp_id, week)
    if not report:
        st.error(f"Could not load report for {emp_name}")
        return

    log_audit_event("view_report", st.session_state.user_id, f"Employee: {emp_id}")

    # Load program config for dynamic KPI rendering
    # Cache program_name in session state; clear when selected employee changes
    # print(f"Session state before program check: {st.session_state}, emp_id: {emp_id}, cached emp_id: {st.session_state.get('_program_employee_id')}, cached program: {st.session_state.get('_program_name')}")
    if st.session_state.get("_program_employee_id") != emp_id:
        st.session_state["_program_employee_id"] = emp_id
        st.session_state["_program_name"] = find_mode_for_program(report.get("programName") or DEFAULT_PROGRAM)

    program_name = st.session_state["_program_name"]
    program_cfg = load_program_config(program_name)

    L = program_cfg.get_label  # shorthand

    score = calculate_performance_score(report, program_config=program_cfg)
    col1, col2 = st.columns([3, 1])
    with col1:
        st.markdown(f"## {report.get('EmployeeName', emp_name)}")
        period = report.get("period", "Unknown").title()
        st.caption(f"Period: {period} | Employee ID: {emp_id}")

    with col2:
        st.metric(L("individual", "performance_score_label", "Performance Score"), f"{score}/100")

    st.markdown("---")

    # ========== MODERN KPI + SPEEDOMETER SECTION ==========
    _has_more_kpis = bool(
        program_cfg.individual_page and program_cfg.individual_page.has_more_kpi_groups()
    )
    if _has_more_kpis:
        _dash_col, _icon_col = st.columns([0.92, 0.08])
        with _dash_col:
            st.markdown(f"### {L('individual', 'performance_dashboard', 'Performance Dashboard')}")
        with _icon_col:
            st.markdown("<div style='height:0.35rem;'></div>", unsafe_allow_html=True)
            if st.button("🔍", key="more_kpis_btn", help="View more KPIs"):
                st.session_state["_more_kpis_open"] = True
                st.rerun()
    else:
        st.markdown(f"### {L('individual', 'performance_dashboard', 'Performance Dashboard')}")

    total_calls = report.get("totalCallCount", 0)

    kpis = report.get("kpis", [])
    behavior_scores = report.get("behavior_scores", {})
    call_handling_skills = report.get(
        "call_handling_and_softs_kills", {}
    ) or report.get("call_handling_and_soft_skills", {})
    wcc_behavior = report.get("wcc_behavior_scores", {})
    wcc_count_kpis = report.get("wcc_count_kpis", {})
    wcc_kpis = report.get("wcc_kpis", [])

    # Map source names from program config to pre-loaded variables
    source_map = {
        "report": report,
        "kpis": kpis,
        "kpi_groups": report.get("kpi_groups", []),
        "wcc_kpis": wcc_kpis,
        "wcc_count_kpis": wcc_count_kpis,
        "behavior_scores": behavior_scores,
        "call_handling_and_soft_skills": call_handling_skills,
        "wcc_behavior": wcc_behavior,
        "wcc_behavior_scores": wcc_behavior,
    }

    if program_cfg.has_kpi_groups():
        col_kpis, col_speedometer = st.columns([2.3, 1])

        # ========== LEFT COLUMN: KPIs ==========
        with col_kpis:
            render_kpi_groups_column(program_cfg, source_map)

        # ========== RIGHT COLUMN: SPEEDOMETER + BEHAVIOR SUMMARY ==========
        with col_speedometer:
            render_behavior_summary_panel(
                report,
                program_cfg,
                behavior_scores,
                call_handling_skills,
                wcc_behavior,
                source_map,
                total_calls,
            )
    else:
        st.warning("No KPI data available")

    # ---- KPI detail popup dialog (must be at top-level scope, not inside columns) ----
    render_kpi_popup_dialog()

    # ---- "More KPIs" popup dialog (top-level scope) ----
    render_more_kpis_dialog(program_cfg, source_map)

    # ========== TABS (config-driven) ==========
    _tab_defs = {
        "overview": {"label_key": "overview_tab", "default": "Overview"},
        "trends":   {"label_key": "trends_tab",   "default": "Trends"},
        "goals":    {"label_key": "goals_tab",     "default": "Goals"},
        "notes":    {"label_key": "notes_tab",     "default": "Notes"},
    }
    _enabled_tabs = (
        program_cfg.individual_page.tabs
        if program_cfg.individual_page
        else ["overview", "trends", "goals", "notes"]
    )
    _tab_labels = [
        L("individual", _tab_defs[t]["label_key"], _tab_defs[t]["default"])
        for t in _enabled_tabs
    ]
    _tabs = st.tabs(_tab_labels)

    _tab_renderers = {
        "overview": lambda: _render_overview_tab(report, emp_id, score, program_config=program_cfg),
        "trends":   lambda: _render_trends_tab(report),
        "goals":    lambda: _render_goals_tab(),
        "notes":    lambda: _render_notes_tab(emp_id),
    }

    for idx, tab_key in enumerate(_enabled_tabs):
        with _tabs[idx]:
            renderer = _tab_renderers.get(tab_key)
            if renderer:
                renderer()


# ---------------------------------------------------------------------------
# Shared KPI rendering (used by Individual Report + Performance Metrics)
# ---------------------------------------------------------------------------


def _build_group_cards(group, source_map):
    """Build the list of KPI card dicts for a single KPI group.

    Resolves each KPI spec against ``source_map`` (list/dict sources), scales
    0-1 values to 0-100 when ``pre_scaled`` is False, and collects supporting
    transcript evidence items for popup rendering. Shared by the main KPI
    column and the "More KPIs" popup so both render identically.
    """
    group_kpis = []
    for kpi_spec in group.kpis:
        src_data = source_map.get(kpi_spec.source)

        value, delta = "N/A", None
        transcripts = []
        if isinstance(src_data, list):
            for item in src_data:
                if item.get("key") == kpi_spec.key:
                    value = item.get("value", item.get("score", "N/A"))
                    delta = round(item.get("delta", 0), 2) if item.get("delta") is not None else None
                    if kpi_spec.popup and kpi_spec.items == "list" and item.get("items"):
                        for sub_item in item["items"]:
                            transcripts.append({
                                "date": sub_item.get(kpi_spec.items_prop.get("key_data", "date_utc"), "N/A"),
                                "contact_id": sub_item.get(kpi_spec.items_prop.get("key_contact", "contact_id"), "N/A"),
                                "segment_id": sub_item.get(kpi_spec.items_prop.get("key_segment", "segment_id"), "N/A"),
                                "status": sub_item.get(kpi_spec.items_prop.get("key_status", "status"), "N/A"),
                                "transcript_excerpt": sub_item.get(kpi_spec.items_prop.get("key_transcript", "transcript_excerpt"), "N/A"),
                            })
                    break
        elif isinstance(src_data, dict):
            kpi_data = src_data.get(kpi_spec.key, {})
            if isinstance(kpi_data, dict):
                value = kpi_data.get("value", kpi_data.get("score", "N/A"))
                delta = round(kpi_data.get("delta", 0), 2) if kpi_data.get("delta") is not None else None
            elif kpi_data is not None:
                value = kpi_data
        # Scale 0-1 values to 0-100 when pre_scaled is False
        if not kpi_spec.pre_scaled:
            if isinstance(value, (int, float)):
                value = round(value * 100, 2)
            if isinstance(delta, (int, float)):
                delta = round(delta * 100, 2)
        group_kpis.append({
            "key": kpi_spec.key,
            "label": kpi_spec.label,
            "value": value,
            "delta": delta,
            "popup": kpi_spec.popup,
            "items": transcripts,
        })

    # Drop entries with no key or no data
    return [k for k in group_kpis if k.get("key") and k.get("value") != "N/A"]


def render_kpi_groups_column(program_cfg, source_map):
    """Render the left-column KPI group cards (3 per row) from program JSON config.

    Shared by the Individual Report and Individual Performance Metrics pages so
    both load identical KPI names/values from the program JSON and render with the
    same styling.
    """
    for group in program_cfg.individual_page.kpi_groups:
        st.caption(f"**{group.name}**")
        group_kpis = _build_group_cards(group, source_map)

        if not group_kpis:
            st.info(f"No data available for {group.name}.")
            continue

        for row_start in range(0, len(group_kpis), 3):
            row_kpis = group_kpis[row_start : row_start + 3]
            cols = st.columns(3, gap="medium")

            for col_idx, kpi in enumerate(row_kpis):
                with cols[col_idx]:
                    is_positive = not program_cfg.is_inverse_kpi(kpi.get("key", ""))
                    has_popup = kpi.get("popup", False)
                    card_html = render_kpi_card(
                        kpi.get("label", "Unknown"),
                        kpi.get("value", "N/A"),
                        kpi.get("delta"),
                        is_positive,
                        clickable=has_popup,
                    )
                    st.markdown(card_html, unsafe_allow_html=True)
                    if has_popup:
                        if st.button(
                            "​",
                            key=f"kpi_popup_{kpi['key']}",
                            use_container_width=True,
                            type="tertiary",
                        ):
                            st.session_state["_kpi_popup"] = kpi
                            st.rerun()


def _build_transcript_grid_html(items):
    """Build the pure-HTML transcript evidence grid for a list of items.

    Uses native ``<details>`` so expanding a transcript never triggers a
    Streamlit rerun. Shared by the per-KPI popup and the "More KPIs" popup.
    """
    cards_html = ""
    for item in items:
        status = (item.get("status", "") or "").lower()
        icon = "&#10060;" if "missed" in status else "&#9989;"
        cid = str(item.get("contact_id", ""))
        short_cid = f"{cid[:5]}.." if len(cid) > 5 else cid
        date_val = str(item.get("date", ""))
        seg_id = str(item.get("segment_id", "") or "")

        transcript = item.get("transcript_excerpt", "N/A")
        body_html = ""
        if seg_id:
            body_html += f'<div class="t-meta">Seg: {seg_id}</div>'
        body_html += f'<div class="t-meta">Contact: {cid}</div>'
        if isinstance(transcript, list):
            for line in transcript:
                speaker = line.get("speaker", "Unknown")
                text = line.get("text", "")
                body_html += f'<div class="t-line"><span class="t-speaker">{speaker}:</span> {text}</div>'
        elif isinstance(transcript, str) and transcript != "N/A":
            body_html += f'<div class="t-line">{transcript}</div>'

        cards_html += f'''<div class="t-cell">
            <details><summary>{icon} {date_val} {short_cid}</summary>
            <div class="t-body">{body_html}</div></details></div>'''

    return f'''
    <style>
    .t-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 4px; }}
    .t-cell {{ position: relative; }}
    .t-cell details {{ border: 1px solid #e0e0e0; border-radius: 4px; background: #fafafa; }}
    .t-cell summary {{ padding: 6px 10px; font-size: 0.9rem; font-weight: 600; color: #333;
        cursor: pointer; list-style: none; user-select: none; }}
    .t-cell summary::-webkit-details-marker {{ display: none; }}
    .t-cell summary::before {{ content: '▸ '; font-size: 0.75rem; color: #999; }}
    .t-cell details[open] summary::before {{ content: '▾ '; }}
    .t-cell summary:hover {{ background: #f0f0f0; }}
    .t-cell details[open] {{ background: #fff; box-shadow: 0 4px 14px rgba(0,0,0,0.13); }}
    .t-body {{ padding: 6px 10px 8px; font-size: 0.8rem; line-height: 1.5;
        max-height: 250px; overflow-y: auto; border-top: 1px solid #eee; }}
    .t-meta {{ color: #888; font-size: 0.75rem; margin-bottom: 3px; }}
    .t-line {{ margin: 2px 0; }}
    .t-speaker {{ font-weight: 700; color: #444; }}
    </style>
    <div style="font-size:0.85rem;color:#666;margin-bottom:5px;">{len(items)} conversations</div>
    <div class="t-grid">{cards_html}</div>
    '''


def render_more_kpis_dialog(program_cfg, source_map):
    """Render the "More KPIs" popup dialog if the button was clicked.

    Shows the extra KPI groups (``individual_page.more_kpi_groups``) as cards
    with their supporting transcript evidence inline (expandable ``<details>``),
    so no nested Streamlit dialog is required. Must be called at top-level page
    scope. Triggered by ``st.session_state["_more_kpis_open"]``.
    """
    if not st.session_state.pop("_more_kpis_open", False):
        return
    if not (program_cfg.individual_page and program_cfg.individual_page.more_kpi_groups):
        return

    @st.dialog("More KPIs", width="large")
    def _show_more_kpis():
        for group in program_cfg.individual_page.more_kpi_groups:
            st.caption(f"**{group.name}**")
            group_kpis = _build_group_cards(group, source_map)
            if not group_kpis:
                st.info(f"No data available for {group.name}.")
                continue

            for row_start in range(0, len(group_kpis), 3):
                row_kpis = group_kpis[row_start : row_start + 3]
                cols = st.columns(3, gap="medium")
                for col_idx, kpi in enumerate(row_kpis):
                    with cols[col_idx]:
                        is_positive = not program_cfg.is_inverse_kpi(kpi.get("key", ""))
                        st.markdown(
                            render_kpi_card(
                                kpi.get("label", "Unknown"),
                                kpi.get("value", "N/A"),
                                kpi.get("delta"),
                                is_positive,
                                clickable=False,
                            ),
                            unsafe_allow_html=True,
                        )

    _show_more_kpis()


def render_kpi_popup_dialog():
    """Render the KPI detail popup dialog if a KPI was clicked.

    Must be called at top-level page scope (not inside columns). Shared by the
    Individual Report and Individual Performance Metrics pages.
    """
    _popup_kpi = st.session_state.pop("_kpi_popup", None)
    if not _popup_kpi:
        return

    _dialog_width = "large" if _popup_kpi.get("items") else "small"

    @st.dialog(_popup_kpi.get("label", "KPI Detail"), width=_dialog_width)
    def _show_kpi_popup():
        st.markdown(f"**Current Value:** {_popup_kpi.get('value', 'N/A')}")
        if _popup_kpi.get("delta") is not None:
            st.markdown(f"**Delta:** {_popup_kpi.get('delta')}")

        items = _popup_kpi.get("items", [])
        if items:
            st.markdown(_build_transcript_grid_html(items), unsafe_allow_html=True)

    _show_kpi_popup()


# ---------------------------------------------------------------------------
# Shared behavior summary panel (used by Individual Report + Performance Metrics)
# ---------------------------------------------------------------------------


def render_behavior_summary_panel(
    report,
    program_cfg,
    behavior_scores,
    call_handling_skills,
    wcc_behavior,
    source_map,
    total_calls,
    button_key: str = "view_all_behaviors_btn",
):
    """Render the right-column behavior panel shared by the Individual Report and
    Individual Performance Metrics pages: config-driven header stats, the
    speedometer gauge, the strong/focus summary, and the "View All Behaviors"
    modal. Everything is loaded from the JSON report + program config so both
    pages render identical behavior analytics.
    """
    L = program_cfg.get_label
    if not behavior_scores:
        return

    # ── Config-driven header stats ──
    _header_stats_cfg = program_cfg.individual_page.header_stats if program_cfg.individual_page else []
    _stat_values = []
    for hs in _header_stats_cfg:
        # Resolve source: empty/"report" = root report, else source_map or sub-key
        if not hs.source or hs.source == "report":
            source_obj = report
        elif hs.source in source_map:
            source_obj = source_map[hs.source]
        else:
            source_obj = report.get(hs.source, {})
        val = source_obj.get(hs.key, 0) if isinstance(source_obj, dict) else 0
        _stat_values.append(val)

    if not _header_stats_cfg:
        # Fallback: hardcoded stats
        _header_stats_cfg_fallback = [
            {"label": L("individual", "total_conversations", "Total Conversations"), "icon": "💬", "value": total_calls},
            {"label": L("individual", "new_prospect", "New Prospect"), "icon": "🆕", "value": report.get("new_prospect", {}).get("total", 0)},
        ]
    else:
        _header_stats_cfg_fallback = [
            {"label": hs.label, "icon": hs.icon, "value": _stat_values[i]}
            for i, hs in enumerate(_header_stats_cfg)
        ]

    _gradient_pairs = [
        ("#0ea5e9", "#a855f7"),
        ("#10b981", "#059669"),
        ("#f59e0b", "#ef4444"),
        ("#6366f1", "#8b5cf6"),
    ]

    stat_items_html = ""
    for i, stat in enumerate(_header_stats_cfg_fallback):
        g1, g2 = _gradient_pairs[i % len(_gradient_pairs)]
        if i > 0:
            stat_items_html += (
                "<div style='width:1px;background:linear-gradient(180deg,transparent,#e5e7eb,transparent);"
                "margin:0.5rem 0;'></div>"
            )
        stat_items_html += (
            f"<div style='flex:1;'>"
            f"<div style='display:inline-flex;align-items:center;justify-content:center;"
            f"background:linear-gradient(135deg,{g1},{g2});"
            f"width:32px;height:32px;border-radius:8px;"
            f"box-shadow:0 3px 10px rgba(14,165,233,0.25);"
            f"margin-bottom:0.4rem;'>"
            f"<span style='font-size:1.1rem;'>{stat['icon']}</span></div>"
            f"<div style='font-size:0.7rem;color:#64748b;font-weight:600;"
            f"text-transform:uppercase;letter-spacing:0.08em;margin-bottom:0.3rem;'>"
            f"{stat['label']}</div>"
            f"<div style='font-size:2.5rem;font-weight:900;"
            f"background:linear-gradient(135deg,{g1},{g2});"
            f"-webkit-background-clip:text;-webkit-text-fill-color:transparent;"
            f"background-clip:text;line-height:1;margin-bottom:0.3rem;'>"
            f"{stat['value']}</div>"
            f"</div>"
        )

    st.markdown(
        f"""
<div style='background: linear-gradient(135deg, rgba(255,255,255,0.95) 0%, rgba(255,255,255,0.9) 100%);
            backdrop-filter: blur(10px);
            border-radius: 16px;
            padding: 0.85rem;
            box-shadow: 0 4px 16px rgba(0,0,0,0.08);
            border: 1px solid rgba(255,255,255,0.18);
            margin-top: -0.5rem;
            margin-bottom: 1rem;'>
    <div style='display: flex; justify-content: space-around; text-align: center;'>
        {stat_items_html}
    </div>
</div>
""",
        unsafe_allow_html=True,
    )

    overall_score = report.get("overall_behavior_score", 0)
    if not overall_score and behavior_scores:
        overall_score = sum(
            data.get("score", 0) for data in behavior_scores.values()
        ) / len(behavior_scores)

    overall_score_display = overall_score * 100

    speedometer_html = create_speedometer_gauge(behavior_scores, overall_score_display)
    if speedometer_html:
        components.html(speedometer_html, height=300)
    else:
        st.info("No data available for Behavior Score gauge.")

    # Build summary stats strictly from selected section keys in config,
    # then map each KPI via its configured source and key.
    _bs_cfg = program_cfg.individual_page.behavior_summary if program_cfg.individual_page else None
    _selected_sources = list(_bs_cfg.sources) if _bs_cfg else ["behavior_skills", "core_skills"]

    _section_to_cfg = {
        "behavior_skills": (program_cfg.individual_page.behavioural_skills if program_cfg.individual_page else None),
        "core_skills": (program_cfg.individual_page.coreskills if program_cfg.individual_page else None),
    }

    def _score_or_zero(value) -> float:
        try:
            if value is None:
                return 0.0
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    all_scores_for_stats = {}
    for _section_key in _selected_sources:
        _section_cfg = _section_to_cfg.get(_section_key)
        if not _section_cfg or not _section_cfg.kpis:
            continue

        for _spec in _section_cfg.kpis:
            _src_data = source_map.get(_spec.source or "", {})
            if not isinstance(_src_data, dict):
                all_scores_for_stats[f"{_section_key}:{_spec.key}"] = {"score": 0, "delta": None}
                continue

            _kpi_data = _src_data.get(_spec.key)
            if isinstance(_kpi_data, dict):
                all_scores_for_stats[f"{_section_key}:{_spec.key}"] = {
                    "score": _score_or_zero(_kpi_data.get("score")),
                    "delta": _kpi_data.get("delta"),
                }
            else:
                all_scores_for_stats[f"{_section_key}:{_spec.key}"] = {
                    "score": _score_or_zero(_kpi_data),
                    "delta": None,
                }

    _bs_threshold = _bs_cfg.threshold if _bs_cfg else 0.5
    strong_count = sum(
        1 for data in all_scores_for_stats.values() if (data.get("score") or 0) > _bs_threshold
    )
    focus_count = sum(
        1 for data in all_scores_for_stats.values() if (data.get("score") or 0) <= _bs_threshold
    )
    _strong_label = _bs_cfg.strong_label if _bs_cfg else "STRONG"
    _strong_color = _bs_cfg.strong_color if _bs_cfg else "#0F9ED5"
    _focus_label = _bs_cfg.focus_label if _bs_cfg else "FOCUS"
    _focus_color = _bs_cfg.focus_color if _bs_cfg else "#ef4444"

    st.markdown(
        f"""
<div style='margin-top: 1rem; padding: 1rem; background: white; border-radius: 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.05);'>
    <div style='display: flex; justify-content: space-around; text-align: center;'>
        <div>
            <div style='font-size: 1.75rem; font-weight: 800; color: {_strong_color};'>{strong_count}</div>
            <div style='font-size: 0.7rem; color: #64748b; font-weight: 600;'>{_strong_label}</div>
        </div>
        <div style='width: 1px; background: #e2e8f0;'></div>
        <div>
            <div style='font-size: 1.75rem; font-weight: 800; color: {_focus_color};'>{focus_count}</div>
            <div style='font-size: 0.7rem; color: #64748b; font-weight: 600;'>{_focus_label}</div>
        </div>
    </div>
</div>
""",
        unsafe_allow_html=True,
    )

    if st.button(
        L("individual", "view_behaviors_btn", "✨ View All Behaviors"),
        key=button_key,
        width="stretch",
    ):
        st.session_state["show_behaviors_modal"] = True

    # ----- Behaviors Modal (st.dialog) -----
    @st.dialog("Performance Analytics", width="large")
    def show_behaviors_modal():
        _render_behaviors_modal(
            behavior_scores, call_handling_skills, overall_score_display, wcc_behavior, report
        )

    if st.session_state.get("show_behaviors_modal", False):
        show_behaviors_modal()
        st.session_state["show_behaviors_modal"] = False


# ---------------------------------------------------------------------------
# Private tab/modal renderers
# ---------------------------------------------------------------------------


def _render_overview_tab(report: dict, emp_id: str, score: int, program_config):
    cfg = program_config
    L = cfg.get_label
    st.markdown(f"### {L('individual', 'overview_header', 'Overview')}")

    # Section renderer dispatch map
    _section_renderers = {
        "escalations": render_escalations_section,
        "customer_experience": render_customer_experience_section,
        "sales_outcome": render_sales_outcome_section,
        "key_improvements": render_key_improvements_section,
    }

    overview_sections = (
        cfg.individual_page.overview_sections if cfg.individual_page else []
    )

    if not overview_sections:
        # Fallback: hardcoded order for configs without individual_page
        overview_sections = _default_overview_sections()

    for section in overview_sections:
        sec_type = section.type if hasattr(section, "type") else section.get("type", "")
        sec_key = section.key if hasattr(section, "key") else section.get("key", "")
        renderer_key = section.renderer if hasattr(section, "renderer") else section.get("renderer", "")

        if sec_type == "summary":
            data = report.get(sec_key, "")
            if data:
                st.info(data)

        elif sec_type == "risks":
            risks = identify_risk_areas(report, program_config=program_config)
            if risks:
                st.markdown(f"### {L('individual', 'risk_header', 'Areas Needing Attention')}")
                # for risk in risks:
                #     if risk["severity"] == "high":
                #         st.error(f"**{risk['area']}**: Current value {risk['value']} (Change: {risk['delta']})")
                #     else:
                #         st.warning(f"**{risk['area']}**: Current value {risk['value']} (Change: {risk['delta']})")

        elif sec_type == "radar":
            _radar_metrics = cfg.individual_page.radar_metrics if cfg.individual_page else []
            radar_fig = create_performance_radar(report, radar_metrics=_radar_metrics or None)
            if radar_fig:
                st.plotly_chart(radar_fig, width="stretch")
            else:
                st.info("No data available for Performance Radar.")

        elif sec_type == "section_renderer":
            data = report.get(sec_key, None)
            renderer_fn = _section_renderers.get(renderer_key)
            if data and renderer_fn:
                renderer_fn(data)
            elif renderer_fn and not data:
                _section_titles = {
                    "escalations": "🚨 Escalations",
                    "customer_experience": "😊 Customer Experience Breakdown",
                    "sales_outcome": "💰 Sales Outcome Analysis",
                    "key_improvements": "Key Improvement Areas",
                }
                _title = _section_titles.get(renderer_key, renderer_key.replace("_", " ").title())
                st.markdown(f"### {_title}")
                st.info(f"No data available for {_title.lstrip('#').strip()}.")

        elif sec_type == "coaching":
            st.markdown(f"### {L('individual', 'coaching_header', '💡 Coaching Recommendations')}")
            tips = report.get(sec_key, [])
            if tips:
                _render_coaching_tips(tips, L)
            else:
                st.info("No coaching recommendations available.")


def _default_overview_sections():
    """Fallback overview section list when no config is provided."""
    from backend.config.programs import OverviewSection
    return [
        OverviewSection(key="summary", type="summary"),
        OverviewSection(key="risks", type="risks"),
        OverviewSection(key="radar", type="radar"),
        OverviewSection(key="key_improvements", type="section_renderer", renderer="key_improvements"),
        OverviewSection(key="escalations", type="section_renderer", renderer="escalations"),
        OverviewSection(key="customer_experience", type="section_renderer", renderer="customer_experience"),
        OverviewSection(key="sales_outcome", type="section_renderer", renderer="sales_outcome"),
        OverviewSection(key="tips", type="coaching"),
    ]


def _render_coaching_tips(tips, L):
    """Render coaching tips using st.expander (same style as escalations)."""
    if not tips:
        st.info("No coaching recommendations for this period.")
        return

    st.markdown(f"Total recommendations: **{len(tips)}**")

    for idx, tip in enumerate(tips, 1):
        summary = tip.get("tip", "No tip available")
        priority = tip.get("priority", "Medium")
        expected_impact = tip.get("expected_impact", "")
        examples = tip.get("examples", [])
        actionable_steps = tip.get("actionable_steps", [])

        with st.expander(
            f"**Tip #{idx}** - {priority} Priority - {summary[:80]}{'...' if len(summary) > 80 else ''}",
            expanded=False,
        ):
            st.markdown(f"**Recommendation:** {summary}")
            st.markdown(f"**Priority:** {priority}")

            if actionable_steps:
                st.markdown("**Actionable Steps:**")
                for step in actionable_steps:
                    st.markdown(f"- {step}")

            if examples:
                st.markdown("**Examples:**")
                for ex in examples:
                    if isinstance(ex, dict):
                        date = ex.get("date_utc", "N/A")
                        contact = ex.get("contact_id", "N/A")
                        explanation = ex.get("explanation", "")
                        st.markdown(
                            f"<div style='margin:2px 0;padding:4px 8px;background:#f8f9fa;border-radius:4px;font-size:0.88rem;'>"
                            f"<b>{date}</b> · Contact <code>{contact}</code> — {explanation}</div>",
                            unsafe_allow_html=True,
                        )
                    else:
                        st.markdown(f"- {ex}")

            if expected_impact:
                st.markdown(f"**Expected Impact:** {expected_impact}")


def _render_trends_tab(report: dict):
    program_id = st.session_state.get("_program_name", DEFAULT_PROGRAM)
    _cfg = load_program_config(program_id)
    L = _cfg.get_label
    st.markdown(f"### {L('individual', 'trend_header', 'Trend Analysis')}")

    # Config-driven trends source
    _trends_cfg = _cfg.individual_page.trends if _cfg.individual_page and _cfg.individual_page.trends else None
    _trends_field = _trends_cfg.source if _trends_cfg else "trends"
    _trend_metrics = _trends_cfg.trend_metrics if _trends_cfg else []

    heatmap_fig = create_trend_heatmap(
        report,
        trend_metrics=_trend_metrics or None,
        trends_field=_trends_field,
    )
    if heatmap_fig:
        st.plotly_chart(heatmap_fig, width="stretch")
    else:
        st.info("No data available for Performance Heatmap.")

    all_trends = report.get(_trends_field, {})
    if not all_trends:
        st.info("No trend data available.")
    if all_trends:
        # Build ordered list: config-driven if available, else all
        if _trend_metrics:
            trend_items = []
            for tm in _trend_metrics:
                _pre_scaled = getattr(tm, 'pre_scaled', False)
                _mult = 1 if _pre_scaled else 100
                if tm.metric in all_trends:
                    raw_points = all_trends[tm.metric]
                    if _mult != 1:
                        raw_points = [{"x": p["x"], "y": (p.get("y") or 0) * _mult} for p in raw_points]
                    trend_items.append((tm.label or tm.metric, raw_points))
                else:
                    # Metric not in data — use 0 values with reference periods
                    ref_periods = next((all_trends[k] for k in all_trends), [])
                    trend_items.append((tm.label or tm.metric, [{"x": p["x"], "y": 0} for p in ref_periods]))
        else:
            trend_items = list(all_trends.items())

        for idx, (metric_name, points) in enumerate(trend_items):
            # Filter out points with null/None y values
            points = [p for p in points if p.get("y") is not None]
            df = pd.DataFrame(points)
            if not df.empty and "y" in df.columns:
                velocity = calculate_trend_velocity(points)
                velocity_emoji = "⬆️" if velocity == "improving" else "⬇️" if velocity == "declining" else "➡️"

                st.markdown(f"#### {metric_name} {velocity_emoji}")

                line_color = get_trend_color(idx)

                fig = go.Figure()

                fig.add_trace(
                    go.Scatter(
                        x=df["x"],
                        y=df["y"],
                        fill="tozeroy",
                        fillcolor=f"rgba({int(line_color[1:3], 16)}, {int(line_color[3:5], 16)}, {int(line_color[5:7], 16)}, 0.1)",
                        line=dict(width=0),
                        showlegend=False,
                        hoverinfo="skip",
                    )
                )

                fig.add_trace(
                    go.Scatter(
                        x=df["x"],
                        y=df["y"],
                        mode="lines+markers",
                        name=metric_name,
                        line=dict(color=line_color, width=3, shape="spline", smoothing=0.3),
                        marker=dict(
                            size=10,
                            color=line_color,
                            line=dict(color="white", width=2),
                            symbol="circle",
                        ),
                        hovertemplate="<b>%{x}</b><br>Value: %{y}<br><extra></extra>",
                    )
                )

                fig.update_layout(
                    height=350,
                    plot_bgcolor="rgba(248, 250, 252, 0.5)",
                    paper_bgcolor="white",
                    font=dict(
                        family="Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
                        size=12,
                        color="#334155",
                    ),
                    xaxis=dict(
                        title=dict(text="Week", font=dict(size=13, color="#475569", weight=600)),
                        showgrid=True,
                        gridwidth=1,
                        gridcolor="rgba(226, 232, 240, 0.8)",
                        showline=True,
                        linewidth=1,
                        linecolor="#e2e8f0",
                        tickfont=dict(size=11, color="#64748b"),
                    ),
                    yaxis=dict(
                        title=dict(text="Count", font=dict(size=13, color="#475569", weight=600)),
                        showgrid=True,
                        gridwidth=1,
                        gridcolor="rgba(226, 232, 240, 0.8)",
                        showline=True,
                        linewidth=1,
                        linecolor="#e2e8f0",
                        tickfont=dict(size=11, color="#64748b"),
                        zeroline=True,
                        zerolinewidth=1,
                        zerolinecolor="rgba(148, 163, 184, 0.3)",
                    ),
                    hovermode="x unified",
                    hoverlabel=dict(
                        bgcolor="white",
                        font_size=12,
                        font_family="Inter, sans-serif",
                        bordercolor=line_color,
                    ),
                    margin=dict(l=60, r=30, t=30, b=50),
                    showlegend=False,
                )

                st.plotly_chart(fig, width="stretch")


def _render_goals_tab():
    program_id = st.session_state.get("_program_name", DEFAULT_PROGRAM)
    L = load_program_config(program_id).get_label
    st.markdown(f"### {L('individual', 'goals_header', 'Goals & Action Items')}")

    goals = [
        {"name": "Improve AHT", "target": 300, "current": 320, "due": "2025-11-01", "status": "in_progress"},
        {"name": "Increase FCR", "target": 85, "current": 78, "due": "2025-10-15", "status": "pending"},
        {"name": "Quality Score", "target": 95, "current": 92, "due": "2025-10-30", "status": "in_progress"},
    ]

    for goal in goals:
        progress = (goal["current"] / goal["target"]) * 100
        status_color = (
            "#3498db" if goal["status"] == "in_progress" else "#f39c12" if goal["status"] == "pending" else "#27ae60"
        )
        with st.container(border=True):
            col1, col2 = st.columns([3, 1])
            with col1:
                st.markdown(f"**{goal['name']}**")
                st.caption(f"Target: {goal['target']} | Current: {goal['current']} | Due: {goal['due']}")
            with col2:
                status_label = goal["status"].replace("_", " ").title()
                st.markdown(
                    f"<span style='background: {status_color}; color: white; padding: 2px 8px; border-radius: 4px; font-size: 0.8em;'>{status_label}</span>",
                    unsafe_allow_html=True,
                )
            st.progress(min(progress / 100, 1.0))


def _render_notes_tab(emp_id: str):
    program_id = st.session_state.get("_program_name", DEFAULT_PROGRAM)
    L = load_program_config(program_id).get_label
    st.markdown(f"### {L('individual', 'notes_header', 'Coaching Notes')}")

    author = st.session_state.get("user_id", "Unknown")
    author_name = st.session_state.get("user_name", "")
    author_role = st.session_state.get("user_role", "")
    week = st.session_state.get("selected_week", get_selected_week())

    clear_key = f"_clear_note_{emp_id}"
    if st.session_state.pop(clear_key, False):
        st.session_state[f"note_input_{emp_id}"] = ""

    new_note = st.text_area(
        L("individual", "add_note_label", "Add a Note"),
        height=100,
        placeholder=L("individual", "add_note_placeholder", "Enter your coaching notes here..."),
        key=f"note_input_{emp_id}",
    )
    if st.button("💾 Save Note", key=f"save_note_{emp_id}"):
        if new_note and new_note.strip():
            add_note(emp_id, author, new_note.strip(), week=week,
                     author_name=author_name, author_role=author_role)
            log_audit_event("add_note", author, f"Employee: {emp_id} | Week: {week} | Note: {new_note.strip()[:200]}")
            st.session_state[clear_key] = True
            st.success("✅ Note saved successfully!")
            st.rerun()
        else:
            st.warning("Please enter a note before saving.")

    st.markdown(f"**{L('individual', 'recent_notes', '📅 Recent Notes')}**")
    notes = get_notes(emp_id, week=week)

    if not notes:
        st.info("No coaching notes yet. Add one above!")

    for note in notes:
        editing_key = f"editing_note_{note['id']}"
        is_editing = st.session_state.get(editing_key, False)

        with st.container(border=True):
            if is_editing:
                edited_text = st.text_area(
                    "Edit note:",
                    value=note["note"],
                    key=f"edit_text_{note['id']}",
                    height=80,
                )
                col_save, col_cancel = st.columns(2)
                with col_save:
                    if st.button("✅ Save", key=f"save_edit_{note['id']}"):
                        if edited_text and edited_text.strip():
                            edit_note(emp_id, note["id"], author, edited_text.strip())
                            log_audit_event("edit_note", author, f"Employee: {emp_id} | Week: {week} | Note ID: {note['id']}")
                            st.session_state[editing_key] = False
                            st.rerun()
                with col_cancel:
                    if st.button("❌ Cancel", key=f"cancel_edit_{note['id']}"):
                        st.session_state[editing_key] = False
                        st.rerun()
            else:
                col_text, col_actions = st.columns([8, 2])
                with col_text:
                    _display_name = note.get("author_name") or note["author"]
                    _display_role = note.get("author_role", "")
                    _role_tag = f" ({_display_role})" if _display_role else ""
                    st.caption(f"{note['date']} — {_display_name}{_role_tag}")
                    st.write(note["note"])
                with col_actions:
                    if note["author"] == author:
                        btn_cols = st.columns(2)
                        with btn_cols[0]:
                            if st.button("✏️", key=f"edit_note_{note['id']}", help="Edit this note"):
                                st.session_state[editing_key] = True
                                st.rerun()
                        with btn_cols[1]:
                            if st.button("🗑️", key=f"del_note_{note['id']}", help="Delete this note"):
                                hide_note(emp_id, note["id"], author)
                                log_audit_event("delete_note", author, f"Employee: {emp_id} | Week: {week} | Note ID: {note['id']}")
                                st.rerun()


def _render_behaviors_modal(behavior_scores, call_handling_skills, overall_score_display, wcc_behavior=None, report=None):
    """Render the content of the behaviors modal dialog."""
    program_id = st.session_state.get("_program_name", DEFAULT_PROGRAM)
    program_cfg = load_program_config(program_id)
    L = program_cfg.get_label

    # Build source_map for resolving behavior data
    _modal_source_map = {
        "behavior_scores": behavior_scores or {},
        "call_handling_and_soft_skills": call_handling_skills or {},
        "wcc_behavior": wcc_behavior or {},
        "wcc_behavior_scores": wcc_behavior or {},
        "report": report or {},
    }

    # Advanced futuristic CSS
    st.markdown("""
    <style>
    [data-testid="stDialog"] {
        background: linear-gradient(135deg, rgba(15, 23, 42, 0.95) 0%, rgba(30, 41, 59, 0.95) 100%) !important;
        backdrop-filter: blur(8px) saturate(120%) !important;
    }
    [data-testid="stMetric"] {
        background: linear-gradient(135deg, rgba(255,255,255,0.95), rgba(248,250,252,0.9));
        backdrop-filter: blur(12px);
        border-radius: 12px;
        padding: 0.85rem 1rem;
        border: 1px solid rgba(148, 163, 184, 0.2);
        box-shadow: 0 4px 16px rgba(0,0,0,0.06);
        transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        position: relative;
        overflow: hidden;
        min-height: 118px;
        box-sizing: border-box;
    }
    [data-testid="stMetric"]::before {
        content: '';
        position: absolute;
        top: 0;
        left: -100%;
        width: 100%;
        height: 100%;
        background: linear-gradient(90deg, transparent, rgba(102, 126, 234, 0.1), transparent);
        transition: left 0.5s;
    }
    [data-testid="stMetric"]:hover::before { left: 100%; }
    [data-testid="stMetric"]:hover {
        transform: translateY(-2px);
        box-shadow: 0 8px 24px rgba(102, 126, 234, 0.15);
        border-color: rgba(102, 126, 234, 0.3);
    }
    [data-testid="stMetric"] label {
        font-size: 0.7rem !important;
        font-weight: 700 !important;
        color: #64748b !important;
        text-transform: uppercase !important;
        letter-spacing: 0.08em !important;
    }
    [data-testid="stMetric"] [data-testid="stMetricValue"] {
        font-size: 1.6rem !important;
        font-weight: 900 !important;
        background: linear-gradient(135deg, #667eea, #764ba2, #f093fb);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-clip: text;
    }
    .stProgress > div > div {
        background: linear-gradient(90deg, #667eea, #764ba2, #f093fb) !important;
        border-radius: 8px !important;
        box-shadow: 0 2px 8px rgba(102, 126, 234, 0.3);
        animation: pulse-glow 2s ease-in-out infinite;
    }
    @keyframes pulse-glow {
        0%, 100% { opacity: 0.9; box-shadow: 0 2px 8px rgba(102, 126, 234, 0.3); }
        50% { opacity: 1; box-shadow: 0 2px 12px rgba(240, 147, 251, 0.5); }
    }
    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
        background: rgba(255,255,255,0.05);
        padding: 0.5rem;
        border-radius: 12px;
    }
    .stTabs [data-baseweb="tab"] {
        background: transparent;
        border-radius: 8px;
        color: #94a3b8;
        font-weight: 600;
        padding: 0.5rem 1rem;
        transition: all 0.3s ease;
    }
    .stTabs [data-baseweb="tab"]:hover {
        background: rgba(102, 126, 234, 0.1);
        color: #667eea;
    }
    .stTabs [aria-selected="true"] {
        background: linear-gradient(135deg, #667eea, #764ba2) !important;
        color: white !important;
        box-shadow: 0 4px 12px rgba(102, 126, 234, 0.3);
    }
    </style>
    """, unsafe_allow_html=True)

    st.markdown(f"""
    <div style="text-align: center; margin-bottom: 1.5rem; padding: 1rem;
                background: linear-gradient(135deg, rgba(102, 126, 234, 0.15), rgba(240, 147, 251, 0.15));
                border-radius: 14px; border: 1.5px solid rgba(102, 126, 234, 0.3);
                box-shadow: 0 4px 16px rgba(102, 126, 234, 0.1);">
        <div style="font-size: 1.1rem; font-weight: 800; background: linear-gradient(135deg, #667eea, #f093fb);
                    -webkit-background-clip: text; -webkit-text-fill-color: transparent; margin-bottom: 0.3rem;">
            {L("individual", "behavior_score_title", "Behavior Performance Score")}
        </div>
        <div style="font-size: 0.75rem; color: #64748b; font-weight: 500;">
            {L("individual", "behavior_score_subtitle", "AI-powered performance analytics")}
        </div>
    </div>
    """, unsafe_allow_html=True)

    all_behaviors = {}

    def _score_or_zero(value) -> float:
        try:
            if value is None:
                return 0.0
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    # Build label lookup from config
    _label_map = {}

    # Config-driven: iterate behavior_skills from JSON, resolve from source_map
    if program_cfg.individual_page.behavioural_skills and program_cfg.individual_page.behavioural_skills.kpis:
        for spec in program_cfg.individual_page.behavioural_skills.kpis:
            _label_map[spec.key] = spec.label or spec.key.replace("_", " ").title()
            src_data = _modal_source_map.get(spec.source or "behavior_scores", {})
            if isinstance(src_data, dict) and spec.key in src_data:
                _value = src_data[spec.key]
                if isinstance(_value, dict):
                    all_behaviors[spec.key] = {
                        "score": _value.get("score"),
                        "delta": _value.get("delta"),
                        "_score_num": _score_or_zero(_value.get("score")),
                    }
                else:
                    all_behaviors[spec.key] = {
                        "score": _value,
                        "delta": None,
                        "_score_num": _score_or_zero(_value),
                    }
            else:
                all_behaviors[spec.key] = {"score": None, "delta": None, "_score_num": 0.0}
    else:
        # Fallback: use raw behavior_scores
        all_behaviors = dict(behavior_scores)

    # Detect scale: if any score > 1, data is already 0-100
    _above_1 = any(d.get("_score_num", 0) > 1 for d in all_behaviors.values())
    _threshold = 50 if _above_1 else 0.5

    all_items = list(all_behaviors.items())
    # Keep the original JSON order after filtering into tabs.
    needs_focus = [(b, d) for b, d in all_items if d.get("_score_num", 0) <= _threshold]
    strong = [(b, d) for b, d in all_items if d.get("_score_num", 0) > _threshold]

    # Build category lookup from config (only "main" KPIs count toward avg)
    _category_map = {}
    if program_cfg.individual_page.behavioural_skills and program_cfg.individual_page.behavioural_skills.kpis:
        _category_map = {spec.key: (spec.category or "main") for spec in program_cfg.individual_page.behavioural_skills.kpis}
    _main_scores = [
        data.get("_score_num", 0)
        for key, data in all_behaviors.items()
        if _category_map.get(key, "main") == "main"
    ]
    avg_score = (sum(_main_scores) / len(_main_scores)) if _main_scores else 0
    if not _above_1:
        avg_score = avg_score * 100

    col1, col2, col3 = st.columns(3, gap="small")
    with col1:
        st.metric(L("individual", "avg_score_label", "📊 Avg Score"), f"{avg_score:.0f}", delta=None)
    with col2:
        st.metric(L("individual", "total_label", "📈 Total"), len(all_behaviors), delta=None)
    with col3:
        st.metric(L("individual", "strong_count_label", "⭐ Strong"), len(strong), delta=None)

    st.markdown("<div style='margin: 1.5rem 0'></div>", unsafe_allow_html=True)

    # Build tabs — read core skill keys from program config
    if program_cfg.individual_page.coreskills and program_cfg.individual_page.coreskills.kpis:
        soft_skill_specs = program_cfg.individual_page.coreskills.kpis
    else:
        soft_skill_specs = None

    soft_skills_list = []
    if soft_skill_specs:
        for spec in soft_skill_specs:
            _label_map[spec.key] = spec.label or spec.key.replace("_", " ").title()
            # Read from the source defined in config (e.g. "call_handling_and_soft_skills")
            src_data = report.get(spec.source, {}) if report and spec.source else call_handling_skills
            if isinstance(src_data, dict) and spec.key in src_data:
                soft_skills_list.append((spec.key, src_data[spec.key]))
            elif spec.key in call_handling_skills:
                soft_skills_list.append((spec.key, call_handling_skills[spec.key]))
            else:
                soft_skills_list.append((spec.key, {"score": None, "delta": None}))
    else:
        # Fallback: hardcoded keys
        for skill_name in ["Comprehension", "Language Proficiency", "Emotional Intelligence",
                           "Relationship Building", "Professional Skills", "Subject Matter Expertise"]:
            if skill_name in call_handling_skills:
                soft_skills_list.append((skill_name, call_handling_skills[skill_name]))
            else:
                soft_skills_list.append((skill_name, {"score": None, "delta": None}))

    tab_names = []
    tab_data_legacy = []

    tab_names.append(
        f"Core Skills ({len([s for s in soft_skills_list if s[1].get('score') is not None])})"
    )
    tab_data_legacy.append(("soft_skills", soft_skills_list, "#1e40af"))

    # Second tab: named from behavior_skills config
    _bs_cfg = program_cfg.individual_page.behavioural_skills
    _bs_tab_label = _bs_cfg.name if _bs_cfg else "Behavior Skills"
    tab_names.append(f"{_bs_tab_label} ({len(all_items)})")
    tab_data_legacy.append(None)  # placeholder — handled separately

    if tab_names:
        tabs = st.tabs(tab_names)

        # --- Tab 0: Core Skills (unchanged) ---
        with tabs[0]:
            _core_items = tab_data_legacy[0][1]
            for i in range(0, len(_core_items), 3):
                cols = st.columns(3, gap="small")
                for col_idx in range(3):
                    if i + col_idx < len(_core_items):
                        behavior, data = _core_items[i + col_idx]
                        score = data.get("score")
                        delta = data.get("delta")
                        with cols[col_idx]:
                            with st.container():
                                display_name = _label_map.get(behavior, behavior.replace("_", " ").title())
                                if score is None:
                                    st.metric(label=display_name, value="N/A", delta=None)
                                    st.progress(0)
                                else:
                                    if score > 1:
                                        score_display = score
                                        progress_val = min(score / 100, 1.0)
                                    else:
                                        score_display = score * 100
                                        progress_val = score
                                    delta_display = delta * 100 if delta and delta != 0 else 0
                                    if delta and delta != 0:
                                        st.metric(
                                            label=display_name,
                                            value=f"{score_display:.0f}",
                                            delta=f"{'+' if delta_display > 0 else ''}{delta_display:.1f}",
                                        )
                                    else:
                                        st.metric(label=display_name, value=f"{score_display:.0f}")
                                    st.progress(max(0.0, min(1.0, progress_val)))

        # --- Tab 1: Behavior Skills — two-column layout (Need Focus | Strong) ---
        with tabs[1]:
            _focus_color = program_cfg.individual_page.behavior_summary.focus_color if program_cfg.individual_page.behavior_summary else "#ef4444"
            _strong_color = program_cfg.individual_page.behavior_summary.strong_color if program_cfg.individual_page.behavior_summary else "#0F9ED5"

            # CSS: bordered KPI cards + scrollable columns via JS-injected class
            st.markdown(f"""
            <style>
            .bhv-col-focus {{
                border: 1.5px solid {_focus_color}40;
                border-radius: 12px;
                padding: 0.6rem;
                background: linear-gradient(180deg, {_focus_color}08 0%, transparent 100%);
            }}
            .bhv-col-strong {{
                border: 1.5px solid {_strong_color}40;
                border-radius: 12px;
                padding: 0.6rem;
                background: linear-gradient(180deg, {_strong_color}08 0%, transparent 100%);
            }}
            .bhv-scroll-inner {{
                overflow-y: auto !important;
                padding-right: 2px;
                # padding-bottom: 10px;
                max-height: 35vh;
            }}
            .bhv-scroll-inner::-webkit-scrollbar {{
                width: 6px;
            }}
            .bhv-col-focus::-webkit-scrollbar-thumb {{
                background: {_focus_color}50;
                border-radius: 3px;
            }}
            .bhv-col-strong::-webkit-scrollbar-thumb {{
                background: {_strong_color}50;
                border-radius: 3px;
            }}
            .bhv-col-focus [data-testid="stMetric"],
            .bhv-col-strong [data-testid="stMetric"] {{
                border: 1px solid rgba(148, 163, 184, 0.25);
                border-radius: 10px;
                padding: 0.7rem 0.8rem;
                box-shadow: 0 2px 8px rgba(0,0,0,0.04);
                background: linear-gradient(135deg, rgba(255,255,255,0.97), rgba(248,250,252,0.92));
                min-height: 110px;
                box-sizing: border-box;
            }}
            .bhv-col-focus [data-testid="stMetric"] {{
                border-left: 3px solid {_focus_color}60;
            }}
            .bhv-col-strong [data-testid="stMetric"] {{
                border-left: 3px solid {_strong_color}60;
            }}
            </style>
            """, unsafe_allow_html=True)

            col_focus, col_strong = st.columns(2, gap="medium")

            # --- Left column: Need Focus ---
            with col_focus:
                st.markdown(
                    f"<div id='bhv-focus-marker'></div>"
                    f"<div style='width:100%;padding:0.55rem 0;margin-bottom:0.6rem;"
                    f"background:linear-gradient(135deg,{_focus_color}18,{_focus_color}08);"
                    f"border:1.5px solid {_focus_color}35;border-radius:10px;"
                    f"text-align:center;font-weight:800;font-size:0.9rem;"
                    f"color:{_focus_color};letter-spacing:0.04em;"
                    f"box-shadow:0 2px 8px {_focus_color}12;'>"
                    f"\U0001f534 NEED FOCUS &nbsp;·&nbsp; {len(needs_focus)}</div>",
                    unsafe_allow_html=True,
                )
                with st.container():
                    st.markdown("<div id='bhv-focus-scroll-start'></div>", unsafe_allow_html=True)
                    for i in range(0, len(needs_focus), 2):
                        sub_cols = st.columns(2, gap="small")
                        for col_idx in range(2):
                            if i + col_idx < len(needs_focus):
                                behavior, data = needs_focus[i + col_idx]
                                score = data.get("score")
                                delta = data.get("delta")
                                with sub_cols[col_idx]:
                                    with st.container():
                                        display_name = _label_map.get(behavior, behavior.replace("_", " ").title())
                                        if score is None:
                                            st.metric(label=display_name, value="N/A", delta=None)
                                            st.progress(0)
                                        else:
                                            if score > 1:
                                                score_display = score
                                                progress_val = min(score / 100, 1.0)
                                            else:
                                                score_display = score * 100
                                                progress_val = score
                                            delta_display = delta * 100 if delta and delta != 0 else 0
                                            if delta and delta != 0:
                                                st.metric(
                                                    label=display_name,
                                                    value=f"{score_display:.0f}",
                                                    delta=f"{'+' if delta_display > 0 else ''}{delta_display:.1f}",
                                                )
                                            else:
                                                st.metric(label=display_name, value=f"{score_display:.0f}")
                                            st.progress(max(0.0, min(1.0, progress_val)))
                    if not needs_focus:
                        st.info("No behaviors below threshold")

            # --- Right column: Strong ---
            with col_strong:
                st.markdown(
                    f"<div id='bhv-strong-marker'></div>"
                    f"<div style='width:100%;padding:0.55rem 0;margin-bottom:0.6rem;"
                    f"background:linear-gradient(135deg,{_strong_color}18,{_strong_color}08);"
                    f"border:1.5px solid {_strong_color}35;border-radius:10px;"
                    f"text-align:center;font-weight:800;font-size:0.9rem;"
                    f"color:{_strong_color};letter-spacing:0.04em;"
                    f"box-shadow:0 2px 8px {_strong_color}12;'>"
                    f"\U0001f7e2 STRONG &nbsp;·&nbsp; {len(strong)}</div>",
                    unsafe_allow_html=True,
                )
                with st.container():
                    st.markdown("<div id='bhv-strong-scroll-start'></div>", unsafe_allow_html=True)
                    for i in range(0, len(strong), 2):
                        sub_cols = st.columns(2, gap="small")
                        for col_idx in range(2):
                            if i + col_idx < len(strong):
                                behavior, data = strong[i + col_idx]
                                score = data.get("score")
                                delta = data.get("delta")
                                with sub_cols[col_idx]:
                                    with st.container():
                                        display_name = _label_map.get(behavior, behavior.replace("_", " ").title())
                                        if score is None:
                                            st.metric(label=display_name, value="N/A", delta=None)
                                            st.progress(0)
                                        else:
                                            if score > 1:
                                                score_display = score
                                                progress_val = min(score / 100, 1.0)
                                            else:
                                                score_display = score * 100
                                                progress_val = score
                                            delta_display = delta * 100 if delta and delta != 0 else 0
                                            if delta and delta != 0:
                                                st.metric(
                                                    label=display_name,
                                                    value=f"{score_display:.0f}",
                                                    delta=f"{'+' if delta_display > 0 else ''}{delta_display:.1f}",
                                                )
                                            else:
                                                st.metric(label=display_name, value=f"{score_display:.0f}")
                                            st.progress(max(0.0, min(1.0, progress_val)))
                    if not strong:
                        st.info("No behaviors above threshold")

            # JS to apply scroll classes to the stColumn containers (outer border)
            # and to the inner content block below the header (scroll)
            components.html("""
            <script>
            const root = window.parent.document;
            function applyClasses(markerId, colCls, scrollMarkerId) {
                const marker = root.getElementById(markerId);
                if (!marker) return;
                // Apply border class to the column
                let col = marker.closest('[data-testid="stColumn"]');
                if (col && !col.classList.contains(colCls)) {
                    col.classList.add(colCls);
                }
                // Apply scroll class to the inner content container
                const scrollMarker = root.getElementById(scrollMarkerId);
                if (!scrollMarker) return;
                let block = scrollMarker.closest('[data-testid="stVerticalBlockBorderWrapper"]')
                          || scrollMarker.closest('[data-testid="stVerticalBlock"]');
                if (block && !block.classList.contains('bhv-scroll-inner')) {
                    block.classList.add('bhv-scroll-inner');
                }
            }
            let tries = 0;
            const iv = setInterval(() => {
                applyClasses('bhv-focus-marker', 'bhv-col-focus', 'bhv-focus-scroll-start');
                applyClasses('bhv-strong-marker', 'bhv-col-strong', 'bhv-strong-scroll-start');
                tries++;
                if (tries > 30) clearInterval(iv);
            }, 150);
            </script>
            """, height=0)
