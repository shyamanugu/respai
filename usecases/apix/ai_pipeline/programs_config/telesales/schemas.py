"""Pydantic evaluation schema for VZW Telesales.

These are the exact original models used in production.
Field descriptions serve as the schema contract — the LLM reads them
via structured output.
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


# 1. Coaching Tip Model
class CoachingTip(BaseModel):
    tip: str  # Succinct coaching tip
    explanation: str  # Justification for the coaching tip, referencing segment IDs
    segment_ids: List[int]  # List of segment IDs referencing the relevant segments


# 2. Customer Experience Model
class CustomerExperience(BaseModel):
    rating: Literal['Good', 'Medium', 'Poor']  # Customer experience rating
    justification: str  # Explanation justifying the rating
    segment_ids: List[int]  # List of segment IDs referenced to support the rating


# 3. Sales Outcome Model
class SalesOutcome(BaseModel):
    outcome: Literal['Closed deal', 'Not closed', 'In progress but not closed', "Not applicable"]  # Sales outcome of the call
    explanation: str  # Reason why this outcome was achieved or not achieved
    segment_ids: List[int]  # Segment IDs referencing parts of the call that contributed to the outcome


# 4. Escalation Model
class Escalation(BaseModel):
    escalation_requested_by_customer: bool  # Whether the customer asked for an escalation
    escalation_due_to_frustration: bool  # Whether escalation was due to frustration
    escalation_reason: Optional[str]  # Reason for escalation (if any)
    segment_ids: List[int]  # Relevant segments that show escalation


# 5. Sales Agent Evaluation Model
class SalesAgentEvaluation(BaseModel):
    # Call completion
    call_completed: bool = Field(description="True if the call reached a natural conclusion.")
    call_dropped_by_customer: bool = Field(description="True if the call was disconnected by the customer.")
    call_transfer: bool = Field(description="True if the call was transferred.")

    # Sales behavior
    pitched_new_line: bool = Field(description="True if the agent actively pitched a new product or line during the conversation.")
    new_line_opportunity_exists: bool = Field(description="True if there was a valid opportunity to pitch a new product or line.")
    new_line_opportunity_missed: bool = Field(
        description=(
            "True if the agent missed pitching a new product/line when a new line opportunity existed. "
            "Always False if no new line opportunity existed."
        )
    )

    pitched_plan_upgrade: bool = Field(
        description="True if the agent presented a plan upgrade to the customer."
    )
    upgrade_opportunity_exists: bool = Field(
        description="True if there was a valid opportunity to offer a plan upgrade."
    )
    upgrade_opportunity_missed: bool = Field(
        description=(
            "True if the agent did not pitch a plan upgrade when an upgrade opportunity existed. "
            "Always False if no upgrade opportunity existed."
        )
    )

    successful_upsell: bool = Field(
        description="True if the agent successfully sold an additional product or upgrade."
    )
    customer_declined_upsell: bool = Field(
        description="True if the customer rejected an upsell offer."
    )

    disclosure_read: bool = Field(description="True if the agent read required disclosures.")

    # Granular sales behavior
    new_line_pitch_positioning: Optional[str] = Field(description="How the new line was pitched.")
    upgrade_pitch_positioning: Optional[str] = Field(description="How the upgrade was pitched.")
    upgrade_plan_type: Optional[str] = Field(description="The type of plan offered.")
    pitched_fwa: bool = Field(description="True if Fixed Wireless Access (FWA) was pitched.")
    pitched_mobile_protection: bool = Field(description="True if mobile protection was pitched.")
    save_attempt: bool = Field(description="True if the agent attempted to save the customer.")
    weve_got_you_statement: Optional[str] = Field(description="If 'We've Got You' was used, include exact phrasing.")

    # Escalation
    escalation: Optional[Escalation]  # Escalation details

    # Call outcome
    sale_made: bool = Field(description="True if a sale was made.")
    customer_intent: str = Field(description="The reason for the customer's call.")
    issue_resolution_steps: Optional[str] = Field(description="Key steps taken by the agent to resolve the issue.")
    issue_resolved: bool = Field(description="True if the issue was resolved.")
    tagging: List[str] = Field(description="List of tags that apply to the call.")

    # Customer sentiment
    customer_frustration_start: int = Field(ge=1, le=10, description="Customer's frustration at the start.")
    customer_frustration_end: int = Field(ge=1, le=10, description="Customer's frustration at the end.")

    # Feedback
    coaching_tip: Optional[CoachingTip] = None  # Coaching tip
    sales_tip: Optional[str] = Field(description="The actionable sales tip.")
    other_feedback: Optional[str] = Field(description="Any additional feedback.")

    # Importance
    call_importance: int = Field(ge=1, le=10, description="Rate the call's importance.")

    # Customer experience and sales outcome
    customer_experience: Optional[CustomerExperience] = None
    sales_outcome: Optional[SalesOutcome] = None

    # Tags
    topics: List[str]  # List of tags (e.g., 'new_line_pitch', 'save_attempt')

    # Behavior flags (0/1)
    active_listening:       bool = Field(description="False only if the agent does not actively demonstrate active listening.")
    acknowledgment:         bool = Field(description="False only if the agent does not explicitly acknowledges the customer's statements.")
    empathy:                bool = Field(description="False only if the agent does not show empathy with explicit phrasing.")
    confidence:             bool = Field(description="False only if the agent does not exhibit confident guidance.")

    clarity:                bool = Field(description="False only if explanations/instructions are clear and explicit.")
    needs_discovery:        bool = Field(description="False only if the agent does not probe to understand needs.")
    solution_guidance:      bool = Field(description="False only if the agent does not guide the customer to a solution.")
    next_steps_summary:     bool = Field(description="False only if the agent does not summarize next steps.")

    objection_handling:     Optional[bool] = Field(description="False only if a customer objection is handled explicitly. None if not applicable.")
    value_positioning:      Optional[bool] = Field(description="False only if value/benefits are positioned clearly. None if not applicable.")
    assumptive_close:       Optional[bool] = Field(description="False only if the agent does not use assumptive closing language. None if not applicable.")
    compliance_disclosures: Optional[bool] = Field(description="False only if mandatory disclosures are read. None if not applicable.")

    call_control:           bool = Field(description="False only if the agent does not demonstrate call control.")
    professional_tone:      bool = Field(description="False only if a professional tone is consistently NOT evident.")

    # New prospect
    new_prospect:                   bool = Field(description="True if the customer is a new prospect; otherwise False. If unclear, set to False.")
    new_prospect_converted:         bool = Field(description="True if the customer is a new prospect that is converted to a sale; otherwise False. If unclear, set to False.")
    new_prospect_evidence: Optional[str] = Field(description="Exact quote(s) from the transcript (max 20 words) that justify the classification.")

    # Call handling soft skills
    comprehension:          bool = Field(description="False only if the agent did not display the ability to understand and clarify customer's needs and concerns.")
    language_proficiency:   bool = Field(description="False only if the agent's communication was not effective and clear in the customer's language, avoiding jargon and maintaining appropriate speech rate.")
    emotional_intelligence: bool = Field(description="False only if the agent did not recognize, understand, and respond to emotions in themselves and others.")
    relationship_building:  bool = Field(description="False only if the agent did not build rapport and trust with the customer through personalized interactions.")
    professional_skills:    bool = Field(description="False only if the agent did not demonstrate the ability to value customer's time when handling the call.")
    subject_matter_expertise: bool = Field(description="False only if the agent did not demonstrate strong knowledge of products, services, and processes, and correct and precise verbiage to effectively assist the customer.")
