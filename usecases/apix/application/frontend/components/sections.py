"""
ui/sections.py — Report section renderers and KPI detail modal
===============================================================
"""

from typing import List, Optional

import plotly.graph_objects as go
import streamlit as st

from frontend.components.charts import create_speedometer_gauge
from backend.config.programs import load_program_config, DEFAULT_PROGRAM


def _L(section: str, key: str, default: str = "") -> str:
    """Shorthand to fetch a UI label from the active program config."""
    program_id = st.session_state.get("selected_program", DEFAULT_PROGRAM)
    return load_program_config(program_id).get_label(section, key, default)


def render_escalations_section(escalations: List[dict]):
    """Render escalations section with clickable transcript excerpts"""
    if not escalations:
        st.info("No escalations recorded for this period.")
        return

    st.markdown('<div id="escalations-section"></div>', unsafe_allow_html=True)
    st.markdown("### 🚨 Escalations")
    st.markdown(f"Total escalations: **{len(escalations)}**")

    for idx, escalation in enumerate(escalations, 1):
        summary = escalation.get("summary", "No summary available")
        priority = escalation.get("priority", "Medium")
        contact_id = escalation.get("contact_id", "N/A")
        date_utc = escalation.get("date_utc", "N/A")
        transcript_url = escalation.get("transcript_url", "")
        transcript_excerpt = escalation.get("transcript_excerpt", [])

        with st.expander(
            f"**Escalation #{idx}** - {priority} Priority - {date_utc}",
            expanded=False,
        ):
            st.markdown(f"**Summary:** {summary}")
            st.markdown(f"**Priority:** {priority}")
            st.markdown(f"**Contact ID:** `{contact_id}`")
            st.markdown(f"**Date:** {date_utc}")

            if transcript_url:
                st.markdown(f"[View Full Transcript]({transcript_url})")

            if transcript_excerpt:
                st.markdown("**Transcript Excerpt:**")
                for line in transcript_excerpt:
                    speaker = line.get("speaker", "Unknown")
                    text = line.get("text", "")
                    st.markdown(
                        f"<div style='margin:1px 0;'><span style='font-weight:600;'>{speaker}:</span> {text}</div>",
                        unsafe_allow_html=True,
                    )


def render_customer_experience_section(customer_experience: dict):
    """Render customer experience breakdown with visualizations"""
    if not customer_experience:
        return

    st.markdown(
        '<div id="customer-experience-section"></div>', unsafe_allow_html=True
    )
    st.markdown("### 😊 Customer Experience Breakdown")

    categories = []
    counts = []

    for category, data in customer_experience.items():
        count = data.get("count", 0)
        if count > 0:
            categories.append(category)
            counts.append(count)

    if not categories:
        st.info("No customer experience data available.")
        return

    cols = st.columns(len(categories))
    for idx, (category, count) in enumerate(zip(categories, counts)):
        with cols[idx]:
            badge_class = f"experience-{category.lower()}"
            st.markdown(
                f"""
            <div class="{badge_class}" style="text-align: center; padding: 1rem; border-radius: 8px;">
                <div style="font-size: 1.5rem; font-weight: 700;">{count}</div>
                <div style="font-size: 0.875rem; font-weight: 600;">{category}</div>
            </div>
            """,
                unsafe_allow_html=True,
            )

    if len(categories) > 1:
        fig = go.Figure(
            data=[
                go.Bar(
                    x=categories,
                    y=counts,
                    marker_color=[
                        "#059669" if c == "Good" else "#d97706" if c == "Medium" else "#dc2626"
                        for c in categories
                    ],
                    text=counts,
                    textposition="auto",
                )
            ]
        )

        fig.update_layout(
            title=_L("sections", "customer_xs_chart_title", "Customer Experience Distribution"),
            xaxis_title=_L("sections", "customer_xs_xaxis", "Experience Level"),
            yaxis_title="Count",
            height=350,
            showlegend=False,
        )

        st.plotly_chart(fig, width="stretch")


def render_sales_outcome_section(sales_outcome: dict):
    """Render sales outcome breakdown with visualizations"""
    if not sales_outcome:
        return

    st.markdown('<div id="sales-outcome-section"></div>', unsafe_allow_html=True)
    st.markdown(f"### {_L('sections', 'sales_section', '💰 Sales Outcome Analysis')}")

    categories = []
    counts = []

    for category, data in sales_outcome.items():
        count = data.get("count", 0)
        if count > 0:
            categories.append(category)
            counts.append(count)

    if not categories:
        st.info("No sales outcome data available.")
        return

    cols = st.columns(len(categories))
    for idx, (category, count) in enumerate(zip(categories, counts)):
        with cols[idx]:
            badge_class = (
                "experience-good"
                if category == "Closed deal"
                else "experience-medium"
                if "progress" in category
                else "experience-poor"
            )
            st.markdown(
                f"""
            <div class="{badge_class}" style="text-align: center; padding: 1rem; border-radius: 8px;">
                <div style="font-size: 1.5rem; font-weight: 700;">{count}</div>
                <div style="font-size: 0.875rem; font-weight: 600;">{category}</div>
            </div>
            """,
                unsafe_allow_html=True,
            )

    if len(categories) > 1:
        colors = [
            "#059669" if c == "Closed deal" else "#d97706" if "progress" in c else "#dc2626"
            for c in categories
        ]

        fig = go.Figure(
            data=[go.Pie(labels=categories, values=counts, hole=0.3, marker_colors=colors)]
        )

        fig.update_layout(title=_L("sections", "sales_chart_title", "Sales Outcome Distribution"), height=350)
        st.plotly_chart(fig, width="stretch")


