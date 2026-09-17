"""Pydantic evaluation schema for PSO — VZ Mobile service (Verizon).

PSO uses a dedicated, standalone evaluation model (``PsoAgentEvaluation``)
— it is NOT an extension of the telesales ``SalesAgentEvaluation`` or the WCC
``WccAgentEvaluation``.

PSO is a Verizon mobile customer-care program. Its evaluation is driven by the
customer-support context and focuses on:

    * Customer Experience   — predicted satisfaction, confidence and effort
    * Resolve               — first-contact resolution, completeness, confidence
    * Quality               — quality, compliance and process adherence
    * Customer Care Handling — ownership, escalation, transfer avoidance, case mgmt
    * Operational Efficiency — AHT, contact-handling and hold management
    * Repeat Contact Risk   — repeat-contact / escalation / callback / reopen risk

Each of the above KPI groups is captured as a True/False judgement (for the
Repeat Contact Risk group True = the risk is present), with supporting
``*_segment_ids`` evidence, and is aggregated as a 0-1 fraction across the
agent's calls in the summary step.

Behavioural KPIs (flat booleans) and the six call-handling soft skills are
aggregated as 0-1 fractions in the summary step.

Shared sub-models (Escalation, CustomerExperience, SalesOutcome, CoachingTip)
are reused from the telesales schema to avoid duplication.
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


# PSO Agent Evaluation Model
class PsoAgentEvaluation(BaseModel):
    # ── Escalation ───────────────────────────────────────────────────────────
    escalation: Optional[Escalation]  # Escalation details

    # ── Call outcome ─────────────────────────────────────────────────────────
    sale_made:              bool = Field(description="True if a sale was made during the interaction.")
    customer_intent:        str = Field(description="The reason for the customer's call.")
    issue_resolution_steps: Optional[str] = Field(description="Key steps taken by the agent to resolve the customer's issue.")
    issue_resolved:         bool = Field(description="True if the customer's issue was fully resolved on this contact.")
    tagging:                List[str] = Field(description="List of tags that apply to the call.")

    # ── Customer experience and sales outcome ────────────────────────────────
    customer_experience: Optional[CustomerExperience] = None
    sales_outcome:       Optional[SalesOutcome] = None

    # ── New prospect ─────────────────────────────────────────────────────────
    new_prospect:           bool = Field(description="True if the customer is a new prospect; otherwise False. If unclear, set to False.")
    new_prospect_converted: bool = Field(description="True if the customer is a new prospect that is converted to a sale; otherwise False. If unclear, set to False.")
    new_prospect_evidence:  Optional[str] = Field(description="Exact quote(s) from the transcript (max 20 words) that justify the classification.")

    # ── Customer sentiment ───────────────────────────────────────────────────
    customer_frustration_start: int = Field(ge=1, le=10, description="Customer's frustration level at the start of the call (1 = calm, 10 = extremely frustrated).")
    customer_frustration_end:   int = Field(ge=1, le=10, description="Customer's frustration level at the end of the call (1 = calm, 10 = extremely frustrated).")
    call_importance:            int = Field(ge=1, le=10, description="Overall importance of the call based on its impact on customer experience, resolution and retention risk (1 = routine, 10 = critical).")

    # ── KPI group evaluations (flat booleans + supporting evidence) ──────────
    # Every KPI is a True/False judgement. For the performance groups True = the
    # positive outcome was met; for the Repeat Contact Risk group True = the risk
    # is present. Each KPI carries a ``*_segment_ids`` list with the transcript
    # segments that justify the judgement (evidence). Return [] when no segment
    # is applicable.

    # Customer Experience
    predicted_csat:                 bool = Field(description="True if the customer was (or likely was) satisfied overall, based on call outcome and sentiment.")
    predicted_csat_segment_ids:     List[int] = Field(description="Segment IDs evidencing the predicted CSAT judgement. Return [] if none.")
    customer_confidence:            bool = Field(description="True if the customer appeared confident the issue was resolved.")
    customer_confidence_segment_ids: List[int] = Field(description="Segment IDs evidencing customer confidence. Return [] if none.")
    customer_effort:                bool = Field(description="True if the resolution process was low-effort / easy for the customer (low CES).")
    customer_effort_segment_ids:    List[int] = Field(description="Segment IDs evidencing customer effort. Return [] if none.")

    # Resolve
    fcr_likelihood:                 bool = Field(description="True if the issue was likely resolved during this first interaction (First-Contact Resolution).")
    fcr_likelihood_segment_ids:     List[int] = Field(description="Segment IDs evidencing the FCR judgement. Return [] if none.")
    resolution_completeness:        bool = Field(description="True if all identified customer concerns were addressed.")
    resolution_completeness_segment_ids: List[int] = Field(description="Segment IDs evidencing resolution completeness. Return [] if none.")
    resolution_confidence:          bool = Field(description="True if the customer can proceed without additional support (issue handling was effective).")
    resolution_confidence_segment_ids: List[int] = Field(description="Segment IDs evidencing resolution confidence. Return [] if none.")
    next_steps_communication:       bool = Field(description="True if the agent provided clear next-step instructions and set expectations.")
    next_steps_communication_segment_ids: List[int] = Field(description="Segment IDs evidencing next-steps communication. Return [] if none.")
    issue_resolution_effectiveness: bool = Field(description="True if the customer's issue was effectively solved (successful case handling).")
    issue_resolution_effectiveness_segment_ids: List[int] = Field(description="Segment IDs evidencing issue-resolution effectiveness. Return [] if none.")

    # Quality
    quality:                        bool = Field(description="True if the overall quality of the interaction was good.")
    quality_segment_ids:            List[int] = Field(description="Segment IDs evidencing the quality judgement. Return [] if none.")
    compliance:                     bool = Field(description="True if the agent adhered to required processes and disclosures (compliant).")
    compliance_segment_ids:         List[int] = Field(description="Segment IDs evidencing compliance. Return [] if none.")
    process_adherence:              bool = Field(description="True if proper troubleshooting and service procedures were followed.")
    process_adherence_segment_ids:  List[int] = Field(description="Segment IDs evidencing process adherence. Return [] if none.")

    # Customer Care Handling
    issue_ownership:                bool = Field(description="True if the agent demonstrated end-to-end ownership of the issue.")
    issue_ownership_segment_ids:    List[int] = Field(description="Segment IDs evidencing issue ownership. Return [] if none.")
    escalation_necessity:           bool = Field(description="True if an escalation was legitimately required for this interaction.")
    escalation_necessity_segment_ids: List[int] = Field(description="Segment IDs evidencing escalation necessity. Return [] if none.")
    transfer_avoidance:             bool = Field(description="True if the issue was (or could have been) resolved without a transfer.")
    transfer_avoidance_segment_ids: List[int] = Field(description="Segment IDs evidencing transfer avoidance. Return [] if none.")
    case_management:                bool = Field(description="True if the issue investigation, documentation and closure activities were handled well.")
    case_management_segment_ids:    List[int] = Field(description="Segment IDs evidencing case management. Return [] if none.")

    # Operational Efficiency
    aht_assessment:                 bool = Field(description="True if the interaction was efficiently paced (appropriate handle time without rushing).")
    aht_assessment_segment_ids:     List[int] = Field(description="Segment IDs evidencing the AHT assessment. Return [] if none.")
    contact_handling_efficiency:    bool = Field(description="True if the agent progressed the conversation efficiently toward resolution.")
    contact_handling_efficiency_segment_ids: List[int] = Field(description="Segment IDs evidencing contact-handling efficiency. Return [] if none.")
    hold_management:                bool = Field(description="True if hold time was used appropriately with clear communication.")
    hold_management_segment_ids:    List[int] = Field(description="Segment IDs evidencing hold management. Return [] if none.")

    # Repeat Contact Risk (True = risk is present)
    repeat_contact_risk:            bool = Field(description="True if the customer is likely to contact again regarding the same issue.")
    repeat_contact_risk_segment_ids: List[int] = Field(description="Segment IDs evidencing repeat-contact risk. Return [] if none.")
    escalation_risk:                bool = Field(description="True if the issue may require a future escalation.")
    escalation_risk_segment_ids:    List[int] = Field(description="Segment IDs evidencing escalation risk. Return [] if none.")
    callback_risk:                  bool = Field(description="True if the customer is likely to call back due to incomplete resolution.")
    callback_risk_segment_ids:      List[int] = Field(description="Segment IDs evidencing callback risk. Return [] if none.")
    reopen_risk:                    bool = Field(description="True if the issue or case is likely to be reopened.")
    reopen_risk_segment_ids:        List[int] = Field(description="Segment IDs evidencing reopen risk. Return [] if none.")

    # ── Behavioural KPIs (flat booleans, 0/1) ────────────────────────────────
    # Communication
    active_listening:   bool = Field(description="False only if the agent does not actively demonstrate active listening.")
    clarity:            bool = Field(description="False only if the agent's explanations/instructions are NOT clear and explicit.")
    professional_tone:  bool = Field(description="False only if a professional tone is consistently NOT evident.")

    # Customer Care
    empathy:            bool = Field(description="False only if the agent does not show empathy with explicit phrasing.")
    acknowledgment:     bool = Field(description="False only if the agent does not explicitly acknowledge the customer's statements or concerns.")
    reassurance:        bool = Field(description="False only if the agent does not reassure the customer or build confidence during the interaction.")
    ownership_behavior: bool = Field(description="False only if the agent does not take clear ownership of the customer's issue.")

    # Problem Solving
    issue_investigation: bool = Field(description="False only if the agent does not properly investigate or diagnose the root cause of the issue.")
    solution_guidance:   bool = Field(description="False only if the agent does not guide the customer to a clear solution.")

    # Resolution
    resolution_verification: bool = Field(description="False only if the agent does not verify with the customer that the issue was resolved.")
    next_steps_summary:      bool = Field(description="False only if the agent does not summarize next steps at the end of the call.")

    # Operations
    call_control:         bool = Field(description="False only if the agent does not demonstrate effective call control.")
    escalation_management: bool = Field(description="False only if the agent does not manage escalation/de-escalation appropriately when tension arises. None-style behaviour: set False only when clearly mishandled.")

    # ── Call handling soft skills ────────────────────────────────────────────
    comprehension:            bool = Field(description="False only if the agent did not display the ability to understand and clarify the customer's needs and concerns.")
    language_proficiency:     bool = Field(description="False only if the agent's communication was not effective and clear in the customer's language, avoiding jargon and maintaining appropriate speech rate.")
    emotional_intelligence:   bool = Field(description="False only if the agent did not recognize, understand, and respond to emotions in themselves and others.")
    relationship_building:    bool = Field(description="False only if the agent did not build rapport and trust with the customer through personalized interactions.")
    professional_skills:      bool = Field(description="False only if the agent did not demonstrate the ability to value the customer's time when handling the call.")
    subject_matter_expertise: bool = Field(description="False only if the agent did not demonstrate strong knowledge of products, services, and processes, and correct and precise verbiage to effectively assist the customer.")

    # ── Feedback ─────────────────────────────────────────────────────────────
    coaching_tip:   Optional[CoachingTip] = None  # Coaching tip
    other_feedback: Optional[str] = Field(description="Any additional critical feedback not covered by the coaching tip.")
