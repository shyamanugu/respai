"""
ui/styles.py — Custom CSS styling for the Streamlit app
========================================================
"""

import streamlit as st
from contextlib import contextmanager


def load_custom_css():
    st.markdown("""
    <style>
    /* Enterprise color scheme */
    :root {
        --primary: #1e40af;
        --secondary: #7c3aed;
        --success: #059669;
        --warning: #d97706;
        --danger: #dc2626;
        --bg: #f8fafc;
        --card: #ffffff;
        --text: #0f172a;
        --text-muted: #64748b;
        --border: #e2e8f0;

        /* Header gradient stops (used for both main header and login screen header) */
        --grad-cyan:   #0F9ED5;
        --grad-indigo: #3C1EBA;
        --grad-magenta:#A02B93;
    }
    
    /* Hide default Streamlit elements */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    
    /* Main container */
    .block-container {
        margin: 0;
        padding: 0;
        top: 0;
        left: 0;
        max-width: 80%;
    }
    
    /* Header styling (applies to main header and login header) */
    .enterprise-header {
        background: linear-gradient(135deg, var(--grad-cyan) 0%, var(--grad-indigo) 55%, var(--grad-magenta) 100%);
        color: white;
        padding: 2rem; 
        border-radius: 12px;
        margin-top: 4rem;
        margin-bottom: 2rem;
        box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1);
        text-align: center;
    }
    
    .enterprise-header h1 {
        margin: 0;
        font-size: 2rem;
        font-weight: 700;
    }
    
    .enterprise-header .subtitle {
        font-size: 0.95rem;
        opacity: 0.9;
        margin-top: 0.5rem;
    }
    
    /* Force equal-height columns for KPI cards */
    [data-testid="stHorizontalBlock"] {
        display: flex !important;
        align-items: stretch !important;
    }
    
    [data-testid="column"] {
        display: flex !important;
        flex-direction: column !important;
    }
    
    [data-testid="column"] > div {
        flex: 1 !important;
        display: flex !important;
        flex-direction: column !important;
    }
    
    [data-testid="column"] > div > div {
        flex: 1 !important;
        display: flex !important;
        flex-direction: column !important;
    }
    
    /* Modern KPI cards with glassmorphism and interactive states */
    /* Streamlit column spacing fix */
    [data-testid="column"] {
        padding: 0 0.5rem !important;
    }
    [data-testid="column"]:first-child {
        padding-left: 0 !important;
    }
    [data-testid="column"]:last-child {
        padding-right: 0 !important;
    }
    
    .kpi-card {
        background: linear-gradient(135deg, rgba(255,255,255,0.98) 0%, rgba(248,250,252,0.95) 100%);
        backdrop-filter: blur(10px);
        border-radius: 12px;
        padding: 1rem 1.25rem;
        box-shadow: 0 2px 8px rgba(0,0,0,0.06);
        border: 1px solid rgba(226,232,240,0.8);
        border-left: 3px solid var(--primary);
        transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        position: relative;
        overflow: hidden;
        height: 100px;
        min-height: 100px;
        max-height: 100px;
        box-sizing: border-box;
        margin-bottom: 0.75rem;
    }
    
    /* Clickable KPI cards - special styling */
    .kpi-card.clickable {
        cursor: pointer;
        user-select: none;
        animation: subtlePulse 4s ease-in-out infinite;
        position: relative;
    }
    
    /* Subtle breathing animation for discoverability */
    @keyframes subtlePulse {
        0%, 100% {
            box-shadow: 0 8px 32px rgba(0,0,0,0.08);
            border-left-color: var(--primary);
        }
        50% {
            box-shadow: 0 8px 36px rgba(15, 158, 213, 0.16);
            border-left-color: rgba(15, 158, 213, 0.8);
        }
    }
    
    /* Invisible click-target button — zero layout impact */
    [data-testid="stColumn"]:has(.kpi-card.clickable) div:has(> [data-testid="stButton"]) {
        flex: 0 0 0px !important;
        height: 0px !important;
        min-height: 0px !important;
        overflow: visible !important;
        margin: 0 !important;
        padding: 0 !important;
    }
    [data-testid="stColumn"]:has(.kpi-card.clickable) [data-testid="stButton"] {
        height: 0px !important;
        min-height: 0px !important;
        overflow: visible !important;
        margin: 0 !important;
        padding: 0 !important;
    }
    [data-testid="stColumn"]:has(.kpi-card.clickable) [data-testid="stButton"] button {
        position: relative;
        top: -100px;
        width: 100%;
        height: 100px;
        background: transparent !important;
        border: none !important;
        box-shadow: none !important;
        color: transparent !important;
        font-size: 0 !important;
        min-height: 0 !important;
        padding: 0 !important;
        cursor: pointer;
        z-index: 5;
    }
    /* Expand icon — always visible on clickable cards, top-right */
    [data-testid="stColumn"]:has(.kpi-card.clickable) [data-testid="stButton"] button::after {
        content: '⛶';
        position: absolute;
        top: 6px;
        right: 15px;
        font-size: 1rem;
        line-height: 1;
        color: var(--danger);
        pointer-events: none;
    }
    
    /* Gradient top border */
    .kpi-card:not(.clickable)::before {
        content: '';
        position: absolute;
        top: 0;
        left: 0;
        right: 0;
        height: 2px;
        background: linear-gradient(90deg, var(--grad-cyan), var(--grad-indigo), var(--grad-magenta));
        opacity: 0;
        transition: opacity 0.3s ease;
    }
    
    .kpi-card:hover {
        transform: translateY(-2px);
        box-shadow: 0 6px 20px rgba(15, 158, 213, 0.15);
        border-left-color: var(--grad-indigo);
        animation: none;
    }
    
    /* Show gradient border on hover for non-clickable cards */
    .kpi-card:not(.clickable):hover::before {
        opacity: 1;
    }
    
    .kpi-card.clickable:hover {
        box-shadow: 0 8px 24px rgba(15, 158, 213, 0.2);
        background: linear-gradient(135deg, rgba(255,255,255,1) 0%, rgba(248,250,252,0.98) 100%);
        border-left-width: 4px;
    }
    

    
    .kpi-card:not(.clickable):hover::before {
        opacity: 1;
    }
    
    .kpi-card.clickable:active {
        transform: translateY(-3px) scale(1.01);
        transition: all 0.1s ease;
        box-shadow: 0 16px 48px rgba(15, 158, 213, 0.3);
    }
    
    .kpi-value {
        font-size: 1.75rem;
        font-weight: 700;
        line-height: 1.1;
        margin-bottom: 0.25rem;
        color: #1e293b;
    }
    
    .kpi-label {
        font-size: 0.7rem;
        color: var(--text-muted);
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        line-height: 1.3;
    }
    
    .kpi-delta {
        font-size: 0.75rem;
        font-weight: 600;
        margin-top: 0.35rem;
    }
    
    .kpi-delta.positive {
        color: var(--success);
    }
    
    .kpi-delta.negative {
        color: var(--danger);
    }
    
    /* Alert boxes */
    .alert-box {
        padding: 1rem;
        border-radius: 8px;
        margin: 1rem 0;
        border-left: 4px solid;
    }
    
    .alert-critical {
        background: #fef2f2;
        border-color: var(--danger);
        color: #991b1b;
    }
    
    .alert-warning {
        background: #fffbeb;
        border-color: var(--warning);
        color: #92400e;
    }
    
    .alert-info {
        background: #eff6ff;
        border-color: var(--primary);
        color: #1e40af;
    }
    
    /* Action items */
    .action-item {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 1rem;
        background: white;
        border-radius: 8px;
        margin: 0.5rem 0;
        border: 1px solid var(--border);
    }
    
    .action-status {
        padding: 0.25rem 0.75rem;
        border-radius: 12px;
        font-size: 0.75rem;
        font-weight: 600;
        text-transform: uppercase;
    }
    
    .status-completed {
        background: #d1fae5;
        color: var(--success);
    }
    
    .status-in-progress {
        background: #dbeafe;
        color: var(--primary);
    }
    
    .status-pending {
        background: #fef3c7;
        color: var(--warning);
    }
    
    /* Tabs styling */
    .stTabs [data-baseweb="tab-list"] {
        gap: 2rem;
        border-bottom: 2px solid var(--border);
    }
    
    .stTabs [data-baseweb="tab"] {
        padding: 1rem 1.5rem;
        font-weight: 600;
    }
    
    /* Escalation card styling */
    .escalation-card {
        background: white;
        border-radius: 8px;
        padding: 1rem;
        margin: 0.5rem 0;
        border-left: 4px solid var(--warning);
        box-shadow: 0 1px 3px rgba(0,0,0,0.1);
    }
    
    .escalation-priority-high, .escalation-priority-very-high {
        border-left-color: var(--danger);
    }
    
    .escalation-priority-medium {
        border-left-color: var(--warning);
    }
    
    .escalation-priority-low {
        border-left-color: var(--primary);
    }
    
    .transcript-line {
        padding: 0.5rem;
        margin: 0.25rem 0;
        border-radius: 4px;
        background: #f8fafc;
    }
    
    .transcript-speaker {
        font-weight: 600;
        color: var(--primary);
    }
    
    /* Experience/Outcome badges */
    .experience-badge {
        display: inline-block;
        padding: 0.5rem 1rem;
        border-radius: 6px;
        font-weight: 600;
        margin: 0.25rem;
    }
    
    .experience-good {
        background: #d1fae5;
        color: var(--success);
    }
    
    .experience-medium {
        background: #fef3c7;
        color: var(--warning);
    }
    
    .experience-poor {
        background: #fef2f2;
        color: var(--danger);
    }
    
    /* KPI Detail Button Styling */
    .stButton > button[kind="secondary"] {
        background: linear-gradient(135deg, var(--grad-cyan) 0%, var(--grad-indigo) 100%);
        color: white;
        border: none;
        padding: 0.4rem 0.8rem;
        font-size: 0.75rem;
        font-weight: 600;
        margin-top: 0.5rem;
        transition: all 0.2s;
    }
    
    .stButton > button[kind="secondary"]:hover {
        transform: translateY(-1px);
        box-shadow: 0 4px 8px rgba(0,0,0,0.15);
    }
    
    /* Completely invisible button for clickable KPIs */
    div[data-testid="column"] button[key^="kpi_btn_"] {
        opacity: 0 !important;
        position: relative !important;
        width: 100% !important;
        height: 0 !important;
        top: -200px !important;
        left: 0 !important;
        border: none !important;
        background: transparent !important;
        cursor: pointer !important;
        z-index: 5 !important;
        padding: 0 !important;
        margin: 0 !important;
        min-height: 200px !important;
        overflow: visible !important;
        pointer-events: auto !important;
    }
    
    /* Ensure button container doesn't add spacing */
    div[data-testid="column"] .stButton:has(button[key^="kpi_btn_"]) {
        height: 0 !important;
        margin: 0 !important;
        padding: 0 !important;
        overflow: visible !important;
    }
    
    /* Hide button text completely */
    div[data-testid="column"] button[key^="kpi_btn_"] p,
    div[data-testid="column"] button[key^="kpi_btn_"] div {
        display: none !important;
    }
    
    /* Container setup for layered clickable cards */
    div[data-testid="column"]:has(button[key^="kpi_btn_"]) {
        position: relative !important;
    }
    
    /* Modern Detail Button */
    div[data-testid="column"] .stButton button {
        background: linear-gradient(135deg, rgba(15, 158, 213, 0.1) 0%, rgba(60, 30, 186, 0.1) 100%);
        border: 1.5px solid rgba(15, 158, 213, 0.3);
        color: var(--grad-indigo);
        font-weight: 600;
        font-size: 0.8rem;
        padding: 0.5rem 1rem;
        border-radius: 10px;
        transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        margin-top: 0.75rem;
        letter-spacing: 0.02em;
    }
    
    div[data-testid="column"] .stButton button:hover {
        background: linear-gradient(135deg, var(--grad-cyan) 0%, var(--grad-indigo) 100%);
        color: white;
        border-color: transparent;
        transform: translateY(-2px);
        box-shadow: 0 8px 24px rgba(15, 158, 213, 0.3);
    }
    
    /* Modern Modal/Dialog Styling - 2025/2026 Design */
    .modal-backdrop {
        position: fixed;
        top: 0;
        left: 0;
        right: 0;
        bottom: 0;
        background: rgba(15, 23, 42, 0.75);
        backdrop-filter: blur(12px) saturate(180%);
        -webkit-backdrop-filter: blur(12px) saturate(180%);
        z-index: 9998;
        animation: backdropFadeIn 0.4s cubic-bezier(0.4, 0, 0.2, 1);
    }
    
    .modal-container {
        position: fixed;
        top: 50%;
        left: 50%;
        transform: translate(-50%, -50%);
        width: 92%;
        max-width: 1400px;
        max-height: 90vh;
        background: linear-gradient(145deg, rgba(255, 255, 255, 0.95) 0%, rgba(248, 250, 252, 0.98) 100%);
        backdrop-filter: blur(40px);
        border-radius: 32px;
        box-shadow: 
            0 60px 120px rgba(15, 158, 213, 0.15),
            0 25px 50px rgba(60, 30, 186, 0.1),
            0 0 0 1px rgba(255, 255, 255, 0.3),
            inset 0 1px 0 rgba(255, 255, 255, 0.8);
        z-index: 9999;
        overflow: hidden;
        animation: modalEntrance 0.5s cubic-bezier(0.34, 1.56, 0.64, 1);
        border: 1px solid rgba(255, 255, 255, 0.2);
    }
    
    @keyframes backdropFadeIn {
        from { 
            opacity: 0;
            backdrop-filter: blur(0px);
        }
        to { 
            opacity: 1;
            backdrop-filter: blur(12px) saturate(180%);
        }
    }
    
    @keyframes modalEntrance {
        0% {
            opacity: 0;
            transform: translate(-50%, -48%) scale(0.9) rotateX(10deg);
            filter: blur(10px);
        }
        100% {
            opacity: 1;
            transform: translate(-50%, -50%) scale(1) rotateX(0deg);
            filter: blur(0px);
        }
    }
    
    .modal-header {
        background: linear-gradient(135deg, #0F9ED5 0%, #3C1EBA 50%, #A02B93 100%);
        padding: 2.5rem 3rem;
        color: white;
        position: relative;
        overflow: hidden;
        border-radius: 32px 32px 0 0;
    }
    
    /* Animated gradient overlay on header */
    .modal-header::before {
        content: '';
        position: absolute;
        top: 0;
        left: -100%;
        width: 100%;
        height: 100%;
        background: linear-gradient(90deg, transparent, rgba(255,255,255,0.1), transparent);
        animation: shimmer 3s infinite;
    }
    
    @keyframes shimmer {
        0%, 100% { left: -100%; }
        50% { left: 100%; }
    }
    
    .modal-close {
        position: absolute;
        top: 2rem;
        right: 2.5rem;
        background: rgba(255, 255, 255, 0.15);
        backdrop-filter: blur(10px);
        border: 1px solid rgba(255, 255, 255, 0.2);
        color: white;
        font-size: 1.25rem;
        width: 48px;
        height: 48px;
        border-radius: 16px;
        cursor: pointer;
        transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        display: flex;
        align-items: center;
        justify-content: center;
        font-weight: 400;
        z-index: 10;
    }
    
    .modal-close:hover {
        background: rgba(255, 255, 255, 0.25);
        transform: rotate(90deg) scale(1.1);
        box-shadow: 0 8px 24px rgba(0, 0, 0, 0.2);
    }
    
    .modal-close:active {
        transform: rotate(90deg) scale(0.95);
    }
    
    .modal-body {
        padding: 3rem;
        max-height: calc(90vh - 180px);
        overflow-y: auto;
        overflow-x: hidden;
        background: transparent;
    }
    
    /* Custom scrollbar styling */
    .modal-body::-webkit-scrollbar {
        width: 10px;
    }
    
    .modal-body::-webkit-scrollbar-track {
        background: rgba(241, 245, 249, 0.5);
        border-radius: 10px;
        margin: 10px 0;
    }
    
    .modal-body::-webkit-scrollbar-thumb {
        background: linear-gradient(135deg, #0F9ED5, #3C1EBA);
        border-radius: 10px;
        border: 2px solid rgba(255, 255, 255, 0.3);
        transition: all 0.3s ease;
    }
    
    .modal-body::-webkit-scrollbar-thumb:hover {
        background: linear-gradient(135deg, #3C1EBA, #A02B93);
        border-color: rgba(255, 255, 255, 0.5);
    }
    
    /* Floating close button at bottom */
    .modal-footer-button {
        position: sticky;
        bottom: 2rem;
        left: 50%;
        transform: translateX(-50%);
        margin-top: 2rem;
        z-index: 100;
    }
    
    .stExpander {
        background: linear-gradient(135deg, rgba(255,255,255,0.98) 0%, rgba(248,250,252,0.95) 100%);
        backdrop-filter: blur(20px);
        border-radius: 20px;
        border: 1px solid rgba(15, 158, 213, 0.2);
        box-shadow: 0 20px 60px rgba(0,0,0,0.15);
        margin: 1.5rem 0;
        padding: 0.5rem;
        animation: modalFadeIn 0.4s cubic-bezier(0.4, 0, 0.2, 1);
    }
    
    @keyframes modalFadeIn {
        from {
            opacity: 0;
            transform: translateY(20px) scale(0.95);
        }
        to {
            opacity: 1;
            transform: translateY(0) scale(1);
        }
    }
    
    .stExpander details {
        border: none !important;
    }
    
    .stExpander summary {
        background: linear-gradient(135deg, var(--grad-cyan) 0%, var(--grad-indigo) 55%, var(--grad-magenta) 100%);
        color: white;
        padding: 1.25rem 1.5rem;
        border-radius: 16px;
        font-weight: 700;
        font-size: 1.1rem;
        letter-spacing: 0.02em;
        transition: all 0.3s ease;
    }
    
    .stExpander summary:hover {
        box-shadow: 0 8px 24px rgba(15, 158, 213, 0.3);
        transform: translateX(4px);
    }
    
    /* Detail Cards inside Modal */
    .detail-card {
        background: white;
        border-radius: 16px;
        padding: 1.5rem;
        margin: 1rem 0;
        box-shadow: 0 4px 16px rgba(0,0,0,0.06);
        border: 1px solid rgba(226, 232, 240, 0.8);
        transition: all 0.3s ease;
    }
    
    .detail-card:hover {
        box-shadow: 0 8px 24px rgba(0,0,0,0.1);
        transform: translateX(4px);
    }
    
    /* Metric Cards */
    .metric-card {
        background: linear-gradient(135deg, rgba(15, 158, 213, 0.08) 0%, rgba(60, 30, 186, 0.08) 100%);
        border-radius: 12px;
        padding: 1.25rem;
        margin: 0.75rem 0;
        border-left: 3px solid var(--grad-cyan);
        transition: all 0.3s ease;
    }
    
    .metric-card:hover {
        border-left-width: 5px;
        transform: translateX(4px);
        box-shadow: 0 4px 16px rgba(15, 158, 213, 0.15);
    }
    
    /* Status Badges */
    .status-badge {
        display: inline-block;
        padding: 0.5rem 1rem;
        border-radius: 20px;
        font-weight: 600;
        font-size: 0.85rem;
        margin: 0.25rem;
        transition: all 0.3s ease;
    }
    
    .status-badge:hover {
        transform: scale(1.05);
        box-shadow: 0 4px 12px rgba(0,0,0,0.1);
    }
    
    .badge-success {
        background: linear-gradient(135deg, #d1fae5 0%, #a7f3d0 100%);
        color: #065f46;
    }
    
    .badge-warning {
        background: linear-gradient(135deg, #fef3c7 0%, #fde68a 100%);
        color: #92400e;
    }
    
    .badge-danger {
        background: linear-gradient(135deg, #fef2f2 0%, #fecaca 100%);
        color: #991b1b;
    }
    
    .badge-info {
        background: linear-gradient(135deg, #dbeafe 0%, #bfdbfe 100%);
        color: #1e40af;
    }
    
    /* Progress Indicators */
    .progress-ring {
        display: inline-block;
        width: 60px;
        height: 60px;
        border-radius: 50%;
        background: linear-gradient(135deg, var(--grad-cyan), var(--grad-indigo));
        padding: 4px;
        animation: rotate 2s linear infinite;
    }
    
    @keyframes rotate {
        from { transform: rotate(0deg); }
        to { transform: rotate(360deg); }
    }
    
    /* Tabs */
    .stTabs [data-baseweb="tab-list"] {
        gap: 2rem;
    }
    
    .stTabs [data-baseweb="tab"] {
        padding: 1rem 2rem;
        font-weight: 600;
    }

    /* Page loading animation (fixed full-screen overlay) */
    .apix-loader-wrap {
        position: fixed;
        inset: 0;
        z-index: 99999;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        gap: 1.1rem;
        background: rgba(255, 255, 255, 0.78);
        backdrop-filter: blur(3px);
        -webkit-backdrop-filter: blur(3px);
        animation: modalFadeIn 0.2s ease;
    }
    .apix-loader-ring {
        width: 64px;
        height: 64px;
        border-radius: 50%;
        border: 5px solid rgba(15, 158, 213, 0.15);
        border-top-color: var(--grad-cyan);
        border-right-color: var(--grad-indigo);
        animation: rotate 0.9s linear infinite;
    }
    .apix-loader-text {
        font-weight: 600;
        font-size: 0.95rem;
        letter-spacing: 0.05em;
        background: linear-gradient(90deg, var(--grad-cyan), var(--grad-indigo), var(--grad-magenta));
        -webkit-background-clip: text;
        background-clip: text;
        -webkit-text-fill-color: transparent;
        animation: subtlePulse 1.4s ease-in-out infinite;
    }
    </style>
    """, unsafe_allow_html=True)


@contextmanager
def page_loader(message: str = "Loading…"):
    """Show a branded loading animation while a page/section renders.

    Usage::

        with page_loader("Loading report…"):
            render_individual_report(...)
    """
    placeholder = st.empty()
    placeholder.markdown(
        f'<div class="apix-loader-wrap">'
        f'<div class="apix-loader-ring"></div>'
        f'<div class="apix-loader-text">{message}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )
    try:
        yield
    finally:
        placeholder.empty()


def render_footer():
    """Render a full-width white footer at the bottom of the page."""
    st.markdown(
        """
        <style>
        .apix-footer {
            width: 100%;
            background: #ffffff;
            border-top: 1px solid #e2e8f0;
            padding: 1rem 0;
            text-align: center;
            margin-top: 3rem;
        }
        .apix-footer p {
            margin: 0;
            font-size: 0.82rem;
            color: #94a3b8;
            font-weight: 500;
            letter-spacing: 0.02em;
        }
        </style>
        <div class="apix-footer">
            <p>\u00a9 2026 Afni, Inc. All rights reserved.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
