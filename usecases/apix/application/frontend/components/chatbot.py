"""
frontend/components/chatbot.py — Floating chatbot widget (APIX Insight)
========================================================================
Renders a fixed chat FAB at bottom-right that expands into a chat box.
Messages are sent **directly** to the ``POST /chat_agent`` FastAPI
endpoint from the browser via JavaScript ``fetch()``.

Sessions are persisted to blob storage via ``/sessions/`` API endpoints.
Each session stores user and bot messages with timestamps.
Session history sidebar visible in fullscreen/maximize modes.
"""

import json

import streamlit as st
import streamlit.components.v1 as components

from backend.config.logging import get_logger
from backend.config.settings import settings
from backend.services.blob_service import load_index_for_week
from backend.auth.chat_token import mint_chat_token

logger = get_logger(__name__)

_CHAT_API_BASE = settings.CHAT_API_BASE_URL
_CHAT_ENDPOINT = f"{_CHAT_API_BASE}/chat_agent"
_SESSIONS_BASE = f"{_CHAT_API_BASE}/sessions"
# Client-side abort budget. Kept above the backend's worst-case (cold Azure SQL
# resume + LLM) so the user gets the answer instead of a spurious timeout when
# the backend actually succeeds. Override via CHAT_REQUEST_TIMEOUT (seconds).
_TIMEOUT_SECONDS = settings.CHAT_REQUEST_TIMEOUT


def _build_reporting_agents() -> list[int] | dict[str, int]:
    """Build ``reporting_agents`` from the session and employee index.

    * **Coach** → ``list[int]`` of employee IDs whose CoachID matches
      the logged-in user.
    * **Manager** → ``dict[str, int]`` mapping each employee ID (str)
      to their coach ID for all employees under the manager's coaches.
    """
    role = st.session_state.get("user_role", "coach")
    user_id = st.session_state.get("user_id")
    week = st.session_state.get("selected_week")

    idx = load_index_for_week(week) if week else []

    if role == "coach":
        return [
            int(emp["EmployeeID"])
            for emp in idx
            if str(emp.get("CoachID", "")) == str(user_id)
               and emp.get("EmployeeID")
        ]

    # Manager — scope via coach_ids stored in session
    coach_ids = st.session_state.get("coach_ids") or []
    coach_id_strs = {str(c) for c in coach_ids}

    return {
        str(emp["EmployeeID"]): int(emp["CoachID"])
        for emp in idx
        if str(emp.get("CoachID", "")) in coach_id_strs
           and emp.get("EmployeeID") and emp.get("CoachID")
    }


