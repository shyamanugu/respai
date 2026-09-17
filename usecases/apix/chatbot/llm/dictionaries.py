"""kpi_dictionary.py — Canonical KPI data dictionary (single source of truth).

This module is the *one* authoritative definition of every KPI / metric /
behavior score in the employee-analytics domain.  Both the database schema
(:mod:`db_crud`) and the natural-language → SQL generator
(:mod:`chatbot.sql.sqlite`) derive their column lists and grounding
glossary from this map so the two never drift apart.

``KPI_MEANING_MAP`` is the verbatim business dictionary.  Everything below it
is *derived* from that map:

    KPI_KEYS, WCC_KPI_KEYS, BEHAVIOR_SCORE_KEYS, CALL_HANDLING_KEYS,
    WCC_BEHAVIOR_KEYS, COMPARISON_KEYS, NEW_PROSPECT_KEYS  — column stems
    BEHAVIOR_KEY_MAP / CALL_HANDLING_KEY_MAP / COMPARISON_KEY_MAP
                                                            — JSON-key → column
    COLUMN_MEANINGS        — actual prefixed DB column → plain-English meaning
    glossary_text()        — compact glossary string for the SQL-gen prompt
    meaning_for(column)    — single-column lookup (for per-metric hints)
"""

from __future__ import annotations

