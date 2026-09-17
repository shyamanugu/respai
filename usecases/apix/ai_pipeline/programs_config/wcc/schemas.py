"""Pydantic evaluation schema for VZW WCC (Whole Call Coaching).

WCC uses a dedicated, standalone evaluation model (``WccAgentEvaluation``)
— it is NOT an extension of the telesales ``SalesAgentEvaluation``.

It focuses on customer-resolution, experience, retention and right-of-sell
behaviours across the LEARN / PROVIDE / CLOSE phases, plus the WCC Core KPIs
(Resolution, Survival/Retention, Right of Sell) expressed as
opportunity/actual pairs with supporting segment IDs.

Shared sub-models (Escalation, CustomerExperience, SalesOutcome) are reused
from the telesales schema to avoid duplication.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

from ai_pipeline.programs_config.telesales.schemas import (  # noqa: F401
    CoachingTip,
    CustomerExperience,
    Escalation,
    SalesOutcome,
)


# WCC Agent Evaluation Model
class WccAgentEvaluation(BaseModel):
    # Escalation
    escalation: Optional[Escalation]  # Escalation details

    # Call outcome
    sale_made:              bool = Field(description="True if a sale was made.")
    customer_intent:        str = Field(description="The reason for the customer's call.")
    issue_resolution_steps: Optional[str] = Field(description="Key steps taken by the agent to resolve the issue.")
    issue_resolved:         bool = Field(description="True if the issue was resolved.")
    tagging:                List[str] = Field(description="List of tags that apply to the call.")

    # Customer experience and sales outcome
    customer_experience: Optional[CustomerExperience] = None
    sales_outcome:       Optional[SalesOutcome] = None

    # New prospect
    new_prospect:              bool = Field(description="True if the customer is a new prospect; otherwise False. If unclear, set to False.")
    new_prospect_converted:    bool = Field(description="True if the customer is a new prospect that is converted to a sale; otherwise False. If unclear, set to False.")
    new_prospect_evidence:     Optional[str] = Field(description="Exact quote(s) from the transcript (max 20 words) that justify the classification.")

    # Call handling soft skills
    comprehension:             bool = Field(description="False only if the agent did not display the ability to understand and clarify customer's needs and concerns.")
    language_proficiency:      bool = Field(description="False only if the agent's communication was not effective and clear in the customer's language, avoiding jargon and maintaining appropriate speech rate.")
    emotional_intelligence:    bool = Field(description="False only if the agent did not recognize, understand, and respond to emotions in themselves and others.")
    relationship_building:     bool = Field(description="False only if the agent did not build rapport and trust with the customer through personalized interactions.")
    professional_skills:       bool = Field(description="False only if the agent did not demonstrate the ability to value customer's time when handling the call.")
    subject_matter_expertise:  bool = Field(description="False only if the agent did not demonstrate strong knowledge of products, services, and processes, and correct and precise verbiage to effectively assist the customer.")

    # ── Behavioral KPIs (flat booleans, WCC mode only) ───────────────────────
    # LEARN phase
    wcc_greetings_connection:   bool = Field(description="True if the agent set the tone for trust and support with a warm greeting.")
    wcc_build_connection:       bool = Field(description="True if the agent took ownership and built a genuine connection with the customer.")
    wcc_gather_information:      bool = Field(description="True if the agent watched for warning signs of customer churn during information gathering.")
    wcc_verification:           bool = Field(description="True if the agent properly verified the customer (confirming name and business name) and completed high-level customer authentication.")
    wcc_callback:               bool = Field(description="True if the agent confirmed a call back number in case of disconnection.")

    # PROVIDE phase
    wcc_address_needs:           bool = Field(description="True if the agent addressed the initial needs and gained explicit agreement before moving forward.")
    wcc_test_resolution:         bool = Field(description="True if the agent ensured resolution was successful before moving forward.")
    wcc_sell_transition:         bool = Field(description="True if the sell transition was smooth, contextually linked to the serve solution, and clearly positioned as a value-add.")
    wcc_sell_confidence:         bool = Field(description="True if the agent delivered the sell transition with a positive, confident tone that builds trust and interest.")
    wcc_overcoming_objections:   bool = Field(description="True if the agent reframed and resolved customer objections effectively.")

    # CLOSE phase
    wcc_sso_enablement:          bool = Field(description="True if the agent informed the customer about managing their account through the My Business portal (Company Portal) or MyBiz app (Company App) and offered to walk them through using the app or website.")
    wcc_setup_success:           bool = Field(description="True if the agent summarized the setup for success at the end of the call.")
    wcc_additional_concerns:     bool = Field(description="True if the agent asked for additional concerns after resolving the main issue.")
    wcc_nps_survey:              bool = Field(description="True if the agent encouraged honest feedback and naturally included the NPS survey spiel in the closing.")
    wcc_closing_restate:         bool = Field(description="True if the agent restated commitment and appreciation during closing.")

    # ── Core KPIs (flat booleans, WCC mode only) ─────────────────────────────

    # Resolution
    resolution_opportunity_exists:       bool = Field(description="True if the customer raised at least one distinct core issue or concern that required resolution.")
    resolution_opportunity_segment_ids:  List[int] = Field(description="Segment IDs where the customer's main issue or concern was expressed. Return [] if none.")
    resolution_actual_exists:            bool = Field(description="True if the agent successfully resolved the customer's main issue or provided a clear path to resolution.")
    resolution_actual_segment_ids:       List[int] = Field(description="Segment IDs where the agent successfully resolved the issue. Must be a subset of resolution_opportunity_segment_ids. Return [] if none.")

    # Survival / Retention
    survival_rate_opportunity_exists:      bool = Field(description="True if any churn risk signal appeared (e.g., customer mentions competitor, threatens to cancel, or expresses dissatisfaction).")
    survival_rate_opportunity_segment_ids: List[int] = Field(description="Segment IDs where churn risk signals were present. Return [] if none.")
    survival_rate_actual_exists:           bool = Field(description="True if the agent effectively addressed churn risk using retention actions (e.g., saves, reassurance, value reinforcement).")
    survival_rate_actual_segment_ids:      List[int] = Field(description="Segment IDs where the agent took effective retention actions. Must be a subset of survival_rate_opportunity_segment_ids. Return [] if none.")

    # Right of Sell
    right_of_sell_opportunity_exists:       bool = Field(description="True if there was at least one appropriate moment where the agent had earned enough trust or credibility to introduce a sales offer.")
    right_of_sell_opportunity_segment_ids:  List[int] = Field(description="Segment IDs where right-of-sell opportunities existed. Return [] if none.")
    right_of_sell_actual_exists:            bool = Field(description="True if the agent acted on a right-of-sell opportunity by introducing a relevant sales offer after building credibility.")
    right_of_sell_actual_segment_ids:       List[int] = Field(description="Segment IDs where the agent presented the sales offer. Must be a subset of right_of_sell_opportunity_segment_ids. Return [] if none.")