def render_chatbot():
    """Render the floating chatbot widget with session management."""
    # ── Build context to pass to JavaScript ──
    user_id = st.session_state.get("user_id")
    user_name = st.session_state.get("user_name", "")
    role = st.session_state.get("user_role", "coach")

    # API only accepts "coach" or "manager"
    api_role = "manager" if role in ("manager", "director") else "coach"

    reporting_agents = _build_reporting_agents() if user_id else []

    _FEEDBACK_ENDPOINT = f"{_CHAT_API_BASE}/feedback"

    # Short-lived signed token proving this browser session is an authenticated
    # APIX user; the chatbot API validates it and binds requests to this user.
    auth_token = mint_chat_token(user_id, api_role, user_name or "") if user_id else ""

    # Agent + period the user is currently viewing in the dashboard. Passed to
    # the chatbot as authoritative context so follow-ups resolve to the selected
    # agent and switching the selection immediately switches context — even
    # within the same chat session. ``_program_employee_id`` is set by the
    # individual-agent pages; absence means the user is on a team-level view.
    focus_agent_raw = st.session_state.get("_program_employee_id")
    try:
        focus_agent_id = int(focus_agent_raw) if focus_agent_raw not in (None, "") else None
    except (TypeError, ValueError):
        focus_agent_id = None
    focus_scope = "agent" if focus_agent_id else "team"
    selected_period = st.session_state.get("selected_week") or None

    chat_ctx = json.dumps({
        "apiUrl": _CHAT_ENDPOINT,
        "sessionsUrl": _SESSIONS_BASE,
        "feedbackUrl": _FEEDBACK_ENDPOINT,
        "userId": int(user_id) if user_id is not None else None,
        "userName": user_name or "",
        "role": api_role,
        "reportingAgents": reporting_agents,
        "focusAgentId": focus_agent_id,
        "focusScope": focus_scope,
        "period": selected_period,
        "timeoutMs": _TIMEOUT_SECONDS * 1000,
        "authToken": auth_token,
    })

    # ── Inject chatbot UI into parent DOM ──
    components.html(f"""
    <script>
    (function() {{
        var CTX = {chat_ctx};
        var parentDoc = window.parent.document;

        // ── Auth: attach the short-lived bearer token to every API call ──
        function authHeaders(extra) {{
            var h = extra || {{}};
            if (CTX.authToken) h['Authorization'] = 'Bearer ' + CTX.authToken;
            return h;
        }}

        // ── Remove previous injection on rerun ──
        var old = parentDoc.getElementById('apixChatContainer');
        if (old) old.remove();
        var oldStyle = parentDoc.getElementById('apixChatStyle');
        if (oldStyle) oldStyle.remove();

        // ── Utility ──
        function escHtml(s) {{
            var d = parentDoc.createElement('div'); d.textContent = s; return d.innerHTML;
        }}
        function renderMd(text) {{
            // Lightweight markdown to HTML (safe: escapes first, then applies formatting)
            var s = escHtml(text);
            console.log('[APIX renderMd] input:', text.substring(0, 120), '| after escHtml:', s.substring(0, 120));
            // Headings
            s = s.replace(/^### (.+)$/gm, '<h4>$1</h4>');
            s = s.replace(/^## (.+)$/gm, '<h3>$1</h3>');
            s = s.replace(/^# (.+)$/gm, '<h2>$1</h2>');
            // Bold **text** using indexOf (avoids f-string regex escaping issues)
            var STAR2 = String.fromCharCode(42) + String.fromCharCode(42);
            var idx = 0;
            while (true) {{
                var open = s.indexOf(STAR2, idx);
                if (open === -1) break;
                var close = s.indexOf(STAR2, open + 2);
                if (close === -1) break;
                var inner = s.substring(open + 2, close);
                s = s.substring(0, open) + '<strong>' + inner + '</strong>' + s.substring(close + 2);
                idx = open + 8 + inner.length;
            }}
            // Bold __text__
            s = s.replace(/__(.+?)__/g, '<strong>$1</strong>');
            // Italic _text_ (single underscores, not inside __)
            s = s.replace(/(?<!_)_(?!_)(.+?)(?<!_)_(?!_)/g, '<em>$1</em>');
            // Inline code
            s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
            // Horizontal rule
            s = s.replace(/^---$/gm, '<hr>');
            // Bullet lists
            var BULLET_RE = new RegExp('^[' + String.fromCharCode(8226) + '\\\\-]\\\\s+(.+)$', 'gm');
            s = s.replace(BULLET_RE, '<li>$1</li>');
            // Also match lines starting with * as bullet (after bold ** already handled)
            var STARBULLET_RE = new RegExp('^\\\\' + String.fromCharCode(42) + '\\\\s+(.+)$', 'gm');
            s = s.replace(STARBULLET_RE, '<li>$1</li>');
            s = s.replace(/((?:<li>.*<\\/li>\\n?)+)/g, '<ul>$1</ul>');
            // Numbered lists
            s = s.replace(/^\\d+\\.\\s+(.+)$/gm, '<oli>$1</oli>');
            s = s.replace(/((?:<oli>.*<\\/oli>\\n?)+)/g, function(m) {{
                return '<ol>' + m.replace(/<\\/?oli>/g, function(t) {{
                    return t === '<oli>' ? '<li>' : '</li>';
                }}) + '</ol>';
            }});
            // Line breaks
            s = s.replace(/\\n/g, '<br>');
            // Clean up br inside block elements
            s = s.replace(/<\\/(li|ul|ol|h[2-4]|hr)><br>/g, '</$1>');
            s = s.replace(/<br><(ul|ol|h[2-4]|hr)/g, '<$1');
            console.log('[APIX renderMd] output:', s.substring(0, 200));
            return s;
        }}
        function fmtTime(iso) {{
            if (!iso) return '';
            var d = new Date(iso);
            var now = new Date();
            var isToday = d.toDateString() === now.toDateString();
            var h = d.getHours().toString().padStart(2,'0');
            var m = d.getMinutes().toString().padStart(2,'0');
            if (isToday) return h + ':' + m;
            var mon = d.toLocaleString('en', {{month:'short'}});
            return mon + ' ' + d.getDate() + ', ' + h + ':' + m;
        }}

        // ── Session state ──
        var currentSessionId = null;
        var currentMessages = [];
        var sessionsList = [];
        var sizeMode = 'normal';
        var sidebarOpen = false;

        // ── CSS ──
        var style = parentDoc.createElement('style');
        style.id = 'apixChatStyle';
        style.textContent = `

        @keyframes apixFabPulse {{
            0%   {{ box-shadow: 0 0 0 0 rgba(15,158,213,0.45); }}
            70%  {{ box-shadow: 0 0 0 18px rgba(15,158,213,0); }}
            100% {{ box-shadow: 0 0 0 0 rgba(15,158,213,0); }}
        }}
        @keyframes apixFabGradient {{
            0%   {{ background-position: 0% 50%; }}
            50%  {{ background-position: 100% 50%; }}
            100% {{ background-position: 0% 50%; }}
        }}
        @keyframes apixChatSlideUp {{
            from {{ opacity: 0; transform: translateY(20px) scale(0.95); }}
            to   {{ opacity: 1; transform: translateY(0) scale(1); }}
        }}
        @keyframes apixChatSlideDown {{
            from {{ opacity: 1; transform: translateY(0) scale(1); }}
            to   {{ opacity: 0; transform: translateY(20px) scale(0.95); }}
        }}
        @keyframes apixFabOpen {{
            0%   {{ transform: scale(1) rotate(0deg); }}
            50%  {{ transform: scale(1.2) rotate(90deg); }}
            100% {{ transform: scale(1) rotate(180deg); }}
        }}
        @keyframes apixFabClose {{
            0%   {{ transform: scale(1) rotate(180deg); }}
            50%  {{ transform: scale(1.2) rotate(90deg); }}
            100% {{ transform: scale(1) rotate(0deg); }}
        }}
        @keyframes apixTooltipFade {{
            0%   {{ opacity: 0; transform: translateX(8px); }}
            100% {{ opacity: 1; transform: translateX(0); }}
        }}
        @keyframes apixTypingDot {{
            0%, 80%, 100% {{ opacity: 0.3; transform: scale(0.8); }}
            40% {{ opacity: 1; transform: scale(1); }}
        }}

        .chat-fab {{
            position: fixed;
            bottom: 28px;
            right: 28px;
            width: 64px;
            height: 64px;
            border-radius: 50%;
            background: linear-gradient(135deg, #0F9ED5, #3C1EBA, #7c3aed, #0F9ED5);
            background-size: 300% 300%;
            color: #fff;
            border: none;
            cursor: pointer;
            box-shadow: 0 6px 24px rgba(15,158,213,0.4);
            z-index: 10000;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: transform 0.35s cubic-bezier(.34,1.56,.64,1), box-shadow 0.3s ease;
            animation: apixFabPulse 2.5s infinite, apixFabGradient 4s ease infinite;
        }}
        .chat-fab:hover {{
            transform: scale(1.15);
            box-shadow: 0 8px 32px rgba(15,158,213,0.55), 0 0 0 6px rgba(15,158,213,0.15);
            animation: apixFabGradient 2s ease infinite;
        }}
        .chat-fab:hover .chat-fab-tooltip {{
            display: block;
            animation: apixTooltipFade 0.25s ease forwards;
        }}
        .chat-fab svg.fab-icon {{
            width: 30px; height: 30px; fill: #fff;
            transition: opacity 0.25s, transform 0.35s;
        }}
        .chat-fab .fab-close-icon {{
            display: none; font-size: 28px; font-weight: 700; line-height: 1;
        }}
        .chat-fab.active {{
            animation: apixFabOpen 0.4s ease forwards, apixFabGradient 4s ease infinite;
        }}
        .chat-fab.closing {{
            animation: apixFabClose 0.4s ease forwards, apixFabGradient 4s ease infinite;
        }}
        .chat-fab.active svg.fab-icon {{ display: none; }}
        .chat-fab.active .fab-close-icon {{ display: block; }}

        .chat-fab-tooltip {{
            display: none; position: absolute; right: 76px; top: 50%;
            transform: translateY(-50%); background: #1e293b; color: #fff;
            font-size: 0.78rem; font-weight: 600; padding: 6px 12px;
            border-radius: 8px; white-space: nowrap; pointer-events: none;
            box-shadow: 0 2px 8px rgba(0,0,0,0.15);
        }}
        .chat-fab-tooltip::after {{
            content: ''; position: absolute; right: -6px; top: 50%;
            transform: translateY(-50%); border: 6px solid transparent;
            border-left-color: #1e293b;
        }}
        .chat-fab.active .chat-fab-tooltip {{ display: none !important; }}

        .chat-box {{
            position: fixed; bottom: 102px; right: 28px;
            width: 390px; height: 500px; background: #ffffff;
            border-radius: 16px; box-shadow: 0 12px 48px rgba(0,0,0,0.18);
            z-index: 9999; display: none; flex-direction: column;
            overflow: hidden; border: 1px solid #e2e8f0;
            transition: width 0.3s ease, height 0.3s ease, bottom 0.3s ease,
                        right 0.3s ease, border-radius 0.3s ease;
        }}
        .chat-box.open {{
            display: flex;
            animation: apixChatSlideUp 0.3s ease forwards;
        }}
        .chat-box.closing-anim {{
            animation: apixChatSlideDown 0.25s ease forwards;
        }}
        .chat-box.size-large {{
            width: 60vw; height: 70vh;
            bottom: 15vh; right: 20vw;
            border-radius: 16px;
        }}
        .chat-box.size-full {{
            position: fixed;
            bottom: 38px;
            right: 28px;
            border-radius: 16px;
            border: 1px solid #e2e8f0;
            /* Dynamically set by JS based on Streamlit layout */
            left: var(--apix-full-left, 28px);
            top: var(--apix-full-top, 60px);
            width: auto; height: auto;
        }}

        /* ── Layout: sidebar + chat content ── */
        .chat-box-body {{
            flex: 1; display: flex; overflow: hidden;
        }}

        /* ── Session sidebar ── */
        .chat-sidebar {{
            width: 0; overflow: hidden; background: #f1f5f9;
            border-right: 1px solid #e2e8f0; display: flex; flex-direction: column;
            transition: width 0.25s ease;
        }}
        .chat-sidebar.visible {{
            width: 240px; min-width: 240px;
        }}
        .chat-sidebar-header {{
            padding: 12px 14px; font-size: 0.78rem; font-weight: 700;
            color: #475569; text-transform: uppercase; letter-spacing: 0.04em;
            border-bottom: 1px solid #e2e8f0; flex-shrink: 0;
        }}
        .chat-sidebar-list {{
            flex: 1; overflow-y: auto; padding: 6px 8px;
        }}
        .chat-sidebar-list::-webkit-scrollbar {{ width: 4px; }}
        .chat-sidebar-list::-webkit-scrollbar-thumb {{ background: #cbd5e1; border-radius: 2px; }}
        .chat-session-item {{
            padding: 9px 10px; margin-bottom: 4px; border-radius: 8px;
            cursor: pointer; transition: background 0.15s;
            border: 1px solid transparent;
            display: flex; align-items: flex-start; gap: 6px;
        }}
        .chat-session-item:hover {{ background: #e2e8f0; }}
        .chat-session-item.active {{
            background: #dbeafe; border-color: #93c5fd;
        }}
        .chat-session-item .session-info {{
            flex: 1; min-width: 0; overflow: hidden;
        }}
        .chat-session-item .session-title {{
            font-size: 0.78rem; font-weight: 600; color: #1e293b;
            white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
        }}
        .chat-session-item .session-meta {{
            font-size: 0.66rem; color: #64748b; margin-top: 2px;
        }}
        .chat-session-item .session-actions {{
            display: flex; gap: 2px; flex-shrink: 0; align-items: center;
            opacity: 0; transition: opacity 0.15s;
        }}
        .chat-session-item:hover .session-actions {{ opacity: 1; }}
        .chat-session-item .session-actions button {{
            background: none; border: none; cursor: pointer;
            padding: 3px; border-radius: 4px; display: flex;
            align-items: center; justify-content: center;
        }}
        .chat-session-item .session-actions button:hover {{ background: #cbd5e1; }}
        .chat-session-item .session-actions .btn-edit svg {{
            width: 13px; height: 13px; fill: #475569;
        }}
        .chat-session-item .session-actions .btn-delete svg {{
            width: 13px; height: 13px; fill: #dc2626;
        }}
        .chat-session-item .session-actions .btn-delete:hover {{ background: #fee2e2; }}
        .session-edit-input {{
            width: 100%; font-size: 0.78rem; padding: 3px 6px;
            border: 1px solid #93c5fd; border-radius: 4px; outline: none;
            font-family: inherit;
        }}

        /* ── Chat content area ── */
        .chat-content {{
            flex: 1; display: flex; flex-direction: column; min-width: 0;
        }}

        .chat-box-header {{
            background: linear-gradient(135deg, #0F9ED5 0%, #3C1EBA 100%);
            color: #fff; padding: 10px 14px; font-size: 0.9rem;
            font-weight: 700; letter-spacing: 0.02em;
            display: flex; align-items: center; gap: 8px;
            user-select: none; flex-shrink: 0;
        }}
        .chat-box-header svg.header-logo {{
            width: 22px; height: 22px; fill: #fff; flex-shrink: 0;
        }}
        .chat-header-title {{ flex: 1; display: flex; flex-direction: column; }}
        .chat-header-title span:first-child {{ font-size: 0.92rem; font-weight: 700; }}
        .chat-header-title span:last-child {{
            font-size: 0.68rem; font-weight: 400; opacity: 0.8; margin-top: 1px;
        }}
        .chat-header-actions {{
            display: flex; align-items: center; gap: 2px; margin-left: auto;
        }}
        .chat-header-actions button {{
            background: rgba(255,255,255,0.12); border: none; color: #fff;
            width: 28px; height: 28px; border-radius: 6px; cursor: pointer;
            display: flex; align-items: center; justify-content: center;
            transition: background 0.15s;
        }}
        .chat-header-actions button:hover {{ background: rgba(255,255,255,0.25); }}
        .chat-header-actions button svg {{ width: 14px; height: 14px; fill: #fff; }}

        .chat-messages {{
            flex: 1; overflow-y: auto; padding: 14px 16px;
            display: flex; flex-direction: column; gap: 10px; background: #f8fafc;
        }}
        .chat-messages::-webkit-scrollbar {{ width: 5px; }}
        .chat-messages::-webkit-scrollbar-track {{ background: transparent; }}
        .chat-messages::-webkit-scrollbar-thumb {{ background: #cbd5e1; border-radius: 3px; }}

        .chat-msg-wrapper {{
            display: flex; flex-direction: row; gap: 8px; max-width: 85%;
            align-items: flex-start;
        }}
        .chat-msg-wrapper.bot {{ align-self: flex-start; }}
        .chat-msg-wrapper.user {{ align-self: flex-end; flex-direction: row-reverse; }}

        .chat-avatar {{
            width: 28px; height: 28px; border-radius: 50%; flex-shrink: 0;
            display: flex; align-items: center; justify-content: center;
            margin-top: 2px;
        }}
        .chat-avatar.bot-avatar {{
            background: linear-gradient(135deg, #0F9ED5 0%, #3C1EBA 100%);
        }}
        .chat-avatar.user-avatar {{
            background: #e2e8f0;
        }}
        .chat-avatar svg {{ width: 16px; height: 16px; fill: #fff; }}
        .chat-avatar.user-avatar svg {{ fill: #475569; }}
        .chat-msg-body {{
            display: flex; flex-direction: column; min-width: 0; flex: 1;
        }}

        .chat-msg {{
            padding: 10px 14px; border-radius: 14px;
            font-size: 0.83rem; line-height: 1.55; word-wrap: break-word;
            white-space: pre-wrap;
        }}
        .chat-msg.bot {{
            background: #fff; color: #1e293b;
            border: 1px solid #e2e8f0; border-top-left-radius: 4px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.04);
        }}
        .chat-msg.user {{
            background: linear-gradient(135deg, #0F9ED5, #3C1EBA);
            color: #fff; border-top-right-radius: 4px;
            box-shadow: 0 1px 3px rgba(15,158,213,0.15);
        }}

        /* ── Markdown rendering inside bot messages ── */
        .chat-msg.bot {{ white-space: normal; }}
        .chat-msg.bot h2, .chat-msg.bot h3, .chat-msg.bot h4 {{
            margin: 8px 0 4px; font-weight: 700; line-height: 1.3;
        }}
        .chat-msg.bot h2 {{ font-size: 1rem; }}
        .chat-msg.bot h3 {{ font-size: 0.92rem; }}
        .chat-msg.bot h4 {{ font-size: 0.86rem; }}
        .chat-msg.bot strong {{ font-weight: 700; color: #0f172a; }}
        .chat-msg.bot em {{ font-style: italic; }}
        .chat-msg.bot code {{
            background: #f1f5f9; padding: 1px 5px; border-radius: 4px;
            font-family: 'SF Mono', Consolas, monospace; font-size: 0.78rem;
            color: #7c3aed;
        }}
        .chat-msg.bot ul, .chat-msg.bot ol {{
            margin: 6px 0; padding-left: 18px;
        }}
        .chat-msg.bot li {{
            margin-bottom: 3px; line-height: 1.5;
            padding-left: 2px;
        }}
        .chat-msg.bot ul li {{ list-style: disc; }}
        .chat-msg.bot ol li {{ list-style: decimal; }}
        .chat-msg.bot hr {{
            border: none; border-top: 1px solid #e2e8f0;
            margin: 8px 0;
        }}
        .chat-msg.bot br + br {{ display: none; }}

        .chat-msg-time {{
            font-size: 0.62rem; color: #94a3b8; margin-top: 3px;
            padding: 0 4px;
        }}
        .chat-msg-wrapper.user .chat-msg-time {{ text-align: right; }}

        .chat-fullmsg-toggle {{
            display: inline-block; margin-top: 8px; padding: 2px 8px;
            font-size: 0.68rem; font-weight: 600; color: #2563eb;
            background: transparent; border: 1px solid #bfdbfe;
            border-radius: 999px; cursor: pointer; line-height: 1.4;
        }}
        .chat-fullmsg-toggle:hover {{ background: #eff6ff; }}
        .chat-fullmsg-body {{
            margin-top: 8px; padding-top: 8px;
            border-top: 1px solid rgba(0,0,0,0.08);
        }}

        .chat-typing {{
            align-self: flex-start; display: flex; gap: 8px;
            align-items: flex-start; max-width: 85%;
        }}
        .chat-typing .chat-avatar {{
            width: 28px; height: 28px; border-radius: 50%; flex-shrink: 0;
            display: flex; align-items: center; justify-content: center;
            background: linear-gradient(135deg, #0F9ED5 0%, #3C1EBA 100%);
        }}
        .chat-typing .chat-avatar svg {{ width: 16px; height: 16px; fill: #fff; }}
        .chat-typing-dots {{
            display: flex; gap: 4px;
            padding: 12px 16px; background: #fff; border: 1px solid #e2e8f0;
            border-radius: 14px; border-top-left-radius: 4px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.04);
        }}
        .chat-typing-dots span {{
            width: 7px; height: 7px; border-radius: 50%; background: #94a3b8;
            animation: apixTypingDot 1.4s ease-in-out infinite;
        }}
        .chat-typing-dots span:nth-child(2) {{ animation-delay: 0.2s; }}
        .chat-typing-dots span:nth-child(3) {{ animation-delay: 0.4s; }}

        .chat-input-area {{
            display: flex; align-items: center; padding: 10px 12px;
            border-top: 1px solid #e2e8f0; background: #fff;
            gap: 8px; flex-shrink: 0;
        }}
        .chat-input-area input {{
            flex: 1; border: 1px solid #e2e8f0; border-radius: 8px;
            padding: 9px 12px; font-size: 0.83rem; outline: none;
            transition: border-color 0.15s; color: #1e293b; background: #f8fafc;
        }}
        .chat-input-area input:focus {{ border-color: #0F9ED5; background: #fff; }}
        .chat-input-area input::placeholder {{ color: #94a3b8; }}
        .chat-send-btn {{
            width: 36px; height: 36px; border-radius: 8px; border: none;
            background: linear-gradient(135deg, #0F9ED5 0%, #3C1EBA 100%);
            color: #fff; cursor: pointer; display: flex;
            align-items: center; justify-content: center;
            transition: opacity 0.15s; flex-shrink: 0;
        }}
        .chat-send-btn:hover {{ opacity: 0.85; }}
        .chat-send-btn svg {{ width: 18px; height: 18px; fill: #fff; }}

        /* ── Feedback ── */
        .chat-feedback {{
            display: flex; align-items: center; gap: 6px;
            margin-top: 4px; padding: 0 4px;
        }}
        .chat-feedback button {{
            background: none; border: none; cursor: pointer;
            padding: 3px 5px; border-radius: 4px; opacity: 0.5;
            transition: opacity 0.15s, background 0.15s;
            display: flex; align-items: center; justify-content: center;
        }}
        .chat-feedback button:hover {{ opacity: 1; background: #f1f5f9; }}
        .chat-feedback button.selected {{ opacity: 1; }}
        .chat-feedback button.selected.up {{ color: #10b981; }}
        .chat-feedback button.selected.down {{ color: #ef4444; }}
        .chat-feedback svg {{ width: 14px; height: 14px; }}
        .chat-feedback .fb-edit {{
            display: none; font-size: 0.68rem; color: #0F9ED5;
            opacity: 0.85; padding: 2px 5px; border-radius: 4px;
        }}
        .chat-feedback .fb-edit:hover {{ opacity: 1; background: #f1f5f9; }}
        .chat-feedback .fb-status {{
            font-size: 0.68rem; color: #94a3b8; margin-left: 2px;
        }}
        .chat-feedback-form {{
            display: none; margin-top: 4px; padding: 0 4px;
        }}
        .chat-feedback-form.visible {{ display: flex; gap: 4px; align-items: center; }}
        .chat-feedback-form input {{
            flex: 1; border: 1px solid #e2e8f0; border-radius: 6px;
            padding: 5px 8px; font-size: 0.72rem; outline: none;
            color: #475569; background: #f8fafc;
        }}
        .chat-feedback-form input:focus {{ border-color: #0F9ED5; }}
        .chat-feedback-form button {{
            background: #0F9ED5; border: none; color: #fff;
            font-size: 0.68rem; padding: 4px 8px; border-radius: 5px;
            cursor: pointer; white-space: nowrap;
        }}
        .chat-feedback-form button:hover {{ background: #0d8bc2; }}

        @keyframes apixCursor {{
            0%, 100% {{ opacity: 1; }}
            50% {{ opacity: 0; }}
        }}
        .typing-cursor {{
            display: inline-block; width: 2px; height: 1em;
            background: #64748b; margin-left: 1px;
            animation: apixCursor 0.7s step-end infinite;
            vertical-align: text-bottom;
        }}
        `;
        parentDoc.head.appendChild(style);

        // ── HTML ──
        var container = parentDoc.createElement('div');
        container.id = 'apixChatContainer';
        container.innerHTML = [
        '<div class="chat-box" id="apixChatBox">',
        '  <div class="chat-box-header">',
        '    <svg class="header-logo" viewBox="0 0 24 24"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 17.93c-3.95-.49-7-3.85-7-7.93 0-.62.08-1.21.21-1.79L9 15v1c0 1.1.9 2 2 2v1.93zm6.9-2.54c-.26-.81-1-1.39-1.9-1.39h-1v-3c0-.55-.45-1-1-1H8v-2h2c.55 0 1-.45 1-1V7h2c1.1 0 2-.9 2-2v-.41c2.93 1.19 5 4.06 5 7.41 0 2.08-.8 3.97-2.1 5.39z"/></svg>',
        '    <div class="chat-header-title">',
        '      <span>APIX Insight</span>',
        '      <span>Performance Intelligence Assistant</span>',
        '    </div>',
        '    <div class="chat-header-actions">',
        '      <button id="apixBtnNewSession" title="New session"><svg viewBox="0 0 24 24"><path d="M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6v2z"/></svg></button>',
        '      <button id="apixBtnHistory" title="Session history"><svg viewBox="0 0 24 24"><path d="M13 3a9 9 0 0 0-9 9H1l3.89 3.89.07.14L9 12H6c0-3.87 3.13-7 7-7s7 3.13 7 7-3.13 7-7 7c-1.93 0-3.68-.79-4.94-2.06l-1.42 1.42A8.954 8.954 0 0 0 13 21a9 9 0 0 0 0-18zm-1 5v5l4.28 2.54.72-1.21-3.5-2.08V8H12z"/></svg></button>',
        '      <button id="apixBtnMaximize" title="Maximize (60%)"><svg viewBox="0 0 24 24"><path d="M4 14h4v4H4zm0-8h4v4H4zm8 8h4v4h-4zm0-8h4v4h-4zm8 8h4v4h-4zm0-8h4v4h-4z"/></svg></button>',
        '      <button id="apixBtnFullscreen" title="Fullscreen"><svg viewBox="0 0 24 24"><path d="M7 14H5v5h5v-2H7v-3zm-2-4h2V7h3V5H5v5zm12 7h-3v2h5v-5h-2v3zM14 5v2h3v3h2V5h-5z"/></svg></button>',
        '      <button id="apixBtnMinimize" title="Minimize"><svg viewBox="0 0 24 24"><path d="M6 19h12v2H6z"/></svg></button>',
        '      <button id="apixBtnClose" title="Close"><svg viewBox="0 0 24 24"><path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/></svg></button>',
        '    </div>',
        '  </div>',
        '  <div class="chat-box-body">',
        '    <div class="chat-sidebar" id="apixSidebar">',
        '      <div class="chat-sidebar-header">Sessions</div>',
        '      <div class="chat-sidebar-list" id="apixSessionList"></div>',
        '    </div>',
        '    <div class="chat-content">',
        '      <div class="chat-messages" id="apixChatMessages"></div>',
        '      <div class="chat-input-area">',
        '        <input type="text" id="apixChatInput" placeholder="Ask APIX Insight anything..." autocomplete="off" />',
        '        <button class="chat-send-btn" id="apixChatSend" title="Send"><svg viewBox="0 0 24 24"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/></svg></button>',
        '      </div>',
        '    </div>',
        '  </div>',
        '</div>',
        '<button class="chat-fab" id="apixChatFab" title="Chat with APIX Insight">',
        '  <svg class="fab-icon" viewBox="0 0 24 24"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 17.93c-3.95-.49-7-3.85-7-7.93 0-.62.08-1.21.21-1.79L9 15v1c0 1.1.9 2 2 2v1.93zm6.9-2.54c-.26-.81-1-1.39-1.9-1.39h-1v-3c0-.55-.45-1-1-1H8v-2h2c.55 0 1-.45 1-1V7h2c1.1 0 2-.9 2-2v-.41c2.93 1.19 5 4.06 5 7.41 0 2.08-.8 3.97-2.1 5.39z"/></svg>',
        '  <span class="fab-close-icon">\\u00d7</span>',
        '  <span class="chat-fab-tooltip">APIX Insight</span>',
        '</button>'
        ].join('\\n');
        parentDoc.body.appendChild(container);

        // ── Refs ──
        var fab        = parentDoc.getElementById('apixChatFab');
        var box        = parentDoc.getElementById('apixChatBox');
        var msgs       = parentDoc.getElementById('apixChatMessages');
        var input      = parentDoc.getElementById('apixChatInput');
        var sendBtn    = parentDoc.getElementById('apixChatSend');
        var sidebar    = parentDoc.getElementById('apixSidebar');
        var sessionListEl = parentDoc.getElementById('apixSessionList');
        var btnNew     = parentDoc.getElementById('apixBtnNewSession');
        var btnHistory = parentDoc.getElementById('apixBtnHistory');
        var btnMax     = parentDoc.getElementById('apixBtnMaximize');
        var btnFull    = parentDoc.getElementById('apixBtnFullscreen');
        var btnMin     = parentDoc.getElementById('apixBtnMinimize');
        var btnClose   = parentDoc.getElementById('apixBtnClose');

        // ── Session API helpers ──
        async function apiCreateSession(title) {{
            try {{
                var r = await fetch(CTX.sessionsUrl + '/' + CTX.userId, {{
                    method: 'POST',
                    headers: authHeaders({{'Content-Type':'application/json'}}),
                    body: JSON.stringify({{title: title || ''}})
                }});
                if (r.ok) return await r.json();
            }} catch(e) {{ console.error('Create session failed', e); }}
            return null;
        }}

        async function apiListSessions() {{
            try {{
                var r = await fetch(CTX.sessionsUrl + '/' + CTX.userId, {{headers: authHeaders()}});
                if (r.ok) return await r.json();
            }} catch(e) {{ console.error('List sessions failed', e); }}
            return [];
        }}

        async function apiGetSession(sid) {{
            try {{
                var r = await fetch(CTX.sessionsUrl + '/' + CTX.userId + '/' + sid, {{headers: authHeaders()}});
                if (r.ok) return await r.json();
            }} catch(e) {{ console.error('Get session failed', e); }}
            return null;
        }}

        async function apiAddMessage(sid, role, content, aiResponded) {{
            try {{
                var payload = {{role: role, content: content}};
                if (role === 'bot') payload.ai_responded = (aiResponded !== false);
                await fetch(CTX.sessionsUrl + '/' + CTX.userId + '/' + sid + '/messages', {{
                    method: 'POST',
                    headers: authHeaders({{'Content-Type':'application/json'}}),
                    body: JSON.stringify(payload)
                }});
            }} catch(e) {{ console.error('Add message failed', e); }}
        }}

        async function apiDeleteSession(sid) {{
            try {{
                var r = await fetch(CTX.sessionsUrl + '/' + CTX.userId + '/' + sid, {{
                    method: 'DELETE',
                    headers: authHeaders()
                }});
                return r.ok;
            }} catch(e) {{ console.error('Delete session failed', e); }}
            return false;
        }}

        async function apiRenameSession(sid, newTitle) {{
            try {{
                var r = await fetch(CTX.sessionsUrl + '/' + CTX.userId + '/' + sid, {{
                    method: 'PATCH',
                    headers: authHeaders({{'Content-Type':'application/json'}}),
                    body: JSON.stringify({{title: newTitle}})
                }});
                return r.ok;
            }} catch(e) {{ console.error('Rename session failed', e); }}
            return false;
        }}

        // ── Feedback API ──
        function apiFeedback(sessionId, query, response, rating, comment) {{
            // Fire-and-forget background call
            try {{
                fetch(CTX.feedbackUrl + '/' + CTX.userId, {{
                    method: 'POST',
                    headers: authHeaders({{'Content-Type':'application/json'}}),
                    body: JSON.stringify({{
                        session_id: sessionId, query: query,
                        response: response, rating: rating, comment: comment || ''
                    }})
                }});
            }} catch(e) {{ /* silent */ }}
        }}

        // ── SVG icon strings ──
        var botAvatarSvg = '<svg viewBox="0 0 24 24"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 17.93c-3.95-.49-7-3.85-7-7.93 0-.62.08-1.21.21-1.79L9 15v1c0 1.1.9 2 2 2v1.93zm6.9-2.54c-.26-.81-1-1.39-1.9-1.39h-1v-3c0-.55-.45-1-1-1H8v-2h2c.55 0 1-.45 1-1V7h2c1.1 0 2-.9 2-2v-.41c2.93 1.19 5 4.06 5 7.41 0 2.08-.8 3.97-2.1 5.39z"/></svg>';
        var userAvatarSvg = '<svg viewBox="0 0 24 24"><path d="M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z"/></svg>';

        // ── Render helpers ──
        function addFullMsgToggle(bubble, details) {{
            // Appends a "Show full message" toggle + hidden full-text section
            // INSIDE the given bot bubble, directly beneath the short response.
            var fullWrap = parentDoc.createElement('div');
            fullWrap.className = 'chat-fullmsg-body';
            fullWrap.style.display = 'none';
            fullWrap.innerHTML = renderMd(details);

            var moreBtn = parentDoc.createElement('button');
            moreBtn.type = 'button';
            moreBtn.className = 'chat-fullmsg-toggle';
            moreBtn.textContent = 'Show full message';
            moreBtn.addEventListener('click', function() {{
                var open = fullWrap.style.display === 'none';
                fullWrap.style.display = open ? 'block' : 'none';
                moreBtn.textContent = open ? 'Hide full message' : 'Show full message';
                msgs.scrollTop = msgs.scrollHeight;
            }});

            bubble.appendChild(moreBtn);
            bubble.appendChild(fullWrap);
        }}

        function renderMessage(m, opts) {{
            opts = opts || {{}};
            var wrapper = parentDoc.createElement('div');
            wrapper.className = 'chat-msg-wrapper ' + (m.role === 'user' ? 'user' : 'bot');

            // Avatar
            var avatar = parentDoc.createElement('div');
            avatar.className = 'chat-avatar ' + (m.role === 'user' ? 'user-avatar' : 'bot-avatar');
            avatar.innerHTML = m.role === 'user' ? userAvatarSvg : botAvatarSvg;
            wrapper.appendChild(avatar);

            // Body container (bubble + time + feedback)
            var body = parentDoc.createElement('div');
            body.className = 'chat-msg-body';

            var bubble = parentDoc.createElement('div');
            bubble.className = 'chat-msg ' + (m.role === 'user' ? 'user' : 'bot');
            if (m.role !== 'user') {{
                bubble.innerHTML = renderMd(m.content);
            }} else {{
                bubble.textContent = m.content;
            }}
            // "Show full message" toggle lives INSIDE the bot bubble, right
            // under the short response (e.g. coaching tips summarized to
            // pointers). The full text expands within the same bubble.
            if (m.role === 'bot' && m.details) {{
                addFullMsgToggle(bubble, m.details);
            }}
            body.appendChild(bubble);

            var time = parentDoc.createElement('div');
            time.className = 'chat-msg-time';
            time.textContent = fmtTime(m.timestamp || new Date().toISOString());
            body.appendChild(time);

            // Feedback row (bot messages only, not for welcome msg)
            if (m.role === 'bot' && opts.showFeedback) {{
                var fb = parentDoc.createElement('div');
                fb.className = 'chat-feedback';
                fb.innerHTML = '<button class="fb-up" title="Helpful"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M2 20h2c.55 0 1-.45 1-1v-9c0-.55-.45-1-1-1H2v11zm19.83-7.12c.11-.25.17-.52.17-.8V11c0-1.1-.9-2-2-2h-5.5l.92-4.65c.05-.22.02-.46-.08-.66a4.8 4.8 0 0 0-.88-1.22L14 2 7.59 8.41C7.21 8.79 7 9.3 7 9.83v7.84A2.34 2.34 0 0 0 9.34 20h8.11c.7 0 1.36-.37 1.72-.97l2.66-6.15z"/></svg></button>'
                    + '<button class="fb-down" title="Not helpful"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M22 4h-2c-.55 0-1 .45-1 1v9c0 .55.45 1 1 1h2V4zM2.17 11.12c-.11.25-.17.52-.17.8V13c0 1.1.9 2 2 2h5.5l-.92 4.65c-.05.22-.02.46.08.66.23.4.52.77.88 1.09L10 22l6.41-6.41c.38-.38.59-.89.59-1.42V6.34A2.34 2.34 0 0 0 14.66 4H6.56c-.71 0-1.37.37-1.73.97L2.17 11.12z"/></svg></button>'
                    + '<button class="fb-edit" title="Edit your feedback" type="button">Edit</button>'
                    + '<span class="fb-status"></span>';
                body.appendChild(fb);

                var fbForm = parentDoc.createElement('div');
                fbForm.className = 'chat-feedback-form';
                fbForm.innerHTML = '<input type="text" placeholder="Add a comment (optional)" maxlength="300" /><button>Save</button>';
                body.appendChild(fbForm);

                // Feedback event handlers (closure over m). Supports editing:
                // both ratings stay toggle-able and the comment can be modified
                // and re-saved; the backend upserts the same message's entry.
                (function(query, response, feedbackRow, form) {{
                    var upBtn = feedbackRow.querySelector('.fb-up');
                    var downBtn = feedbackRow.querySelector('.fb-down');
                    var editBtn = feedbackRow.querySelector('.fb-edit');
                    var statusEl = feedbackRow.querySelector('.fb-status');
                    var formBtn = form.querySelector('button');
                    var formInput = form.querySelector('input');
                    var currentRating = '';
                    var currentComment = '';

                    function save() {{
                        if (!currentRating) {{ return; }}
                        apiFeedback(currentSessionId, query, response, currentRating, currentComment);
                        statusEl.textContent = 'Saved \u2713';
                        editBtn.style.display = 'inline-flex';
                    }}

                    function selectRating(r) {{
                        currentRating = r;
                        upBtn.classList.toggle('selected', r === 'up');
                        upBtn.classList.toggle('up', r === 'up');
                        downBtn.classList.toggle('selected', r === 'down');
                        downBtn.classList.toggle('down', r === 'down');
                    }}

                    upBtn.addEventListener('click', function() {{
                        selectRating('up');
                        form.classList.remove('visible');
                        save();
                    }});
                    downBtn.addEventListener('click', function() {{
                        selectRating('down');
                        formInput.value = currentComment;
                        form.classList.add('visible');
                        formInput.focus();
                    }});
                    editBtn.addEventListener('click', function() {{
                        formInput.value = currentComment;
                        form.classList.toggle('visible');
                        if (form.classList.contains('visible')) {{ formInput.focus(); }}
                    }});
                    formBtn.addEventListener('click', function() {{
                        currentComment = formInput.value.trim();
                        if (!currentRating) {{ selectRating('down'); }}
                        form.classList.remove('visible');
                        save();
                    }});
                    formInput.addEventListener('keydown', function(e) {{
                        if (e.key === 'Enter') {{ formBtn.click(); }}
                    }});
                }})(opts.query || '', m.content, fb, fbForm);
            }}

            wrapper.appendChild(body);
            return wrapper;
        }}

        // ── Typing effect ──
        function typeMessage(wrapper, bubble, fullText, callback) {{
            // Type as plain escaped text, then swap to rendered markdown at the end
            var plainHtml = escHtml(fullText).replace(/\\n/g, '<br>');
            var words = plainHtml.split(/(?=\\s)|(?<=\\s)/);
            var i = 0;
            bubble.innerHTML = '<span class="typing-cursor"></span>';
            var interval = setInterval(function() {{
                if (i < words.length) {{
                    var cursor = bubble.querySelector('.typing-cursor');
                    if (cursor) cursor.remove();
                    bubble.innerHTML += words[i];
                    bubble.innerHTML += '<span class="typing-cursor"></span>';
                    i++;
                    msgs.scrollTop = msgs.scrollHeight;
                }} else {{
                    clearInterval(interval);
                    // Replace with fully rendered markdown
                    bubble.innerHTML = renderMd(fullText);
                    if (callback) callback();
                }}
            }}, 20);
        }}

        function renderAllMessages() {{
            msgs.innerHTML = '';
            currentMessages.forEach(function(m) {{
                msgs.appendChild(renderMessage(m));
            }});
            msgs.scrollTop = msgs.scrollHeight;
        }}

        function renderSessionList() {{
            sessionListEl.innerHTML = '';
            sessionsList.forEach(function(s) {{
                var item = parentDoc.createElement('div');
                item.className = 'chat-session-item' + (s.session_id === currentSessionId ? ' active' : '');

                // Info column (title + meta)
                var info = parentDoc.createElement('div');
                info.className = 'session-info';
                info.innerHTML = '<div class="session-title">' + escHtml(s.title) + '</div>'
                    + '<div class="session-meta">' + fmtTime(s.updated_at || s.created_at)
                    + ' \\u00b7 ' + (s.message_count || 0) + ' msgs</div>';
                item.appendChild(info);

                // Action buttons (edit + delete)
                var actions = parentDoc.createElement('div');
                actions.className = 'session-actions';
                actions.innerHTML = '<button class="btn-edit" title="Rename"><svg viewBox="0 0 24 24"><path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04a1 1 0 0 0 0-1.41l-2.34-2.34a1 1 0 0 0-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z"/></svg></button>'
                    + '<button class="btn-delete" title="Delete"><svg viewBox="0 0 24 24"><path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/></svg></button>';
                item.appendChild(actions);

                // Click info area to load session (lazy-load on click)
                info.addEventListener('click', function() {{ loadSession(s.session_id); }});

                // Rename handler
                (function(sess, infoEl) {{
                    actions.querySelector('.btn-edit').addEventListener('click', function(e) {{
                        e.stopPropagation();
                        var titleEl = infoEl.querySelector('.session-title');
                        var oldTitle = sess.title;
                        // Replace title with input
                        var inp = parentDoc.createElement('input');
                        inp.className = 'session-edit-input';
                        inp.value = oldTitle;
                        titleEl.replaceWith(inp);
                        inp.focus();
                        inp.select();

                        function commitRename() {{
                            var newTitle = inp.value.trim() || oldTitle;
                            // Restore title element
                            var newTitleEl = parentDoc.createElement('div');
                            newTitleEl.className = 'session-title';
                            newTitleEl.textContent = newTitle;
                            inp.replaceWith(newTitleEl);
                            if (newTitle !== oldTitle) {{
                                sess.title = newTitle;
                                apiRenameSession(sess.session_id, newTitle);
                            }}
                        }}
                        inp.addEventListener('blur', commitRename);
                        inp.addEventListener('keydown', function(ev) {{
                            if (ev.key === 'Enter') {{ ev.preventDefault(); inp.blur(); }}
                            if (ev.key === 'Escape') {{ inp.value = oldTitle; inp.blur(); }}
                        }});
                    }});
                }})(s, info);

                // Delete handler
                (function(sess) {{
                    actions.querySelector('.btn-delete').addEventListener('click', async function(e) {{
                        e.stopPropagation();
                        var ok = await apiDeleteSession(sess.session_id);
                        if (ok) {{
                            // Remove from local list
                            sessionsList = sessionsList.filter(function(x) {{ return x.session_id !== sess.session_id; }});
                            // If we deleted the active session, load another or create new
                            if (sess.session_id === currentSessionId) {{
                                if (sessionsList.length > 0) {{
                                    loadSession(sessionsList[0].session_id);
                                }} else {{
                                    createNewSession();
                                }}
                            }}
                            renderSessionList();
                        }}
                    }});
                }})(s);

                sessionListEl.appendChild(item);
            }});
        }}

        // ── Session logic ──
        // Persist the *active* session id across Streamlit page navigations
        // (each navigation re-injects this script and resets in-memory state).
        // Stored in the parent window's sessionStorage keyed by the logged-in
        // user, so the same conversation is resumed on every screen until the
        // user logs out or a different user logs in.
        var _SESSION_STORE_KEY = 'apix_chat_session';
        function saveActiveSession(sid) {{
            try {{
                window.parent.sessionStorage.setItem(
                    _SESSION_STORE_KEY,
                    JSON.stringify({{ userId: CTX.userId, sessionId: sid }})
                );
            }} catch(e) {{}}
        }}
        function getActiveSession() {{
            try {{
                var raw = window.parent.sessionStorage.getItem(_SESSION_STORE_KEY);
                if (!raw) return null;
                var obj = JSON.parse(raw);
                // Only resume when it belongs to the current user (guards against
                // a different user logging in within the same browser tab).
                if (!obj || obj.userId !== CTX.userId) return null;
                return obj.sessionId || null;
            }} catch(e) {{ return null; }}
        }}

        async function createNewSession() {{
            if (!CTX.userId) return;
            var session = await apiCreateSession('');
            if (session) {{
                currentSessionId = session.session_id;
                saveActiveSession(currentSessionId);
                currentMessages = [{{
                    role: 'bot',
                    content: "Hi! I\\u2019m APIX Insight, your Performance Intelligence assistant. How can I help you today?",
                    timestamp: session.created_at
                }}];
                await apiAddMessage(currentSessionId, 'bot', currentMessages[0].content);
                renderAllMessages();
                await refreshSessionList();
            }}
        }}

        async function loadSession(sid) {{
            var data = await apiGetSession(sid);
            if (data) {{
                currentSessionId = data.session_id;
                saveActiveSession(currentSessionId);
                currentMessages = data.messages || [];
                renderAllMessages();
                renderSessionList();
            }}
        }}

        // Resume the persisted session if one exists for this user; otherwise
        // start a fresh one. Called whenever the chat is opened so navigating
        // between screens keeps the same conversation instead of resetting it.
        async function resumeOrCreateSession() {{
            if (currentSessionId) return;
            var saved = getActiveSession();
            if (saved) {{
                var data = await apiGetSession(saved);
                if (data && data.session_id) {{
                    currentSessionId = data.session_id;
                    saveActiveSession(currentSessionId);
                    currentMessages = data.messages || [];
                    renderAllMessages();
                    renderSessionList();
                    return;
                }}
            }}
            await createNewSession();
        }}


        async function refreshSessionList() {{
            sessionsList = await apiListSessions();
            renderSessionList();
        }}

        async function initSessions() {{
            // Populate the session list but do not auto-create or auto-load any
            // session. A new session will be created when the user opens the
            // chat via the FAB (per product request).
            if (!CTX.userId) return;
            sessionsList = await apiListSessions();
            renderSessionList();
        }}

        // ── Toggle sidebar (only in large/full modes) ──
        function updateSidebar() {{
            if ((sizeMode === 'large' || sizeMode === 'full') && sidebarOpen) {{
                sidebar.classList.add('visible');
            }} else {{
                sidebar.classList.remove('visible');
            }}
        }}

        // ── Open/Close ──
        function openChat() {{
            box.classList.remove('closing-anim');
            box.classList.add('open');
            fab.classList.remove('closing');
            fab.classList.add('active');
            setTimeout(function() {{ input.focus(); }}, 350);
        }}

        function closeChat() {{
            box.classList.add('closing-anim');
            fab.classList.remove('active');
            fab.classList.add('closing');
            try {{ window.parent.sessionStorage.setItem('apix_chat_closed', '1'); }} catch(e) {{}}
            setTimeout(function() {{
                box.classList.remove('open', 'closing-anim', 'size-large', 'size-full');
                fab.classList.remove('closing');
                sizeMode = 'normal';
                sidebarOpen = false;
                updateSidebar();
            }}, 250);
        }}

        fab.addEventListener('click', async function() {{
            if (box.classList.contains('open')) {{
                closeChat();
            }} else {{
                openChat();
                // Resume the user's existing session (persisted across screen
                // navigations) or create one only if none exists yet. The
                // session lives until logout or a different user logs in.
                try {{
                    await resumeOrCreateSession();
                }} catch(e) {{
                    console.error('Failed to resume/create session on open', e);
                }}
            }}
        }});

        btnMin.addEventListener('click', function() {{ closeChat(); }});
        btnClose.addEventListener('click', function() {{ closeChat(); }});

        // ── New session ──
        btnNew.addEventListener('click', function() {{ createNewSession(); }});

        // ── History toggle ──
        btnHistory.addEventListener('click', function() {{
            sidebarOpen = !sidebarOpen;
            if (sidebarOpen) {{
                // If in normal mode, auto-expand to large to show sidebar
                if (sizeMode === 'normal') {{
                    box.classList.add('size-large');
                    sizeMode = 'large';
                }}
                refreshSessionList();
            }}
            updateSidebar();
        }});

        // ── Maximize (60%) ──
        btnMax.addEventListener('click', function() {{
            if (sizeMode === 'large') {{
                box.classList.remove('size-large');
                sizeMode = 'normal';
                sidebarOpen = false;
            }} else {{
                box.classList.remove('size-full');
                box.classList.add('size-large');
                sizeMode = 'large';
            }}
            updateSidebar();
        }});

        // ── Fullscreen ──
        function applyFullscreenPosition() {{
            // Detect Streamlit sidebar width and header height
            var stSidebar = parentDoc.querySelector('[data-testid="stSidebar"]');
            var stHeader  = parentDoc.querySelector('header[data-testid="stHeader"]')
                         || parentDoc.querySelector('.stAppHeader')
                         || parentDoc.querySelector('header');
            var sidebarW = stSidebar ? stSidebar.getBoundingClientRect().width : 0;
            var headerH  = stHeader  ? stHeader.getBoundingClientRect().height  : 0;

            // Position: left edge after sidebar + gap, top below header + gap
            var leftPx = Math.round(sidebarW + 16);
            var topPx  = Math.round(headerH + 12);
            box.style.setProperty('--apix-full-left', leftPx + 'px');
            box.style.setProperty('--apix-full-top', topPx + 'px');
        }}

        btnFull.addEventListener('click', function() {{
            if (sizeMode === 'full') {{
                box.classList.remove('size-full');
                box.style.removeProperty('--apix-full-left');
                box.style.removeProperty('--apix-full-top');
                sizeMode = 'normal';
                sidebarOpen = false;
            }} else {{
                box.classList.remove('size-large');
                applyFullscreenPosition();
                box.classList.add('size-full');
                sizeMode = 'full';
                // Auto-show sidebar in full screen
                sidebarOpen = true;
                refreshSessionList();
            }}
            updateSidebar();
        }});

        // ── Send message ──
        function rmTyping() {{
            var t = parentDoc.getElementById('apixTyping');
            if (t) t.remove();
        }}

        async function sendMsg() {{
            var text = input.value.trim();
            if (!text) return;

            try {{ window.parent.sessionStorage.removeItem('apix_chat_closed'); }} catch(e) {{}}

            // Ensure we have a session
            if (!currentSessionId) {{
                await resumeOrCreateSession();
            }}

            var now = new Date().toISOString();

            // User message
            var userMsg = {{role: 'user', content: text, timestamp: now}};
            currentMessages.push(userMsg);
            msgs.appendChild(renderMessage(userMsg));
            input.value = '';
            msgs.scrollTop = msgs.scrollHeight;

            // Persist user message to session
            apiAddMessage(currentSessionId, 'user', text);

            // Typing indicator
            var typing = parentDoc.createElement('div');
            typing.className = 'chat-typing';
            typing.id = 'apixTyping';
            typing.innerHTML = '<div class="chat-avatar bot-avatar">' + botAvatarSvg + '</div>'
                + '<div class="chat-typing-dots"><span></span><span></span><span></span></div>';
            msgs.appendChild(typing);
            msgs.scrollTop = msgs.scrollHeight;

            // Validate prerequisites
            if (!CTX.userId) {{
                rmTyping();
                var errMsg = {{role:'bot', content:"Please log in first so I can access your team\\u2019s data.", timestamp: new Date().toISOString()}};
                currentMessages.push(errMsg);
                msgs.appendChild(renderMessage(errMsg));
                apiAddMessage(currentSessionId, 'bot', errMsg.content, false);
                return;
            }}
            var ra = CTX.reportingAgents;
            if (!ra || (Array.isArray(ra) && ra.length === 0) ||
                (!Array.isArray(ra) && typeof ra === 'object' && Object.keys(ra).length === 0)) {{
                rmTyping();
                var errMsg2 = {{role:'bot', content:"I couldn\\u2019t find any employees assigned to you for the selected week. Please check the sidebar filters.", timestamp: new Date().toISOString()}};
                currentMessages.push(errMsg2);
                msgs.appendChild(renderMessage(errMsg2));
                apiAddMessage(currentSessionId, 'bot', errMsg2.content, false);
                return;
            }}

            // POST to chat_agent API
            try {{
                var ac = new AbortController();
                var tid = setTimeout(function() {{ ac.abort(); }}, CTX.timeoutMs);

                var resp = await fetch(CTX.apiUrl, {{
                    method: 'POST',
                    headers: authHeaders({{'Content-Type':'application/json', 'Accept':'application/json'}}),
                    body: JSON.stringify({{
                        user_query: text,
                        user_id: CTX.userId,
                        user_name: CTX.userName,
                        role: CTX.role,
                        reporting_agents: CTX.reportingAgents,
                        session_id: currentSessionId || null,
                        focus_agent_id: CTX.focusAgentId,
                        focus_scope: CTX.focusScope,
                        period: CTX.period,
                    }}),
                    signal: ac.signal,
                }});
                clearTimeout(tid);
                rmTyping();

                var botContent = '';
                var botDetails = null;
                var aiOk = false;
                if (resp.ok) {{
                    var data = await resp.json();
                    botContent = data.response || "No response generated.";
                    botDetails = data.details || null;
                    aiOk = true;
                }} else {{
                    var detail = "";
                    try {{ detail = (await resp.json()).detail || ""; }} catch(e) {{}}
                    botContent = "Something went wrong: " + (detail || "HTTP " + resp.status);
                }}

                var botMsg = {{role: 'bot', content: botContent, details: botDetails, timestamp: new Date().toISOString()}};
                currentMessages.push(botMsg);

                // Render with typing effect + feedback
                var botWrapper = renderMessage(botMsg, {{showFeedback: true, query: text}});
                var botBubble = botWrapper.querySelector('.chat-msg.bot');
                botBubble.innerHTML = '';  // Clear for typing effect
                msgs.appendChild(botWrapper);
                msgs.scrollTop = msgs.scrollHeight;

                typeMessage(botWrapper, botBubble, botContent, function() {{
                    // Typing rewrote the bubble; re-attach the in-bubble
                    // "Show full message" toggle when a full version exists.
                    if (botDetails) {{ addFullMsgToggle(botBubble, botDetails); }}
                    msgs.scrollTop = msgs.scrollHeight;
                }});

                // Persist bot response
                apiAddMessage(currentSessionId, 'bot', botContent, aiOk);

            }} catch(err) {{
                rmTyping();
                var errContent = '';
                if (err.name === 'AbortError') {{
                    errContent = "The request timed out. Please try a simpler question.";
                }} else {{
                    errContent = "The analytics engine is currently unavailable. Please make sure the chat agent server is running.";
                }}
                var errBotMsg = {{role: 'bot', content: errContent, timestamp: new Date().toISOString()}};
                currentMessages.push(errBotMsg);
                var errWrapper = renderMessage(errBotMsg, {{showFeedback: false}});
                msgs.appendChild(errWrapper);
                msgs.scrollTop = msgs.scrollHeight;
                apiAddMessage(currentSessionId, 'bot', errContent, false);
            }}
        }}

        sendBtn.addEventListener('click', sendMsg);
        input.addEventListener('keydown', function(e) {{
            if (e.key === 'Enter') {{ e.preventDefault(); sendMsg(); }}
        }});

        // ── Initialize sessions on load ──
        initSessions();

        // NOTE: Do not auto-open the chat on page load. The chat will open
        // only when the user clicks the FAB. This avoids unexpected popups.
    }})();
    </script>
    """, height=0)