# ═══════════════════════════════════════════════════════════════════════════
# CANONICAL BUSINESS DICTIONARY (verbatim — do not paraphrase)
# ═══════════════════════════════════════════════════════════════════════════
KPI_MEANING_MAP: dict[str, dict[str, str]] = {
    "root_level_fields": {
        "date_utc": "Date for which the performance data is recorded",
        "employeeId": "Unique identifier of the employee",
        "employeeName": "Name of the employee",
        "totalCallCount": "Total number of calls handled during the period",
        "programName": "Program or business line the agent belongs to",
        "period": "Time granularity of the data (e.g., weekly)",
        "summary": "LLM-generated overall performance summary",
        "overall_behavior_score": "Aggregated behavioral performance score across all soft skills",
    },
    "kpis": {
        "new_line_pitches": "Number of times agent offered a new line to customers",
        "new_line_opportunity_exists": "Number of calls where a new line opportunity was identified",
        "new_line_opportunity_missed": "Number of times agent failed to pitch despite opportunity",
        "upgrade_attempts": "Number of attempts made to upgrade customer plans/devices",
        "upgrade_opportunity_exists": "Number of calls where upgrade opportunity existed",
        "upgrade_opportunity_missed": "Missed upgrade opportunities where agent did not act",
        "save_attempts": "Attempts made to retain a customer at risk of leaving",
        "fwa_attempts": "Attempts made to offer Fixed Wireless Access products",
        "mobile_protection_attempts": "Attempts made to sell mobile/device protection",
        "we_got_you_utterances": "Count of reassurance phrases used to build trust ('we've got you')",
        "escalations": "Number of calls escalated to supervisors",
        "customer_experience": "Customer satisfaction score derived from interaction quality",
    },
    "wcc_kpis": {
        "resolution_opportunity_exists": "Calls where an issue could have been resolved by the agent",
        "resolution_attempted": "Calls where the agent attempted to resolve the issue",
        "resolution_actual": "Calls where the issue was successfully resolved",
        "save_opportunity_exists": "Calls where customer retention opportunity was present",
        "save_attempted": "Agent attempts to retain the customer",
        "save_actual": "Successful retention of the customer",
        "sale_opportunity_exists": "Calls where a sales opportunity was identified",
        "sale_attempted": "Agent attempted to make a sale",
        "sale_actual": "Successful completion of a sale",
        "resolution_attempted_rate": "Percentage of resolution opportunities where attempts were made",
        "resolution_actual_rate": "Percentage of cases successfully resolved",
        "save_attempted_rate": "Percentage of retention opportunities acted upon",
        "save_actual_rate": "Percentage of successful saves out of attempts",
        "sale_attempted_rate": "Percentage of sales opportunities acted upon",
        "sale_actual_rate": "Percentage of successful sales conversions",
    },
    "pso_kpis": {
        # PSO (VZ Mobile service) customer-care KPIs. Scored 0-100 unless the
        # name ends in "_risk" (risk KPIs are inverse — lower is better).
        "predicted_csat": "Predicted customer satisfaction (CSAT) score",
        "customer_confidence": "How confident the customer felt in the resolution",
        "customer_effort": "Ease of the customer's experience (lower effort is better)",
        "fcr_likelihood": "Likelihood the issue was resolved on first contact (FCR)",
        "resolution_completeness": "How completely the customer's issue was resolved",
        "resolution_confidence": "Confidence that the issue was fully resolved",
        "next_steps_clarity": "Clarity of the next steps communicated to the customer",
        "issue_resolution_effectiveness": "Overall effectiveness of resolving the issue",
        "quality": "Overall call quality score",
        "compliance": "Adherence to compliance and policy requirements",
        "process_adherence": "Adherence to the defined call-handling process",
        "issue_ownership": "Degree to which the agent took ownership of the issue",
        "escalation_handling": "Effectiveness in handling or avoiding escalations",
        "transfer_avoidance": "Ability to resolve without transferring the customer",
        "case_management": "Effectiveness of managing the case end to end",
        "aht_efficiency": "Average-handle-time efficiency",
        "contact_handling_efficiency": "Overall efficiency in handling the contact",
        "hold_management": "Effective use and management of hold time",
        "repeat_contact_risk": "Risk the customer contacts again for the same issue (lower is better)",
        "escalation_risk": "Risk the contact escalates (lower is better)",
        "callback_risk": "Risk the customer needs a callback (lower is better)",
        "reopen_risk": "Risk the case is reopened (lower is better)",
    },
    "trends": {
        "resolution_actual_rate": "Trend of successful issue resolution over time",
        "resolution_attempted_rate": "Trend of effort made to resolve issues",
        "save_actual_rate": "Trend of successful customer retention",
        "save_attempted_rate": "Trend of retention attempts",
        "sale_actual_rate": "Trend of successful sales",
        "sale_attempted_rate": "Trend of sales effort",
        "escalations": "Trend of escalation frequency",
        "customer_experience": "Trend of customer satisfaction over time",
    },
    "behavior_scores": {
        "Active Listening": "Ability to accurately understand customer concerns",
        "Acknowledgment": "Recognizing and validating customer statements",
        "Empathy": "Expressing care and understanding of customer emotions",
        "Confidence": "Delivery with certainty and assurance",
        "Clarity": "Clear and easy-to-understand communication",
        "Needs Discovery": "Effectiveness in identifying customer needs",
        "Solution Guidance": "Ability to guide customer toward resolution",
        "Next Steps Summary": "Clearly outlining next steps before ending the call",
        "Objection Handling": "Handling customer doubts or pushback effectively",
        "Value Positioning": "Explaining benefits of products or solutions",
        "Assumptive Close": "Closing with confidence assuming customer agreement",
        "Compliance Disclosures": "Adherence to regulatory and policy requirements",
        "Call Control": "Ability to control conversation flow",
        "Professional Tone": "Maintaining professionalism throughout the call",
    },
    "call_handling_and_softs_kills": {
        "Comprehension": "Understanding customer queries accurately",
        "Language Proficiency": "Fluency and correctness of language",
        "Emotional Intelligence": "Managing emotions and responding appropriately",
        "Relationship Building": "Establishing rapport and trust",
        "Professional Skills": "General workplace communication effectiveness",
        "Subject Matter Expertise": "Knowledge of products, services, and processes",
    },
    "wcc_behavior_scores": {
        "greeting_connection": "How well the agent initiates and connects at the start",
        "build_connection_active_listening": "Listening attentively to build rapport",
        "build_connection_empathize": "Using empathy to connect with the customer",
        "build_connection_ownership": "Taking responsibility for resolving the issue",
        "set_next_steps_review_for_save_opportunities": "Reviewing retention opportunities during next steps",
        "gather_information_use_tools_resources": "Using tools effectively to gather information",
        "gather_information_ask_questions_to_understand": "Asking questions to clarify needs",
        "gather_information_uncover_needs": "Identifying underlying customer needs",
        "gather_information_watch_out_for_bells_of_churn": "Identifying churn/risk signals",
        "address_initial_needs_present_solution_gain_agreement": "Presenting solution and getting agreement",
        "test_for_resolution_ensure_successful_before_moving_forward": "Ensuring issue is resolved before closing",
        "sell_transition_statements": "Smooth transition into sales discussion",
        "add_value_position_additional_solutions": "Suggesting additional relevant solutions",
        "overcome_objections_reframe_resolve_use_empathy_benefit_focused_responses": "Handling objections effectively",
        "sso_enablement": "Supporting single sign-on or access setup",
        "set_up_for_success_summarize_recap": "Summarizing conversation and outcomes",
        "set_up_for_success_set_clear_expectations_next_steps": "Setting clear expectations",
        "ask_additional_concerns_after_resolving_main_concern": "Checking for additional needs",
        "nps_survey_spiel": "Inviting customer to give feedback via survey",
        "closing_restate_commitment_appreciation": "Closing with recap, commitment, and appreciation",
        "build_connection": "Overall effectiveness in building rapport",
        "gather_information": "Overall effectiveness in understanding the problem",
        "set_up_for_success": "Overall readiness for next steps and closure",
    },
    "new_prospect": {
        "total": "Total number of new potential customers identified",
        "converted": "Number of prospects converted to customers",
        "conversion_rate": "Percentage of prospects converted",
    },
    "comparison": {
        "Resolution Rate": "Comparison of agent vs team resolution effectiveness",
        "Save Rate": "Comparison of retention performance",
        "Sale Rate": "Comparison of sales conversion performance",
        "Escalations": "Comparison of escalation frequency",
        "Customer Experience": "Comparison of satisfaction scores",
    },
    "escalations": {
        "date_utc": "Date of escalation",
        "contact_id": "Unique interaction identifier",
        "summary": "Reason for escalation",
        "tags": "Categorization of escalation",
        "segment_ids": "Call segment references",
        "transcript_excerpt": "Conversation snippet leading to escalation",
    },
    "coaching_tips": {
        "tip": "Actionable recommendation for agent improvement",
        "priority": "Importance level of the tip",
        "examples": "Sample call scenarios where improvement is needed",
        "expected_impact": "Expected outcome after applying the recommendation",
    },
    "key_improvements": {
        "item": "High-level improvement focus area for the agent",
    },
    "customer_experience": {
        "Poor": "Interactions with negative customer sentiment",
    },
    "sales_outcome": {
        "Closed deal": "Successful sales completed",
        "Not closed": "Sales opportunities that did not convert",
        "In progress but not closed": "Ongoing sales discussions",
        "Not applicable": "Calls where sales was not relevant",
    },
}


