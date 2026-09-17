"""
main.py — APIX Sales Performance Intelligence Dashboard entry point
=====================================================================
Usage:
    streamlit run main.py
"""

from datetime import datetime

import streamlit as st

from backend.auth.session import AuthSystem
from backend.auth.upn_map import get_all_managers
from backend.services.blob_service import (
    discover_available_weeks,
    get_selected_week,
    load_index_for_week,
)
from backend.config.programs import load_program_config, DEFAULT_PROGRAM
from frontend.pages.individual_performance_metrics import render_individual_performance_metrics_view
from frontend.pages.individual import render_individual_report
from frontend.pages.login import render_login_page
from frontend.pages.manager import render_manager_overview
from frontend.components.styles import load_custom_css, render_footer, page_loader
from frontend.components.chatbot import render_chatbot
from backend.config.logging import get_logger

logger = get_logger(__name__)

# Conditional SSO import
_SSO_AVAILABLE = False
try:
    from backend.auth.sso import (
        AZURE_SCOPE,
        REDIRECT_UI,
        handle_sso_callback,
        initialize_app,
    )

    _SSO_AVAILABLE = True
except Exception:
    pass

# ---------------- Page Configuration ----------------
st.set_page_config(
    page_title="APIX",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)


def _get_effective_coach_ids() -> list:
    """
    Return the list of coach_ids the current user should be scoped to.

    - Coaches: not applicable (filtered by their own user_id elsewhere).
    - Managers with coach_ids: returns their assigned coach_ids.
    - Superusers (no coach_ids): checks 'superuser_manager_filter' session
      state set by the sidebar dropdown. Returns that manager's coach_ids,
      or an empty list for 'All'.
    """
    session_coach_ids = st.session_state.get("coach_ids") or []
    if session_coach_ids:
        return [str(c) for c in session_coach_ids]

    # Superuser: check if they picked a specific manager in the dropdown
    selected = st.session_state.get("superuser_manager_filter")
    if selected and selected != "__all__":
        managers = get_all_managers()
        for mgr in managers:
            if str(mgr["employee_id"]) == selected:
                return [str(c) for c in mgr.get("coach_ids", [])]
    return []


def _filter_index_by_coach_ids(idx: list, coach_id_strs: list) -> list:
    """Filter an employee index to only entries whose CoachID is in coach_id_strs."""
    if not coach_id_strs:
        return idx
    return [emp for emp in idx if str(emp.get("CoachID", "")) in coach_id_strs]


@st.cache_resource(show_spinner=False)
def _prewarm_backends_once() -> bool:
    """Warm slow backends ONCE per process so the first page load is fast.

    ``@st.cache_resource`` guarantees the body runs a single time for the whole
    app process (not per session/rerun). It pre-opens a few Azure SQL
    connections on a background thread so the first metrics load skips the
    multi-second cold TLS+AAD-login connect. Best-effort: never blocks or breaks
    startup if Azure SQL is unconfigured/unavailable.
    """
    try:
        from backend.services.azure_sql import prewarm_pool

        prewarm_pool()
    except Exception as exc:  # pragma: no cover - warming is best-effort
        logger.info("backend prewarm skipped: %s", exc)
    return True