def render_key_improvements_section(key_improvements: List[str]):
    """Render key improvements as a bullet list"""
    if not key_improvements:
        return

    st.markdown(f"### {_L('sections', 'improvement_areas', 'Key Improvement Areas')}")
    for improvement in key_improvements:
        st.markdown(f"- {improvement}")


def render_behavior_scores_section(behavior_scores: dict):
    """Render behavior scores with speedometer gauge and individual metrics"""
    if not behavior_scores:
        st.info("No behavior score data available.")
        return

    st.markdown(f"### {_L('sections', 'behavior_section', 'Behavior Performance Analysis')}")

    col_gauge, col_breakdown = st.columns([1, 2])

    with col_gauge:
        speedometer_fig = create_speedometer_gauge(behavior_scores)
        if speedometer_fig:
            st.plotly_chart(speedometer_fig, width="stretch")

    with col_breakdown:
        st.markdown(f"#### {_L('sections', 'behavior_individual', 'Individual Behavior Scores')}")

        sorted_behaviors = sorted(
            behavior_scores.items(), key=lambda x: x[1].get("score", 0)
        )

        for i in range(0, len(sorted_behaviors), 2):
            cols = st.columns(2)

            for col_idx in range(2):
                if i + col_idx < len(sorted_behaviors):
                    behavior, data = sorted_behaviors[i + col_idx]
                    score = data.get("score", 0)
                    delta = data.get("delta", 0)

                    if score >= 70:
                        color = "#0F9ED5"
                        bg_color = "rgba(15, 158, 213, 0.1)"
                        status = "Excellent"
                    elif score >= 40:
                        color = "#d97706"
                        bg_color = "rgba(217, 119, 6, 0.1)"
                        status = "Moderate"
                    else:
                        color = "#ef4444"
                        bg_color = "rgba(239, 68, 68, 0.1)"
                        status = "Needs Focus"

                    delta_html = ""
                    if delta != 0:
                        delta_color = "#059669" if delta > 0 else "#dc2626"
                        delta_symbol = "↑" if delta > 0 else "↓"
                        delta_html = f"""
                        <div style='display: inline-block; margin-left: 0.5rem;'>
                            <span style='color: {delta_color}; font-size: 0.85rem; font-weight: 600;'>
                                {delta_symbol} {abs(delta)}
                            </span>
                        </div>
                        """

                    with cols[col_idx]:
                        st.markdown(
                            f"""
                        <div style='background: {bg_color}; padding: 1rem; margin: 0.5rem 0;
                                    border-left: 4px solid {color}; border-radius: 12px;
                                    box-shadow: 0 2px 8px rgba(0,0,0,0.05);
                                    transition: all 0.3s ease;'>
                            <div style='display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.5rem;'>
                                <div style='font-size: 0.85rem; color: #334155; font-weight: 600;'>
                                    {behavior}
                                </div>
                                <div style='background: white; padding: 0.25rem 0.75rem; border-radius: 8px;'>
                                    <span style='font-size: 0.75rem; color: {color}; font-weight: 700;'>
                                        {status}
                                    </span>
                                </div>
                            </div>
                            <div style='display: flex; align-items: center; gap: 1rem;'>
                                <div style='flex: 1;'>
                                    <div style='background: rgba(255,255,255,0.6); height: 8px; border-radius: 4px; overflow: hidden;'>
                                        <div style='background: {color}; height: 100%; width: {score}%; border-radius: 4px;
                                                    transition: width 0.5s ease;'></div>
                                    </div>
                                </div>
                                <div style='display: flex; align-items: center;'>
                                    <span style='font-size: 1.5rem; font-weight: 800; color: {color};'>
                                        {score}
                                    </span>
                                    {delta_html}
                                </div>
                            </div>
                        </div>
                        """,
                            unsafe_allow_html=True,
                        )

    st.markdown("---")
    st.markdown(f"#### {_L('sections', 'insights_header', '💡 Key Insights')}")

    avg_score = sum(data.get("score", 0) for data in behavior_scores.values()) / len(
        behavior_scores
    )
    strong_areas = [
        name for name, data in behavior_scores.items() if data.get("score", 0) >= 70
    ]
    focus_areas = [
        name for name, data in behavior_scores.items() if data.get("score", 0) < 40
    ]

    col1, col2, col3 = st.columns(3)

    with col1:
        st.markdown(
            f"""
        <div style='background: linear-gradient(135deg, #0F9ED5 0%, #3C1EBA 100%);
                    padding: 1.5rem; border-radius: 12px; text-align: center; color: white;'>
            <div style='font-size: 0.75rem; font-weight: 600; text-transform: uppercase;
                        letter-spacing: 0.05em; opacity: 0.9; margin-bottom: 0.5rem;'>
                {_L("sections", "avg_score_label", "Average Score")}
            </div>
            <div style='font-size: 2.5rem; font-weight: 800;'>
                {avg_score:.1f}
            </div>
        </div>
        """,
            unsafe_allow_html=True,
        )

    with col2:
        st.markdown(
            f"""
        <div style='background: linear-gradient(135deg, #059669 0%, #047857 100%);
                    padding: 1.5rem; border-radius: 12px; text-align: center; color: white;'>
            <div style='font-size: 0.75rem; font-weight: 600; text-transform: uppercase;
                        letter-spacing: 0.05em; opacity: 0.9; margin-bottom: 0.5rem;'>
                {_L("sections", "strong_areas_label", "Strong Areas")}
            </div>
            <div style='font-size: 2.5rem; font-weight: 800;'>
                {len(strong_areas)}
            </div>
        </div>
        """,
            unsafe_allow_html=True,
        )

    with col3:
        st.markdown(
            f"""
        <div style='background: linear-gradient(135deg, #dc2626 0%, #b91c1c 100%);
                    padding: 1.5rem; border-radius: 12px; text-align: center; color: white;'>
            <div style='font-size: 0.75rem; font-weight: 600; text-transform: uppercase;
                        letter-spacing: 0.05em; opacity: 0.9; margin-bottom: 0.5rem;'>
                {_L("sections", "focus_areas_label", "Focus Areas")}
            </div>
            <div style='font-size: 2.5rem; font-weight: 800;'>
                {len(focus_areas)}
            </div>
        </div>
        """,
            unsafe_allow_html=True,
        )

    if strong_areas or focus_areas:
        col_strong, col_focus = st.columns(2)

        with col_strong:
            if strong_areas:
                st.markdown(f"**{_L('sections', 'strong_behaviors', '✅ Strong Behaviors:')}**")
                for area in strong_areas[:5]:
                    score = behavior_scores[area].get("score", 0)
                    st.markdown(f"- **{area}** ({score})")

        with col_focus:
            if focus_areas:
                st.markdown(f"**{_L('sections', 'needs_attention', '⚠️ Needs Attention:')}**")
                for area in focus_areas[:5]:
                    score = behavior_scores[area].get("score", 0)
                    st.markdown(f"- **{area}** ({score})")