# ═══════════════════════════════════════════════════════════════════════════
# DERIVATION HELPERS
# ═══════════════════════════════════════════════════════════════════════════
def normalize_key(key: str) -> str:
    """Convert a 'Title Case' or mixed JSON key to snake_case column stem."""
    return key.strip().lower().replace(" ", "_").replace("-", "_")


# Column-name prefixes used by the flat wide-table schema.
PREFIX = {
    "behavior": "bs_",       # behavior_scores      → scores.bs_*
    "call_handling": "ch_",  # soft skills          → scores.ch_*
    "wcc_kpi": "wkpi_",      # wcc_kpis             → wcc_metrics.wkpi_*
    "wcc_behavior": "wbs_",  # wcc_behavior_scores  → wcc_metrics.wbs_*
    "pso_kpi": "pkpi_",      # pso_kpis             → pso_metrics.pkpi_*
    "comparison": "cmp_",    # comparison           → comparison.cmp_*
    "new_prospect": "np_",   # new_prospect         → kpis.np_*
}


# ── Column stems (snake_case, no prefix) ────────────────────────────────────
KPI_KEYS: list[str] = list(KPI_MEANING_MAP["kpis"].keys())
WCC_KPI_KEYS: list[str] = list(KPI_MEANING_MAP["wcc_kpis"].keys())
PSO_KPI_KEYS: list[str] = list(KPI_MEANING_MAP["pso_kpis"].keys())
BEHAVIOR_SCORE_KEYS: list[str] = [normalize_key(k) for k in KPI_MEANING_MAP["behavior_scores"]]
CALL_HANDLING_KEYS: list[str] = [normalize_key(k) for k in KPI_MEANING_MAP["call_handling_and_softs_kills"]]
WCC_BEHAVIOR_KEYS: list[str] = list(KPI_MEANING_MAP["wcc_behavior_scores"].keys())
COMPARISON_KEYS: list[str] = [normalize_key(k) for k in KPI_MEANING_MAP["comparison"]]
NEW_PROSPECT_KEYS: list[str] = list(KPI_MEANING_MAP["new_prospect"].keys())

