"""
ui/pages/analytics.py — Advanced analytics view (stub)
========================================================
"""

import streamlit as st

from backend.config.logging import get_logger
from backend.config.programs import DEFAULT_PROGRAM, load_program_config

logger = get_logger(__name__)


def render_analytics_view(emp_names: dict):
    """Render advanced analytics"""
    logger.info("Rendering analytics view")
    program_id = st.session_state.get("selected_program", DEFAULT_PROGRAM)
    cfg = load_program_config(program_id)
    L = cfg.get_label
    st.markdown(f"## {L('analytics', 'page_title', 'Advanced Analytics')}")

    st.info(
        L("analytics", "coming_soon", "🚧 Advanced analytics features coming soon: predictive modeling, "
          "cohort analysis, and custom reporting.")
    )