def show_kpi_details_modal(
    kpi_key: str, kpi_label: str, kpi_value, kpi_delta, report: dict, modal_id: int
):
    """Show detailed information for a KPI in a modern modal popup"""

    st.markdown('<div class="modal-backdrop"></div>', unsafe_allow_html=True)

    delta_emoji = "📈" if kpi_delta and kpi_delta > 0 else "📉" if kpi_delta and kpi_delta < 0 else "➡️"
    delta_sign = "+" if kpi_delta and kpi_delta > 0 else ""

    _detailed = _L("sections", "detailed_analytics", "Detailed Analytics")
    _comprehensive = _L("sections", "comprehensive_insights", "Comprehensive insights and performance metrics")
    _current_val = _L("sections", "current_value", "Current Value")

    st.markdown(
        f'''
    <div class="modal-container">
        <div class="modal-header">
            <div style="position: absolute; top: 2rem; right: 2.5rem; z-index: 100;">
                <div style="background: rgba(255, 255, 255, 0.2); backdrop-filter: blur(10px);
                            border: 2px solid rgba(255, 255, 255, 0.3); color: white;
                            font-size: 1.5rem; width: 56px; height: 56px; border-radius: 18px;
                            cursor: pointer; display: flex; align-items: center; justify-content: center;
                            font-weight: 400; transition: all 0.3s ease;
                            box-shadow: 0 8px 24px rgba(0,0,0,0.15);"
                     onmouseover="this.style.background=\'rgba(255,255,255,0.3)\'; this.style.transform=\'rotate(90deg) scale(1.1)\';"
                     onmouseout="this.style.background=\'rgba(255,255,255,0.2)\'; this.style.transform=\'rotate(0deg) scale(1)\';">
                    <span style="display: block; margin-top: -2px;">✕</span>
                </div>
            </div>
            <div style="position: relative; z-index: 2;">
                <div style="display: flex; justify-content: space-between; align-items: start; gap: 3rem; padding-right: 80px;">
                    <div style="flex: 1;">
                        <div style="font-size: 0.85rem; font-weight: 600; text-transform: uppercase;
                                    letter-spacing: 0.1em; opacity: 0.8; margin-bottom: 0.75rem;">
                            {_detailed}
                        </div>
                        <h2 style="margin: 0; font-size: 2.25rem; font-weight: 800; line-height: 1.2;
                                    text-shadow: 0 2px 10px rgba(0,0,0,0.1);">
                            {kpi_label}
                        </h2>
                        <p style="margin: 0.75rem 0 0 0; font-size: 1rem; opacity: 0.9; font-weight: 400;">
                            {_comprehensive}
                        </p>
                    </div>
                    <div style="display: flex; flex-direction: column; align-items: flex-end; gap: 0.5rem;">
                        <div style="background: rgba(255,255,255,0.15); backdrop-filter: blur(10px);
                                    padding: 1.25rem 2rem; border-radius: 20px; text-align: right;
                                    border: 1px solid rgba(255,255,255,0.2);
                                    box-shadow: 0 8px 32px rgba(0,0,0,0.1);">
                            <div style="font-size: 0.75rem; font-weight: 600; text-transform: uppercase;
                                        letter-spacing: 0.1em; opacity: 0.8; margin-bottom: 0.5rem;">
                                {_current_val}
                            </div>
                            <div style="font-size: 3.5rem; font-weight: 900; line-height: 1;
                                        text-shadow: 0 4px 20px rgba(0,0,0,0.15);">
                                {kpi_value}
                            </div>
                        </div>
                        <div style="background: rgba(255,255,255,0.12); backdrop-filter: blur(8px);
                                    padding: 0.5rem 1.25rem; border-radius: 12px;
                                    border: 1px solid rgba(255,255,255,0.15);
                                    display: flex; align-items: center; gap: 0.5rem;">
                            <span style="font-size: 1.25rem;">{delta_emoji}</span>
                            <span style="font-size: 1rem; font-weight: 700;">
                                {delta_sign}{abs(kpi_delta) if kpi_delta else 0}
                            </span>
                            <span style="font-size: 0.85rem; opacity: 0.8; font-weight: 500;">vs last period</span>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    ''',
        unsafe_allow_html=True,
    )

    st.markdown(
        '<div class="modal-body" style="background: white; padding: 3rem; max-height: calc(90vh - 180px); overflow-y: auto;">',
        unsafe_allow_html=True,
    )

    # Close button styling
    st.markdown(
        """
    <style>
    div[data-testid="column"] button[key*="close_modal_top"] {
        background: linear-gradient(135deg, #ef4444, #dc2626) !important;
        color: white !important;
        border: 2px solid rgba(255,255,255,0.4) !important;
        padding: 0.875rem 2rem !important;
        border-radius: 16px !important;
        font-weight: 700 !important;
        font-size: 1.1rem !important;
        box-shadow: 0 12px 32px rgba(239, 68, 68, 0.4) !important;
        transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1) !important;
    }
    div[data-testid="column"] button[key*="close_modal_top"]:hover {
        transform: translateY(-3px) scale(1.08) !important;
        box-shadow: 0 16px 40px rgba(239, 68, 68, 0.5) !important;
        background: linear-gradient(135deg, #dc2626, #b91c1c) !important;
    }
    </style>
    """,
        unsafe_allow_html=True,
    )

    col1, col2, col3 = st.columns([1, 1, 1])
    with col2:
        if st.button("✕ Close Modal", key=f"close_modal_top_{modal_id}", width="stretch"):
            st.session_state[f"show_kpi_modal_{modal_id}"] = False
            st.rerun()

    st.markdown("<br>", unsafe_allow_html=True)

    # Content section based on KPI type
    if kpi_key == "escalations":
        _render_escalation_modal_content(report)
    elif kpi_key in ["vxs", "vxs_solutions"]:
        _render_vxs_modal_content(kpi_key, kpi_value, kpi_delta, report)

    # Bottom close button
    st.markdown("<br><br>", unsafe_allow_html=True)
    col1, col2, col3 = st.columns([3, 2, 3])
    with col2:
        st.markdown(
            """
        <style>
        div[data-testid="column"] button[key*="close_modal_bottom"] {
            background: linear-gradient(135deg, #0F9ED5 0%, #3C1EBA 100%) !important;
            color: white !important;
            border: none !important;
            padding: 0.875rem 2rem !important;
            border-radius: 16px !important;
            font-weight: 600 !important;
            font-size: 1rem !important;
            box-shadow: 0 12px 40px rgba(15, 158, 213, 0.4) !important;
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1) !important;
        }
        div[data-testid="column"] button[key*="close_modal_bottom"]:hover {
            transform: translateY(-2px) !important;
            box-shadow: 0 16px 48px rgba(15, 158, 213, 0.5) !important;
        }
        </style>
        """,
            unsafe_allow_html=True,
        )

        if st.button("✓ Got it, Close", key=f"close_modal_bottom_{modal_id}", width="stretch"):
            st.session_state[f"show_kpi_modal_{modal_id}"] = False
            st.rerun()

    st.markdown("</div></div>", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Modal sub-renderers (private helpers)
# ---------------------------------------------------------------------------


def _render_escalation_modal_content(report: dict):
    """Render the escalation details inside a KPI modal."""
    st.markdown(
        """
    <div style="margin-bottom: 2rem;">
        <h3 style="font-size: 1.75rem; font-weight: 800;
                   background: linear-gradient(135deg, #0F9ED5, #3C1EBA);
                   -webkit-background-clip: text;
                   -webkit-text-fill-color: transparent;
                   background-clip: text;
                   margin-bottom: 0.5rem;">
            {_L("sections", "escalation_title", "🚨 Escalation Management Dashboard")}
        </h3>
        <p style="color: #64748b; font-size: 1rem; margin: 0;">
            {_L("sections", "escalation_subtitle", "Review and analyze customer escalations with detailed insights")}
        </p>
    </div>
    """,
        unsafe_allow_html=True,
    )

    escalations = report.get("escalations", [])

    st.markdown(
        f"""
    <div style='background: linear-gradient(135deg, #0F9ED5, #3C1EBA);
                padding: 1rem 1.5rem; border-radius: 12px; margin: 1rem 0;
                color: white; font-size: 1.1rem; font-weight: 600;
                box-shadow: 0 4px 12px rgba(15, 158, 213, 0.3);'>
        📊 Analyzing <strong style='font-size: 1.5rem;'>{len(escalations)}</strong> escalation(s)
    </div>
    """,
        unsafe_allow_html=True,
    )

    if escalations:
        col_a, col_b, col_c = st.columns(3)

        with col_a:
            st.markdown(
                f"""
            <div style='background: white; padding: 1.5rem; border-radius: 16px;
                        border-left: 5px solid #1e40af; box-shadow: 0 4px 12px rgba(0,0,0,0.08);'>
                <div style='font-size: 0.85rem; color: #64748b; font-weight: 600;
                            text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 0.5rem;'>
                    {_L("sections", "total_escalations", "Total Escalations")}
                </div>
                <div style='font-size: 3rem; font-weight: 800; color: #1e40af;'>{len(escalations)}</div>
            </div>
            """,
                unsafe_allow_html=True,
            )

        with col_b:
            high_priority = sum(
                1 for e in escalations if e.get("priority", "") in ["High", "Very High"]
            )
            st.markdown(
                f"""
            <div style='background: white; padding: 1.5rem; border-radius: 16px;
                        border-left: 5px solid #dc2626; box-shadow: 0 4px 12px rgba(0,0,0,0.08);'>
                <div style='font-size: 0.85rem; color: #64748b; font-weight: 600;
                            text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 0.5rem;'>
                    {_L("sections", "high_priority", "High Priority")}
                </div>
                <div style='font-size: 3rem; font-weight: 800; color: #dc2626;'>{high_priority}</div>
            </div>
            """,
                unsafe_allow_html=True,
            )

        with col_c:
            avg_per_week = len(escalations) / 4
            st.markdown(
                f"""
            <div style='background: white; padding: 1.5rem; border-radius: 16px;
                        border-left: 5px solid #d97706; box-shadow: 0 4px 12px rgba(0,0,0,0.08);'>
                <div style='font-size: 0.85rem; color: #64748b; font-weight: 600;
                            text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 0.5rem;'>
                    {_L("sections", "avg_per_week", "Avg/Week")}
                </div>
                <div style='font-size: 3rem; font-weight: 800; color: #d97706;'>{avg_per_week:.1f}</div>
            </div>
            """,
                unsafe_allow_html=True,
            )

        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown(f"### {_L('sections', 'detailed_escalations', '📋 Detailed Escalations')}")

        for idx, esc in enumerate(escalations, 1):
            priority = esc.get("priority", "Medium")
            summary = esc.get("summary", "No summary available")
            date_utc = esc.get("date_utc", "N/A")
            contact_id = esc.get("contact_id", "N/A")
            transcript_url = esc.get("transcript_url", "")
            transcript_excerpt = esc.get("transcript_excerpt", [])

            priority_colors = {
                "Very High": ("#dc2626", "🔴", "#fef2f2"),
                "High": ("#ef4444", "🔴", "#fee2e2"),
                "Medium": ("#d97706", "🟡", "#fef3c7"),
                "Low": ("#059669", "🟢", "#d1fae5"),
            }
            color, emoji, bg_color = priority_colors.get(priority, ("#64748b", "⚪", "#f8fafc"))

            with st.expander(
                f"{emoji} **Escalation #{idx}** - {priority} Priority | {date_utc}",
                expanded=(idx == 1),
            ):
                col1, col2 = st.columns([2, 1])
                with col1:
                    st.markdown(
                        f"""
                    <div style='background: {bg_color}; padding: 1rem; border-radius: 12px;
                                border-left: 4px solid {color}; margin-bottom: 1rem;'>
                        <div style='font-weight: 700; color: {color}; font-size: 1.1rem; margin-bottom: 0.5rem;'>
                            {emoji} {priority} Priority Escalation
                        </div>
                        <div style='color: #334155; line-height: 1.6;'>
                            <strong>Summary:</strong><br>{summary}
                        </div>
                    </div>
                    """,
                        unsafe_allow_html=True,
                    )

                with col2:
                    st.markdown(
                        f"""
                    <div style='background: white; padding: 1rem; border-radius: 12px;
                                border: 2px solid #e2e8f0; font-size: 0.9rem;'>
                        <div style='margin-bottom: 0.5rem;'>
                            <strong style='color: #64748b;'>📅 Date:</strong><br>
                            <span style='color: #334155;'>{date_utc}</span>
                        </div>
                        <div>
                            <strong style='color: #64748b;'>👤 Contact:</strong><br>
                            <span style='color: #334155; font-size: 0.8rem; word-break: break-all;'>
                                {contact_id[:30]}...
                            </span>
                        </div>
                    </div>
                    """,
                        unsafe_allow_html=True,
                    )

                if transcript_url:
                    st.markdown(
                        f"""
                    <div style='margin: 1rem 0;'>
                        <a href='{transcript_url}' target='_blank'
                           style='background: linear-gradient(135deg, #0F9ED5, #3C1EBA);
                                  color: white; padding: 0.75rem 1.5rem; border-radius: 10px;
                                  text-decoration: none; display: inline-block; font-weight: 600;
                                  box-shadow: 0 4px 12px rgba(15, 158, 213, 0.3);'>
                            🔗 View Full Transcript
                        </a>
                    </div>
                    """,
                        unsafe_allow_html=True,
                    )

                if transcript_excerpt:
                    st.markdown(
                        """
                    <div style='margin-top: 1.5rem;'>
                        <h4 style='color: #334155; font-size: 1.1rem; font-weight: 700; margin-bottom: 1rem;'>
                            💬 Conversation Excerpt
                        </h4>
                    </div>
                    """,
                        unsafe_allow_html=True,
                    )

                    for line in transcript_excerpt[:10]:
                        speaker = line.get("speaker", "Unknown")
                        text = line.get("text", "")
                        line_id = line.get("id", "")

                        speaker_color = (
                            "#0F9ED5" if speaker == "Agent" else "#A02B93" if speaker == "Customer" else "#64748b"
                        )
                        speaker_bg = (
                            "#e0f2fe" if speaker == "Agent" else "#fce7f3" if speaker == "Customer" else "#f1f5f9"
                        )

                        st.markdown(
                            f"""
                        <div style='background: {speaker_bg}; padding: 1rem 1.25rem;
                                    margin: 0.75rem 0; border-radius: 12px;
                                    border-left: 4px solid {speaker_color};
                                    box-shadow: 0 2px 6px rgba(0,0,0,0.05);'>
                            <div style='display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.5rem;'>
                                <strong style='color: {speaker_color}; font-size: 0.95rem;'>{speaker}</strong>
                                <span style='color: #94a3b8; font-size: 0.75rem;'>#{line_id}</span>
                            </div>
                            <p style='margin: 0; color: #334155; line-height: 1.6; font-size: 0.95rem;'>{text}</p>
                        </div>
                        """,
                            unsafe_allow_html=True,
                        )
    else:
        st.success(_L("sections", "no_escalations", "✅ Outstanding Performance - No Escalations!"))
        st.markdown(
            f"""
        <div style='background: linear-gradient(135deg, #d1fae5 0%, #a7f3d0 100%);
                    padding: 2.5rem; border-radius: 20px; border-left: 5px solid #059669;
                    margin: 2rem 0; box-shadow: 0 8px 24px rgba(5, 150, 105, 0.15);'>
            <div style='display: flex; align-items: center; gap: 1.5rem;'>
                <div style='font-size: 4rem;'>🌟</div>
                <div>
                    <h4 style='margin: 0; color: #065f46; font-size: 1.5rem; font-weight: 700;'>
                        {_L("sections", "no_escalations_title", "Exceptional Service Quality")}
                    </h4>
                    <p style='margin: 0.75rem 0 0 0; color: #047857; font-size: 1.05rem; line-height: 1.6;'>
                        {_L("sections", "no_escalations_msg", "Zero escalations recorded for this period, indicating excellent customer handling, proactive issue resolution, and outstanding service delivery.")}
                    </p>
                </div>
            </div>
        </div>
        """,
            unsafe_allow_html=True,
        )


def _render_vxs_modal_content(kpi_key: str, kpi_value, kpi_delta, report: dict):
    """Render VXS score details inside a KPI modal."""
    vxs_type = _L("sections", "vxs_customer_experience", "Customer Experience") if kpi_key == "vxs" else _L("sections", "vxs_solutions_quality", "Solutions Quality")
    st.markdown(
        f"""
    <div style="margin-bottom: 2rem;">
        <h3 style="font-size: 1.75rem; font-weight: 800;
                   background: linear-gradient(135deg, #0F9ED5, #3C1EBA);
                   -webkit-background-clip: text;
                   -webkit-text-fill-color: transparent;
                   background-clip: text;
                   margin-bottom: 0.5rem;">
            📊 VXS Score - {vxs_type}
        </h3>
        <p style="color: #64748b; font-size: 1rem; margin: 0;">
            {_L("sections", "vxs_subtitle", "Performance analysis and quality metrics evaluation")}
        </p>
    </div>
    """,
        unsafe_allow_html=True,
    )

    score_data = {
        "excellent": (80, 100, "💎", _L("sections", "excellent_title", "Excellent Performance"), "#0F9ED5", _L("sections", "excellent_msg", "You're exceeding expectations!")),
        "good": (60, 80, "⚡", _L("sections", "good_title", "Good Performance"), "#3C1EBA", _L("sections", "good_msg", "Solid work with room to grow")),
        "fair": (40, 60, "📊", _L("sections", "needs_improvement_title", "Needs Improvement"), "#A02B93", _L("sections", "needs_improvement_msg", "Focus areas identified")),
        "poor": (0, 40, "⚠️", _L("sections", "critical_title", "Critical Attention Required"), "#ef4444", _L("sections", "critical_msg", "Immediate action needed")),
    }

    for level, (min_score, max_score, emoji, title, color, message) in score_data.items():
        if min_score <= kpi_value < max_score:
            st.markdown(
                f"""
            <div style='background: linear-gradient(135deg, {color}12 0%, {color}06 100%);
                        padding: 2.5rem; border-radius: 20px; border-left: 5px solid {color};
                        margin: 2rem 0; box-shadow: 0 8px 24px {color}20;'>
                <div style='display: flex; align-items: center; gap: 1.5rem;'>
                    <div style='font-size: 4rem; filter: drop-shadow(0 4px 8px {color}30);'>{emoji}</div>
                    <div style='flex: 1;'>
                        <h3 style='margin: 0; color: {color}; font-size: 1.75rem; font-weight: 800;'>{title}</h3>
                        <p style='margin: 0.75rem 0 0 0; color: #64748b; font-size: 1.05rem; line-height: 1.6;'>{message}</p>
                    </div>
                </div>
            </div>
            """,
                unsafe_allow_html=True,
            )
            break

    if kpi_delta is not None:
        cols_trend = st.columns([1, 1])
        with cols_trend[0]:
            trend_color = "#0F9ED5" if kpi_delta > 0 else "#ef4444" if kpi_delta < 0 else "#A02B93"
            trend_icon = "📈" if kpi_delta > 0 else "📉" if kpi_delta < 0 else "➡️"
            trend_text = _L("sections", "trend_improving", "Improving") if kpi_delta > 0 else _L("sections", "trend_declining", "Declining") if kpi_delta < 0 else _L("sections", "trend_stable", "Stable")

            st.markdown(
                f"""
            <div style='background: linear-gradient(135deg, {trend_color}10 0%, {trend_color}05 100%);
                        padding: 1.75rem; border-radius: 16px; border-left: 5px solid {trend_color};
                        box-shadow: 0 4px 12px {trend_color}15;'>
                <div style='font-size: 0.85rem; color: #64748b; font-weight: 600;
                            text-transform: uppercase; letter-spacing: 0.05em;'>{_L("sections", "trend_label", "TREND")}</div>
                <div style='font-size: 2rem; font-weight: 800; color: {trend_color}; margin-top: 0.75rem;'>
                    {trend_icon} {trend_text}
                </div>
                <div style='font-size: 1rem; color: #64748b; margin-top: 0.5rem; font-weight: 600;'>
                    {abs(kpi_delta)} points change
                </div>
            </div>
            """,
                unsafe_allow_html=True,
            )

        with cols_trend[1]:
            progress_percent = min(100, (kpi_value / 100) * 100)
            st.markdown(
                f"""
            <div style='background: linear-gradient(135deg, rgba(15, 158, 213, 0.08) 0%, rgba(60, 30, 186, 0.08) 100%);
                        padding: 1.75rem; border-radius: 16px; border-left: 5px solid #3C1EBA;
                        box-shadow: 0 4px 12px rgba(60, 30, 186, 0.15);'>
                <div style='font-size: 0.85rem; color: #64748b; font-weight: 600;
                            text-transform: uppercase; letter-spacing: 0.05em;'>{_L("sections", "score_progress", "SCORE PROGRESS")}</div>
                <div style='font-size: 2rem; font-weight: 800; color: #3C1EBA; margin-top: 0.75rem;'>
                    {progress_percent:.0f}%
                </div>
                <div style='margin-top: 1rem;'>
                    <div style='background: rgba(226, 232, 240, 0.5); height: 10px; border-radius: 10px; overflow: hidden;
                                box-shadow: inset 0 2px 4px rgba(0,0,0,0.05);'>
                        <div style='background: linear-gradient(90deg, #0F9ED5, #3C1EBA, #A02B93);
                                    height: 100%; width: {progress_percent}%;
                                    transition: width 1.2s cubic-bezier(0.4, 0, 0.2, 1);
                                    box-shadow: 0 0 10px rgba(15, 158, 213, 0.5);'></div>
                    </div>
                </div>
            </div>
            """,
                unsafe_allow_html=True,
            )

    if kpi_key == "vxs":
        customer_exp = report.get("customer_experience", {})
        if customer_exp:
            st.markdown("---")
            st.markdown(f"### {_L('sections', 'customer_xs_section', '😊 Customer Experience Breakdown')}")
            total = sum(data.get("count", 0) for data in customer_exp.values())
            if total > 0:
                exp_cols = st.columns(len(customer_exp))
                for idx, (category, data) in enumerate(customer_exp.items()):
                    count = data.get("count", 0)
                    percentage = (count / total) * 100
                    emoji_map = {"Good": "🌟", "Medium": "⚡", "Poor": "⚠️"}
                    color_map = {"Good": "#0F9ED5", "Medium": "#A02B93", "Poor": "#ef4444"}

                    with exp_cols[idx]:
                        c = color_map.get(category, "#64748b")
                        st.markdown(
                            f"""
                        <div style='background: linear-gradient(135deg, {c}12 0%, {c}06 100%);
                                    padding: 2rem 1.5rem; border-radius: 16px; text-align: center;
                                    border-top: 4px solid {c};
                                    box-shadow: 0 4px 12px rgba(15, 158, 213, 0.1);'>
                            <div style='font-size: 3rem; margin-bottom: 0.5rem;'>{emoji_map.get(category, "😐")}</div>
                            <div style='font-size: 2.5rem; font-weight: 800; color: {c}; margin-top: 0.5rem;'>{count}</div>
                            <div style='font-size: 0.85rem; color: #64748b; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; margin-top: 0.75rem;'>{category}</div>
                            <div style='font-size: 1.25rem; color: {c}; font-weight: 800; margin-top: 0.5rem;'>{percentage:.1f}%</div>
                        </div>
                        """,
                            unsafe_allow_html=True,
                        )

    if kpi_key == "vxs_solutions":
        sales_outcome = report.get("sales_outcome", {})
        if sales_outcome:
            st.markdown("---")
            st.markdown(f"### {_L('sections', 'sales_section', '💰 Sales Outcomes Analysis')}")
            total = sum(data.get("count", 0) for data in sales_outcome.values())
            if total > 0:
                for category, data in sales_outcome.items():
                    count = data.get("count", 0)
                    percentage = (count / total) * 100
                    emoji_map = {"Closed deal": "💎", "In progress but not closed": "⚡", "Not closed": "📊"}
                    color_map = {"Closed deal": "#0F9ED5", "In progress but not closed": "#A02B93", "Not closed": "#3C1EBA"}
                    bg_map = {
                        "Closed deal": "linear-gradient(135deg, rgba(15, 158, 213, 0.08) 0%, rgba(15, 158, 213, 0.03) 100%)",
                        "In progress but not closed": "linear-gradient(135deg, rgba(160, 43, 147, 0.08) 0%, rgba(160, 43, 147, 0.03) 100%)",
                        "Not closed": "linear-gradient(135deg, rgba(60, 30, 186, 0.08) 0%, rgba(60, 30, 186, 0.03) 100%)",
                    }

                    c = color_map.get(category, "#64748b")
                    st.markdown(
                        f"""
                    <div style='background: {bg_map.get(category, "white")};
                                padding: 1.5rem 2rem; margin: 1rem 0; border-radius: 16px;
                                border-left: 5px solid {c};
                                box-shadow: 0 4px 16px rgba(15, 158, 213, 0.08);'>
                        <div style='display: flex; justify-content: space-between; align-items: center;'>
                            <div style='display: flex; align-items: center; gap: 1.25rem;'>
                                <div style='background: {c}15;
                                            width: 56px; height: 56px; border-radius: 14px;
                                            display: flex; align-items: center; justify-content: center;
                                            font-size: 1.75rem;'>
                                    {emoji_map.get(category, "📊")}
                                </div>
                                <span style='font-weight: 700; color: #334155; font-size: 1.05rem;'>{category}</span>
                            </div>
                            <div style='text-align: right;'>
                                <div style='font-size: 2rem; font-weight: 800; color: {c};'>{count}</div>
                                <div style='font-size: 1rem; color: {c}; font-weight: 600;'>{percentage:.1f}%</div>
                            </div>
                        </div>
                    </div>
                    """,
                        unsafe_allow_html=True,
                    )