# ── JSON-key (mixed case) → normalized column stem (for ingestion) ──────────
BEHAVIOR_KEY_MAP: dict[str, str] = {k: normalize_key(k) for k in KPI_MEANING_MAP["behavior_scores"]}
CALL_HANDLING_KEY_MAP: dict[str, str] = {k: normalize_key(k) for k in KPI_MEANING_MAP["call_handling_and_softs_kills"]}
COMPARISON_KEY_MAP: dict[str, str] = {k: normalize_key(k) for k in KPI_MEANING_MAP["comparison"]}


# ═══════════════════════════════════════════════════════════════════════════
# DB COLUMN → MEANING  (actual prefixed columns used in generated SQL)
# ═══════════════════════════════════════════════════════════════════════════
def _build_column_meanings() -> dict[str, str]:
    m: dict[str, str] = {}

    # Root-level queryable columns
    root = KPI_MEANING_MAP["root_level_fields"]
    m["total_call_count"] = root["totalCallCount"]
    m["program_name"] = root["programName"]
    m["overall_behavior_score"] = root["overall_behavior_score"]
    m["summary"] = root["summary"]

    # KPIs (bare columns on the kpis table)
    for col, meaning in KPI_MEANING_MAP["kpis"].items():
        m[col] = meaning

    # New prospect (np_ prefix on kpis table)
    np = KPI_MEANING_MAP["new_prospect"]
    m["np_total"] = np["total"]
    m["np_converted"] = np["converted"]
    m["np_conversion_rate"] = np["conversion_rate"]

    # Behavior scores (bs_ on scores table)
    for jkey, meaning in KPI_MEANING_MAP["behavior_scores"].items():
        m[f"{PREFIX['behavior']}{normalize_key(jkey)}"] = meaning

    # Call handling / soft skills (ch_ on scores table)
    for jkey, meaning in KPI_MEANING_MAP["call_handling_and_softs_kills"].items():
        m[f"{PREFIX['call_handling']}{normalize_key(jkey)}"] = meaning

    # WCC KPIs (wkpi_ on wcc_metrics table)
    for col, meaning in KPI_MEANING_MAP["wcc_kpis"].items():
        m[f"{PREFIX['wcc_kpi']}{col}"] = meaning

    # PSO KPIs (pkpi_ on pso_metrics table)
    for col, meaning in KPI_MEANING_MAP["pso_kpis"].items():
        m[f"{PREFIX['pso_kpi']}{col}"] = meaning

    # WCC behavior scores (wbs_ on wcc_metrics table)
    for col, meaning in KPI_MEANING_MAP["wcc_behavior_scores"].items():
        m[f"{PREFIX['wcc_behavior']}{col}"] = meaning

    # Comparison (cmp_ on comparison table — value + _benchmark sibling)
    for jkey, meaning in KPI_MEANING_MAP["comparison"].items():
        col = f"{PREFIX['comparison']}{normalize_key(jkey)}"
        m[col] = meaning
        m[f"{col}_benchmark"] = f"Team-average benchmark for: {meaning}"

    return m


COLUMN_MEANINGS: dict[str, str] = _build_column_meanings()


def meaning_for(column: str) -> str | None:
    """Return the plain-English meaning of a DB column (ignoring _delta suffix)."""
    if column in COLUMN_MEANINGS:
        return COLUMN_MEANINGS[column]
    if column.endswith("_delta"):
        base = column[:-6]
        base_meaning = COLUMN_MEANINGS.get(base)
        if base_meaning:
            return f"Week-over-week change in: {base_meaning}"
    return None