def main():
    """Main application entry point"""
    logger.info("Application startup")
    # Initialize session
    AuthSystem.init_session()

    # Warm slow backends (Azure SQL connection pool) once per process. Runs on a
    # background thread so it never delays first paint; by the time the user
    # opens the metrics page the connections are already established.
    _prewarm_backends_once()

    # Load custom CSS
    load_custom_css()

    # ── SSO OAuth callback ──────────────────────────────────────────────
    if (
        _SSO_AVAILABLE
        and not st.session_state.authenticated
        and st.query_params.get("code")
    ):
        try:
            sso_app = initialize_app()
            code = st.query_params.get("code")
            user_name, roles, upn = handle_sso_callback(sso_app, code)
            if user_name:
                AuthSystem.sso_login(user_name, roles, upn)
                logger.info("SSO callback completed for %s", upn)
                st.query_params.clear()
                st.rerun()
            else:
                logger.warning("SSO callback returned no user")
                st.error("SSO authentication failed. Please try again.")
                st.query_params.clear()
        except Exception as e:
            logger.error("SSO callback error: %s", e)
            st.error(f"SSO error: {e}")
            st.query_params.clear()

    # ── Authentication guard ────────────────────────────────────────────
    if not st.session_state.authenticated:
        render_login_page()
        render_footer()
        return

    # Main application header
    program_id = st.session_state.get("selected_program", DEFAULT_PROGRAM)
    cfg = load_program_config(program_id)
    L = cfg.get_label  # shorthand

    st.markdown(
        f"""
    <div class="enterprise-header">
        <h1>{L("app", "dashboard_title", "APIX - Afni Performance Intelligence Index")}</h1>
        <div class="subtitle">{L("app", "welcome_template", "Welcome back, {name} | Role: {role}").format(name=st.session_state.user_name, role=st.session_state.user_role.title())}</div>
    </div>
    """,
        unsafe_allow_html=True,
    )

    # Sidebar navigation
    with st.sidebar:
        st.subheader(L("sidebar", "navigation", "Navigation"), anchor=False)

        _views = [
            L("sidebar", "view_individual", "Individual Report"),
            L("sidebar", "view_analytics", "Individual Performance Metrics"),
            L("sidebar", "view_manager", "Manager Overview"),
        ]
        view_mode = st.radio(
            "View",
            _views,
            label_visibility="collapsed",
        )

        st.subheader(L("sidebar", "filters", "Filters"), anchor=False)

        # Dynamic week selector
        available_weeks = discover_available_weeks()
        if available_weeks:

            def format_week_label(week_str: str) -> str:
                try:
                    dt = datetime.strptime(week_str, "%Y-%m-%d")
                    return f"Week of {dt.strftime('%b %d, %Y')}"
                except Exception:
                    return week_str

            week_options = {format_week_label(w): w for w in available_weeks}
            selected_week_label = st.selectbox(
                L("sidebar", "select_week", "📅 Select Week"),
                list(week_options.keys()),
                key="week_selector",
                help=L("sidebar", "week_help", "Select a week to view sales performance data"),
            )
            selected_week = week_options[selected_week_label]
            st.session_state.selected_week = selected_week
        else:
            from backend.config.settings import settings

            selected_week = settings.DEFAULT_REPORTS_PREFIX
            st.session_state.selected_week = selected_week
            st.warning("Using default week. Unable to discover available weeks.")

        # Shared scrollbar CSS for dropdowns
        st.markdown(
            """
        <style>
        div[data-baseweb="select"] > div { max-height: 400px !important; }
        div[data-baseweb="popover"] { max-height: 400px !important; }
        div[data-baseweb="popover"] ul {
            max-height: 350px !important; overflow-y: auto !important;
            scrollbar-width: thin !important; scrollbar-color: #1e40af #f1f5f9 !important;
        }
        div[data-baseweb="popover"] ul::-webkit-scrollbar { width: 12px !important; height: 12px !important; }
        div[data-baseweb="popover"] ul::-webkit-scrollbar-track { background: #f1f5f9 !important; border-radius: 10px !important; }
        div[data-baseweb="popover"] ul::-webkit-scrollbar-thumb { background: #1e40af !important; border-radius: 10px !important; border: 2px solid #f1f5f9 !important; }
        div[data-baseweb="popover"] ul::-webkit-scrollbar-thumb:hover { background: #3b82f6 !important; }
        div[data-baseweb="popover"] ul { scroll-behavior: smooth !important; }
        div[data-baseweb="popover"] li { padding: 0.75rem 1rem !important; min-height: 44px !important; }
        </style>
        """,
            unsafe_allow_html=True,
        )

        # Employee selector (Individual Report)
        selected_employee = None
        emp_id = None
        analytics_selected_emp_id = None
        analytics_selected_emp_name = None

        # Program — auto-determined from report; ensure default is set
        if not st.session_state.get("selected_program"):
            st.session_state.selected_program = DEFAULT_PROGRAM

        # Superuser manager dropdown — shown when manager has no coach_ids
        user_role = st.session_state.get("user_role", "agent")
        if user_role in ("manager", "director") and not st.session_state.get("coach_ids"):
            all_managers = get_all_managers()
            if all_managers:
                mgr_options = {"All": "__all__"}
                mgr_options.update({
                    m["name"]: str(m["employee_id"]) for m in all_managers
                })
                selected_mgr_label = st.selectbox(
                    "👔 View as Manager",
                    list(mgr_options.keys()),
                    key="superuser_mgr_selector",
                )
                st.session_state["superuser_manager_filter"] = mgr_options[selected_mgr_label]

        if view_mode == _views[0]:  # Individual Report
            idx = load_index_for_week(selected_week)
            if idx:
                if st.session_state.user_role == "coach":
                    coach_user_id = str(st.session_state.user_id)
                    filtered_idx = [
                        emp
                        for emp in idx
                        if str(emp.get("CoachID", "")) == coach_user_id
                    ]
                else:
                    # Scope index to manager's assigned coaches
                    effective_ids = _get_effective_coach_ids()
                    scoped_idx = _filter_index_by_coach_ids(idx, effective_ids)

                    coach_map = {}
                    for emp in scoped_idx:
                        cid = emp.get("CoachID")
                        if cid:
                            if cid not in coach_map:
                                coach_map[cid] = (
                                    emp.get("CoachName") or f"Coach {cid}"
                                )

                    if coach_map:
                        coach_display = {
                            name: cid for cid, name in coach_map.items()
                        }
                        selected_coach_label = st.selectbox(
                            L("sidebar", "select_coach", "👨‍💼 Select Coach"),
                            sorted(coach_display.keys()),
                            key="individual_coach_selector",
                        )
                        selected_coach_id = str(coach_display[selected_coach_label])
                        filtered_idx = [
                            emp
                            for emp in scoped_idx
                            if str(emp.get("CoachID", "")) == selected_coach_id
                        ]
                    else:
                        filtered_idx = scoped_idx

                if filtered_idx:
                    emp_names_map = {
                        it["EmployeeName"]: it["EmployeeID"]
                        for it in filtered_idx
                        if it.get("EmployeeID") and it.get("EmployeeName")
                    }
                    if emp_names_map:
                        selected_employee = st.selectbox(
                            L("sidebar", "select_employee", "👤 Employee"),
                            sorted(emp_names_map.keys()),
                            key="employee_selector",
                        )
                        emp_id = emp_names_map.get(selected_employee)
                    else:
                        st.error("No employee data found.")
                else:
                    st.error(
                        "No employees assigned to you."
                        if st.session_state.user_role == "coach"
                        else "No employee data found."
                    )
            else:
                st.error("Could not load employee index.")

        if view_mode == _views[1]:  # Analytics
            analytics_idx = load_index_for_week(selected_week)
            if analytics_idx:
                if st.session_state.user_role == "coach":
                    coach_user_id = str(st.session_state.user_id)
                    analytics_filtered_idx = [
                        emp
                        for emp in analytics_idx
                        if str(emp.get("CoachID", "")) == coach_user_id
                    ]
                else:
                    effective_ids = _get_effective_coach_ids()
                    analytics_filtered_idx = _filter_index_by_coach_ids(analytics_idx, effective_ids)

                analytics_emp_names = {
                    it["EmployeeName"]: it["EmployeeID"]
                    for it in analytics_filtered_idx
                    if it.get("EmployeeID") and it.get("EmployeeName")
                }
                if analytics_emp_names:
                    if "analytics_metric_agg" not in st.session_state:
                        st.session_state["analytics_metric_agg"] = "Average"

                    analytics_selected_emp_name = st.selectbox(
                        "👤 Select Employee",
                        sorted(analytics_emp_names.keys()),
                        key="analytics_employee_selector",
                        help="Select an employee to view analytics",
                    )
                    analytics_selected_emp_id = analytics_emp_names.get(analytics_selected_emp_name)

                    analytics_metric_agg = st.selectbox(
                        "📊 Metric Aggregation",
                        ["Average", "Sum"],
                        index=["Average", "Sum"].index(
                            st.session_state.get("analytics_metric_agg", "Average")
                        ),
                        key="analytics_metric_agg",
                        help="How to aggregate metrics across the time period",
                    )
                else:
                    st.warning("No employees available for analytics.")
            else:
                st.error("Could not load employee index for analytics.")

        st.subheader(L("sidebar", "export_header", "Export options"), anchor=False)
        export_format = st.selectbox(L("sidebar", "export_format", "Format"), ["PDF", "Excel", "PNG"])

    # Main content area — route based on view mode
    if view_mode == _views[0]:  # Individual Report
        if selected_employee and emp_id:
            with page_loader("Loading individual report…"):
                render_individual_report(emp_id, selected_employee)
        else:
            st.info("👈 Select an employee from the sidebar to view their report.")

    elif view_mode == _views[2]:  # Manager Overview
        idx = load_index_for_week(st.session_state.selected_week)
        if idx:
            if st.session_state.user_role == "coach":
                coach_user_id = str(st.session_state.user_id)
                filtered_idx = [
                    emp
                    for emp in idx
                    if str(emp.get("CoachID", "")) == coach_user_id
                ]
            else:
                # Scope index to manager's assigned coaches
                effective_ids = _get_effective_coach_ids()
                scoped_idx = _filter_index_by_coach_ids(idx, effective_ids)

                coach_map = {}
                for emp in scoped_idx:
                    cid = emp.get("CoachID")
                    if cid:
                        if cid not in coach_map:
                            coach_map[cid] = emp.get("CoachName") or f"Coach {cid}"

                with st.sidebar:
                    coach_options = [L("sidebar", "all_coaches", "All Coaches")] + sorted(coach_map.values())
                    selected_mgr_coach = st.selectbox(
                        L("sidebar", "filter_coach", "👨‍💼 Filter by Coach"),
                        coach_options,
                        key="manager_coach_filter",
                    )

                if selected_mgr_coach == L("sidebar", "all_coaches", "All Coaches"):
                    filtered_idx = scoped_idx
                else:
                    sel_coach_id = next(
                        (
                            cid
                            for cid, cname in coach_map.items()
                            if cname == selected_mgr_coach
                        ),
                        None,
                    )
                    filtered_idx = (
                        [
                            emp
                            for emp in scoped_idx
                            if str(emp.get("CoachID", "")) == str(sel_coach_id)
                        ]
                        if sel_coach_id
                        else scoped_idx
                    )

            emp_names_dict = {
                it["EmployeeName"]: it["EmployeeID"]
                for it in filtered_idx
                if it.get("EmployeeID") and it.get("EmployeeName")
            }
            with page_loader("Loading manager overview…"):
                render_manager_overview(emp_names_dict)
        else:
            st.error("Could not load employee data.")

    elif view_mode == _views[1]:  # Analytics
        idx = load_index_for_week(st.session_state.selected_week)
        if idx:
            if st.session_state.user_role == "coach":
                coach_user_id = str(st.session_state.user_id)
                filtered_idx = [
                    emp
                    for emp in idx
                    if str(emp.get("CoachID", "")) == coach_user_id
                ]
            else:
                effective_ids = _get_effective_coach_ids()
                filtered_idx = _filter_index_by_coach_ids(idx, effective_ids)

            emp_names_dict = {
                it["EmployeeName"]: it["EmployeeID"]
                for it in filtered_idx
                if it.get("EmployeeID") and it.get("EmployeeName")
            }
            with page_loader("Loading Individual Performance Metrics…"):
                render_individual_performance_metrics_view(
                    emp_names_dict,
                    analytics_selected_emp_id,
                    analytics_selected_emp_name,
                    st.session_state.get("analytics_metric_agg", "Average"),
                )
        else:
            st.error("Could not load employee data.")
    
    with st.sidebar:
        # Sticky logout button at bottom of sidebar
        st.markdown(
            """
            <style>
            .sidebar-logout-fixed {
                position: fixed;
                left: 20px;
                bottom: 20px;
                width: calc(var(--sidebar-width, 21rem) - 40px);
                z-index: 999;
            }
            </style>
            """,
            unsafe_allow_html=True,
        )
        if st.button("🚪 Log Out", key="sidebar_logout_btn", use_container_width=True, type="primary"):
            # Remove chatbot from parent DOM before rerun
            st.markdown("""
            <script>
            (function(){
                var p = window.parent.document;
                var el = p.getElementById('apixChatContainer');
                if (el) el.remove();
                var s = p.getElementById('apixChatStyle');
                if (s) s.remove();
                // Drop the persisted chat session so the next login starts fresh.
                try { window.parent.sessionStorage.removeItem('apix_chat_session'); } catch(e) {}
                try { window.parent.sessionStorage.removeItem('apix_chat_closed'); } catch(e) {}
            })();
            </script>
            """, unsafe_allow_html=True)
            AuthSystem.logout()
            st.rerun()
    render_footer()
    if st.session_state.get("authenticated"):
        render_chatbot()


if __name__ == "__main__":
    main()