# Columns the NL→SQL generator is allowed to reference, grouped by table.
# (Kept in sync with sql_generator._COMPACT_SCHEMA so the glossary never
# advertises a column that is not actually queryable.)
_GLOSSARY_TABLE_COLUMNS: dict[str, list[str]] = {
    "kpis": [
        "total_call_count", "overall_behavior_score", *KPI_KEYS,
        "np_total", "np_converted", "np_conversion_rate",
    ],
    "scores": [
        *[f"{PREFIX['behavior']}{k}" for k in BEHAVIOR_SCORE_KEYS],
        *[f"{PREFIX['call_handling']}{k}" for k in CALL_HANDLING_KEYS],
    ],
    "wcc_metrics": [
        *[f"{PREFIX['wcc_kpi']}{k}" for k in WCC_KPI_KEYS],
        *[f"{PREFIX['wcc_behavior']}{k}" for k in WCC_BEHAVIOR_KEYS],
    ],
    "pso_metrics": [f"{PREFIX['pso_kpi']}{k}" for k in PSO_KPI_KEYS],
    "comparison": [f"{PREFIX['comparison']}{k}" for k in COMPARISON_KEYS],
}


def glossary_text() -> str:
    """Compact 'column — meaning' glossary grouped by table for the SQL prompt.

    Grounds the LLM so it maps a user's wording to the correct column using the
    authoritative business meaning before generating SQL.
    """
    lines: list[str] = []
    for table, cols in _GLOSSARY_TABLE_COLUMNS.items():
        lines.append(f"[{table}]")
        for col in cols:
            meaning = COLUMN_MEANINGS.get(col)
            if meaning:
                lines.append(f"  {col} — {meaning}")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════
# rep_pivoted (Azure SQL) METRICS — key = exact MetricDesc, value = meaning.
# Used to deterministically route NL questions to the Azure SQL data source.
# ═══════════════════════════════════════════════════════════════════════════
import re

from chatbot.core.config import get_logger

log = get_logger(__name__)

REP_PIVOTED_METRICS: dict[str, str] = {
    "AML (Average Message Length)": "Average size or length of messages sent by agents in chats",
    "At Desk Approval": "Approval actions completed by agents while actively handling customer sessions",
    "Agent Outbound AHT": "Average time agents spend on outbound calls",
    "Agent Kryon Utilization": "Percentage of automation tool (Kryon) usage by agent",
    "Prepaid Suspends": "Number of prepaid accounts temporarily disconnected",
    "ACSS AA Utilization %": "Usage % of automated assistant/self-service system",
    "AAL Count": "Number of new lines added to customer accounts",
    "IVR AA Nudges": "Prompts shown to customers in IVR to use automation",
    "Agent Close Rate": "% of conversations successfully closed by agent",
    "7-Day Resolve": "Issues resolved within 7 days",
    "Handoffs": "Transfers of conversations between teams/tools",
    "Convo Chatbot Launched & Used": "Chatbot sessions initiated and actually used by customer",
    "3 Day Contact Disconnect %": "% of customers who disconnected within 3 days",
    "3-Day Resolve": "Issues resolved within 3 days",
    "60 Day Contact Disconnect %": "% of customers who disconnected within 60 days",
    "Compass Utilization %": "% usage of Compass (agent assist tool)",
    "Available Hours": "Total time agents are available for handling work",
    "OCC/Contact": "Value generated per customer contact",
    "Compass Accepted %": "% of Compass suggestions accepted by agents",
    "App Engagement 3-Day": "Customer app activity within 3 days",
    "Gross Adds - Phone (Finance CS)": "Number of phones added (financial reporting)",
    "Return to Queue": "Cases sent back to queue for reassignment",
    "Agent Avg. Call Work": "Average after-call work time",
    "My Offers Declined %": "% of offers rejected by customers",
    "ARPC (Agent response per conversation)": "Number of responses per conversation",
    "B2Q Queue (Sec)": "Time to send request back to queue",
    "2-Hour Resolve": "Issues resolved within 2 hours",
    "My Offers Provisions": "Offers successfully provisioned/activated",
    "AQT - Skill (Sec)": "Wait time before agent picks task (skill-based)",
    "AQT - B2Q (Sec)": "Wait time after returning to queue",
    "Avg Assignment Duration (Min)": "Average time assignment stays with an agent",
    "1-Day Resolve": "Issues resolved within 1 day",
    "CCPAH": "Cases closed per available hour",
    "Gross Adds Connected Device Count": "Devices (watch/tablet) added",
    "Consumer Closed": "Customer cases closed",
    "FRPAH": "First resolution per available hour",
    "Engaged Conversations %": "% of conversations with active interaction",
    "SPPA Utilization": "Usage of service plan/promo assistance",
    "Thumbs Up %": "% of positive feedback",
    "Net OCC Amount": "Net revenue/value per contact",
    "% Assistant Clicks": "% of times assistants/tools were clicked",
    "My Offers Opportunities": "Total offer opportunities presented",
    "Compass Not Discussed %": "Suggestions not used or mentioned",
    "Approved OCCs": "Approved value-generating actions",
    "Gross Adds Phone Count": "Number of phone additions",
    "ATTFA (Sec)": "Average time to first action",
    "Inconvenience Credit Count %": "% of credit issued for inconvenience",
    "CM Process Sat": "Satisfaction with case management process",
    "Gross Adds - AAL (Finance CS)": "Add-a-line count (financial view)",
    "CM Rep Sat": "Satisfaction with agent handling",
    "5-Day Resolve": "Issues resolved in 5 days",
    "Agent Avg. Talk": "Average talk time",
    "31-Day Resolve": "Issues resolved in 31 days",
    "Agent Outbound Calls": "Total outbound calls made",
    "Assignment Duration (Min)": "Time spent on an assignment",
    "1 Day Contact Disconnect %": "Customers leaving within 1 day",
    "30-Day Resolve": "Issues resolved within 30 days",
    "VPC": "Value per customer",
    "CFT 2.0": "Updated customer friction tracking metric",
    "App Engagement 1-Day": "App usage within 1 day",
    "CEPAH": "Customer experience per available hour",
    "Gross Adds - Connected Devices (Finance CS)": "Device additions financial metric",
    "Agent Outbound %": "% of outbound interactions",
    "My Offers Customer Interactions": "Interactions involving offers",
    "Total Negative Value Amount": "Total losses/credits issued",
    "High Priority Upgrade %": "% of priority upgrades completed",
    "Same Day Contact Disconnect %": "Customers leaving same day",
    "Gross Adds - Total (Finance CS)": "Total additions across all products",
    "EPAH": "Earnings/performance per available hour",
    "90 Day Contact Disconnect %": "Customers leaving within 90 days",
    "Net Feature Amount": "Revenue from features added/removed",
    "Fios Orders Installed": "Completed Fios installations",
    "Prepaid OCC BundleSync Amount": "Value from prepaid bundle synchronization",
    "Handoffs w/ TRG": "Transfers involving technical resolution group",
    "CSPAH": "Customer satisfaction per available hour",
    "My Offers Provisional Opportunities": "Temporary offer opportunities created",
    "Gross Adds FWA Count": "Fixed wireless additions",
    "Troubleshooting Tab Utilization Pct": "% usage of troubleshooting tab",
    "Avg Response Time": "Average response delay",
    "Credit Average": "Average credit amount issued",
    "Gross Adds - FWA (Finance CS)": "FWA financial additions",
    "Engaged Conversations": "Conversations with active agent/customer participation",
    "Conversations Closed": "Total closed chats/calls",
    "Troubleshooting Tab Clicked Pct": "% of sessions where troubleshooting tab clicked",
    "Fios Referrals Submitted": "Fios referrals generated",
    "Handoffs Count": "Total number of transfers",
    "Total Perk Adds (Line Lvl)": "Perks added at line level",
    "Troubleshooting Tab Guided Flow Pct": "% using guided troubleshooting",
    "30 Day Contact Disconnect %": "Customers leaving within 30 days",
    "Time To First Assignment (Sec)": "Time taken to assign task initially",
    "IVR AA Suggestions": "Automated suggestions in IVR",
    "B2Q Handoff Volume": "Number of cases returned to queue",
    "My Offers Take Rate": "% of offers accepted vs shown",
    "Troubleshooting Tab Guided Flow Count": "Count of guided flows used",
    "Perk Add Count (Acct Lvl)": "Perks added at account level",
    "Credit Frequency": "How often credits are issued",
    "Inconvenience Credit Amount %": "% of amount given as credit",
    "Compass Declined %": "Suggestions rejected by agent",
    "Real Time Agent DPC": "Real-time decisions per contact",
    "Net Upgrades": "Total upgrades after cancellations",
    "Agent Calls": "Total calls handled",
    "AFRRT (Avg First Rep Response Time)": "Time for first agent reply",
    "Total Handoff Rate (Skill)": "% transferred within same skill group",
    "Credit Interaction": "Interactions involving credits",
    "IVR AA Searches": "Searches done in automation/IVR",
    "Troubleshooting Tab Utilized Count": "Count of troubleshooting uses",
    "Negative Value Per Contact": "Loss per interaction",
    "NVPH": "Negative value per hour",
    "High Priority Upgrade Count": "Number of priority upgrades",
    "Agent Avg. Hold": "Average hold time",
    "NRPC": "Net responses per conversation",
    "Net Handoff Count": "Transfers minus returns",
    "My Offers Accepts": "Number of accepted offers",
    "Total Plan Step Ratio (Finance)": "Ratio of upgrades vs downgrades",
    "System Closed": "Cases auto-closed by system",
    "IPC (Interactions Per Conversation)": "Avg interactions per conversation",
    "View Together Attach and Transact": "Using co-browse to complete action",
    "Total Handoffs": "Total transfers",
    "Net Handoff Rate": "Final transfer rate after adjustments",
    "Total Interactions": "Total chat/call messages",
    "Thumbs Down %": "Negative feedback rate",
    "myPlan Conversion Rate": "% converting to new plan",
    "VPH": "Value per hour",
    "Login Hours": "Total logged-in time",
    "My Offers Accept Rate": "% of offers accepted",
    "ACPAH (Agent Closed Per Available Hour)": "Closures per available hour",
    "View Together Utilized": "Co-browse usage count",
    "IVR AA Utilization (+SP) %": "Automation usage including service plan",
    "Net Value Amount": "Revenue after adjustments",
    "Warm Transfer Referrals Submitted": "Referrals made during transfer",
    "Plan Up Count": "Number of plan upgrades",
    "My Offers Accepted %": "% of offers accepted",
    "Conversations Started": "Total initiated conversations",
    "Online Hours": "Active working hours",
    "IVR AA Utilization %": "Automation usage rate",
    "Avg Rep Response": "Average agent reply time",
    "Fios Orders Submitted": "Fios orders placed",
    "IVR AA Utilization (+SP+SN) %": "Automation usage incl. add-ons",
    "Skill Handoff Volume": "Transfers within skill groups",
    "Troubleshooting Tab Effectiveness Count": "Successful resolutions via tool",
    "VXS Overall Rep": "Overall experience score",
    "Plan Down Count": "Plan downgrades",
    "Troubleshooting Tab Clicked Count": "Total clicks",
    "VXS VZ Sat": "Verizon satisfaction score",
    "My Offers Utilization Rate": "% usage of offers feature",
    "myPlan Upgrade Rate": "% of plan upgrades",
    "Agent AHT": "Avg handling time",
    "Total Handoff Rate": "% conversations transferred",
    "View Together Attached": "Co-browse attachment usage",
    "Total Rep Msg Cnt": "Total agent messages",
    "High Priority Upgrade Eligible": "Customers eligible for upgrade",
    "Total Conversations": "Total conversations handled",
    "My Offers Clicks": "Number of offer clicks",
    "Total Value Amount": "Total revenue generated",
    "Not Engaged Conversations": "Conversations without interaction",
    "NVPC": "Negative value per conversation",
    "Total Plan Step Ups (Finance)": "Financial count of upgrades",
    "My Offers Not Discussed %": "Offers not talked about",
    "Warm Transfer Closed Orders": "Orders closed after transfer",
    "Prepaid Data Adjustment Units": "Adjustments made to prepaid data",
    "Prepaid Disconnects": "Prepaid accounts terminated",
    "Tech Handoffs": "Transfers to technical teams",
    "Troubleshooting Tab Effectiveness Pct": "Success rate of troubleshooting",
    "VXS Survey Count": "Total surveys completed",
}

_PAREN_RE = re.compile(r"\(([^)]*)\)")
_WS_RE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    """Lower-case and collapse whitespace; map ``&``/``%`` to words."""
    t = text.lower()
    t = t.replace("&", " and ").replace("%", " percent ")
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    return _WS_RE.sub(" ", t).strip()


def _candidate_phrases(metric_desc: str) -> list[str]:
    """Return the distinctive phrases used to recognise *metric_desc* in text."""
    phrases: set[str] = set()
    for inner in _PAREN_RE.findall(metric_desc):
        norm = _normalize(inner)
        if len(norm) >= 4:
            phrases.add(norm)
    base = _PAREN_RE.sub("", metric_desc).strip()
    norm_base = _normalize(base)
    if norm_base:
        phrases.add(norm_base)
    return [p for p in phrases if p]


# phrase → (metric_desc, is_short_token)
_INDEX: list[tuple[str, str, bool]] = []
for _desc in REP_PIVOTED_METRICS:
    for _phrase in _candidate_phrases(_desc):
        _is_short = (" " not in _phrase) and (len(_phrase) <= 5)
        _INDEX.append((_phrase, _desc, _is_short))

# Hand-curated synonyms for phrasings the auto-derived phrases miss — most
# notably "resolution" (users' word) vs "Resolve" (the exact MetricDesc). These
# ensure time-boxed resolution questions route to the Azure rep_pivoted source.
_REP_SYNONYMS: dict[str, str] = {
    "2 hour resolution": "2-Hour Resolve",
    "two hour resolution": "2-Hour Resolve",
    "2hr resolution": "2-Hour Resolve",
    "2 hr resolution": "2-Hour Resolve",
    "2hr": "2-Hour Resolve",
    "1 day resolution": "1-Day Resolve",
    "one day resolution": "1-Day Resolve",
    "3 day resolution": "3-Day Resolve",
    "three day resolution": "3-Day Resolve",
    "5 day resolution": "5-Day Resolve",
    "five day resolution": "5-Day Resolve",
    "7 day resolution": "7-Day Resolve",
    "seven day resolution": "7-Day Resolve",
    "7dr": "7-Day Resolve",
    "31 day resolution": "31-Day Resolve",
}
for _syn, _desc in _REP_SYNONYMS.items():
    if _desc in REP_PIVOTED_METRICS:
        _norm_syn = _normalize(_syn)
        _INDEX.append((_norm_syn, _desc, (" " not in _norm_syn) and (len(_norm_syn) <= 5)))

# Longest phrases first so the most specific match wins.
_INDEX.sort(key=lambda t: len(t[0]), reverse=True)

log.info("rep_metrics → indexed %d phrases for %d metrics",
         len(_INDEX), len(REP_PIVOTED_METRICS))


def match_rep_metrics(question: str) -> list[dict]:
    """Return rep_pivoted metrics referenced in *question*, best match first.

    Each result dict has ``metric_desc`` (exact DB value), ``meaning`` and
    ``score`` (length of the matched phrase). An empty list means the question
    does not reference any rep_pivoted metric.
    """
    norm_q = _normalize(question)
    padded = f" {norm_q} "

    matched: list[dict] = []
    seen: set[str] = set()
    for phrase, desc, is_short in _INDEX:
        if desc in seen:
            continue
        if is_short:
            hit = f" {phrase} " in padded
        else:
            hit = phrase in norm_q
        if hit:
            seen.add(desc)
            matched.append({
                "metric_desc": desc,
                "meaning": REP_PIVOTED_METRICS[desc],
                "score": len(phrase),
            })

    matched.sort(key=lambda m: m["score"], reverse=True)
    if matched:
        log.info("match_rep_metrics → %d match(es) for '%s' (top='%s')",
                 len(matched), question[:80], matched[0]["metric_desc"])
    return matched


def best_match_score(question: str) -> int:
    """Return the score of the strongest rep_pivoted match (0 if none)."""
    matches = match_rep_metrics(question)
    return matches[0]["score"] if matches else 0
