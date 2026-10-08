# Business Requirements Document
## End-to-End APIX on a Reusable LLMOps Platform

---

## 1. Purpose
Implement APIX (Afni Performance Intelligence Index) end to end on a standardized LLMOps platform. The LLMOps platform must be reusable for future AFNI AI use cases through configuration, not rebuild.

## 2. Business Objectives
- Deliver APIX as a production platform that turns 100% of contact-center interactions into agent-level performance intelligence and coaching.
- Operate every LLM interaction under a governed LLMOps discipline (versioning, evaluation, guardrails, observability, feedback, audit).
- Build the LLMOps core once so the next use case reuses it by configuration.

## 3. Scope
**In scope:** the reusable LLMOps platform core; the LLMOps Ops Platform (management console); the full APIX application (ingestion, AI pipeline, scoring engine, coaching dashboard, analytics chatbot, reporting, user management, notifications, audit); onboarding of real users.
**Out of scope:** any second use case build; real-time / streaming ingestion.

## 4. Stakeholders
AFNI AI Practice Head (sponsor); Coaches, Managers, QA (primary users); Agents (scored, with appeal rights); Compliance / Risk; Delivery team.

---

## 5. Business Requirements

### Group A - Reusable LLMOps Platform Core

### BR-1 - Prompt Management
The platform shall manage all prompts as versioned, governed assets.
- BR-1.1 Store every prompt with a unique version and status (draft / active / archived).
- BR-1.2 Activate or roll back a prompt version without a code deployment.
- BR-1.3 Record author, timestamp, and change note on every version.
- BR-1.4 Stamp the prompt version used onto every output it produces.

**Acceptance Criteria**
- AC-1.1 A prompt can be edited and activated by an authorized user with no code release.
- AC-1.2 Any prior version can be restored and becomes active within one action.
- AC-1.3 Given any output, the exact prompt version that produced it can be identified.

### BR-2 - Model Management
The platform shall route and manage models by configuration.
- BR-2.1 Call models via a named role / alias (e.g. reasoning, bulk), not a hardcoded model name.
- BR-2.2 Change the model behind an alias per environment via configuration only.
- BR-2.3 Bind prompt version + model alias + parameters together as a single task unit.
- BR-2.4 Pin model versions to prevent silent provider upgrades.

**Acceptance Criteria**
- AC-2.1 A model swap for a task requires a config change only, no code.
- AC-2.2 Changing a task's prompt and model is a single governed change.
- AC-2.3 Each environment can resolve the same alias to a different deployment.

### BR-3 - Evaluation Gate
The platform shall prevent unproven changes from reaching production.
- BR-3.1 Maintain a versioned golden dataset of certified inputs and expected outputs.
- BR-3.2 Score any prompt / model / task change against the golden dataset.
- BR-3.3 Enforce an environment-specific pass threshold; block promotion on failure.
- BR-3.4 Evaluate at both per-call and end-to-end levels.
- BR-3.5 Support fairness evaluation across agent segments (program, tenure, language).

**Acceptance Criteria**
- AC-3.1 A change scoring below the environment threshold cannot be promoted.
- AC-3.2 Evaluation output shows per-probe pass / fail and an overall pass rate.
- AC-3.3 A material score disparity across a segment is surfaced before release.

### BR-4 - Guardrails
The platform shall enforce safety policy on every LLM call.
- BR-4.1 Detect PII, secrets, and prompt injection on input and output.
- BR-4.2 Apply a configurable action per category (flag / redact / hard-block).
- BR-4.3 Apply content-safety checks to model input and output.
- BR-4.4 Log every guardrail decision.

**Acceptance Criteria**
- AC-4.1 PII in a transcript is handled per the configured policy on every call.
- AC-4.2 Changing a category's action is a configuration change, not a rebuild.
- AC-4.3 Every guardrail action is retrievable in the audit log.

### BR-5 - Observability
The platform shall make every LLM call measurable.
- BR-5.1 Record tokens, cost, latency, model / prompt version, step, and guardrail outcome per call.
- BR-5.2 Surface cost by program and by day.
- BR-5.3 Alert when cost or error thresholds are breached.
- BR-5.4 Exclude raw transcript content from telemetry (metadata / IDs only).

**Acceptance Criteria**
- AC-5.1 Cost per call, per step, and per program is viewable with real rates.
- AC-5.2 A latency or error spike raises an alert.
- AC-5.3 No raw PII content appears in any trace.

### BR-6 - Feedback Loop
The platform shall improve from human feedback.
- BR-6.1 Capture coach / QA corrections and ratings tied to a call segment and the version that produced the output.
- BR-6.2 Route corrections through a certification queue requiring two-reviewer concurrence.
- BR-6.3 Admit only certified corrections into the golden dataset.

**Acceptance Criteria**
- AC-6.1 A correction is captured with its call, segment, and version context.
- AC-6.2 Uncertified feedback never alters the golden dataset.
- AC-6.3 A certified correction becomes available to the next evaluation run.

### BR-7 - Governance & Audit
The platform shall make every change and decision accountable.
- BR-7.1 Require eval-gate pass plus human approval before a change reaches production.
- BR-7.2 Log who changed what, when, and why for prompts, models, and config.
- BR-7.3 Trace any AI decision to its prompt, model, and data version.

**Acceptance Criteria**
- AC-7.1 A production change without approval is rejected.
- AC-7.2 A disputed score can be fully reconstructed from the audit trail.

---

### Group B - LLMOps Ops Platform (Primary Control Plane)

### BR-17 - Ops Platform
The Ops Platform is the central, user-facing product of this engagement. It is the single console through which every LLMOps capability becomes visible, operable, and governed, each one integrated with live APIX data. The LLMOps core and the APIX pipeline run behind it; the Ops Platform is where they are seen and controlled. Without it the platform is backend-only with nothing to show. Every requirement below (BR-1 to BR-16) must be reflected and operable here.

#### BR-17.1 - Prompt Management module
Manage every APIX prompt (denoise, analysis, summary) per program (Telesales, WCC, PSO) as versioned assets.
**Acceptance Criteria**
- AC-17.1.1 The console lists each APIX prompt by program and step, showing the version currently active in each environment.
- AC-17.1.2 An authorized user edits and activates a program's analysis prompt (e.g. PSO) with no code deployment.
- AC-17.1.3 Version history shows author, timestamp, and change note; any prior version can be rolled back in one action.
- AC-17.1.4 A produced agent report shows the exact prompt version that generated it.
- AC-17.1.5 Two prompt versions can be diffed side by side before activation.

#### BR-17.2 - Model Management module
Manage aliases (reasoning, bulk) to Azure OpenAI deployments per environment, and the task bindings that pair a prompt version with a model.
**Acceptance Criteria**
- AC-17.2.1 The console shows each alias and the deployment it resolves to per environment (e.g. reasoning -> gpt-5-nano in prod).
- AC-17.2.2 A task (e.g. call_analysis) shows its bound prompt version + model alias + parameters as one unit.
- AC-17.2.3 Re-binding a task to a different model is a config action that takes effect in the APIX pipeline with no code change.
- AC-17.2.4 Pinned model versions are visible and changing one is a governed action.

#### BR-17.3 - Evaluation module
Run and view evaluations of APIX tasks against the golden dataset, with thresholds and promotion control.
**Acceptance Criteria**
- AC-17.3.1 A user runs an evaluation of a candidate APIX task (e.g. analysis v4 for PSO) from the console.
- AC-17.3.2 The scorecard shows each probe's pass/fail (e.g. escalations, save_attempts, behavior scores) and the overall pass rate.
- AC-17.3.3 The environment threshold is shown (e.g. PROD 0.95); a result below it is blocked from promotion.
- AC-17.3.4 Fairness results across agent segments (program, tenure, language) are shown alongside accuracy.
- AC-17.3.5 A passing change can be promoted from the console, and the action is audited.

#### BR-17.4 - Playground module
Run ad hoc prompt + model + parameter combinations against a sample APIX transcript, with no production impact.
**Acceptance Criteria**
- AC-17.4.1 A user selects a sample transcript, a prompt version, a model, and parameters, and runs the combination.
- AC-17.4.2 Two or more combinations (e.g. prompt v3 + gpt-5-nano vs prompt v4 + gpt-5-mini) run and display outputs side by side with tokens, cost, and latency.
- AC-17.4.3 Nothing in the Playground alters any active production prompt, model, or config.
- AC-17.4.4 A promising Playground combination can be sent into the formal evaluation flow.

#### BR-17.5 - Golden Dataset module
View, label, certify, and version the golden dataset per program, through a certification queue.
**Acceptance Criteria**
- AC-17.5.1 The console shows golden calls per program with their certified expected values and dataset version.
- AC-17.5.2 A new candidate entry enters a certification queue and requires two-reviewer concurrence to be certified.
- AC-17.5.3 A certified entry produces a new golden dataset version available to the next evaluation.

#### BR-17.6 - Guardrails module
View and configure per-category guardrail policy and see guardrail activity on live APIX calls.
**Acceptance Criteria**
- AC-17.6.1 The console shows the current policy per category (PII, secrets, injection, content safety) as flag / redact / hard-block.
- AC-17.6.2 Changing a category's action (e.g. SSN -> hard-block) is a config action with no rebuild.
- AC-17.6.3 The console shows guardrail flag counts on APIX runs and links each to its audit record.

#### BR-17.7 - Monitoring module
Show operational and cost telemetry for APIX across the platform.
**Acceptance Criteria**
- AC-17.7.1 Dashboards show cost per program per day (Telesales, WCC, PSO) using real rates.
- AC-17.7.2 Dashboards show latency (p95), error rate, token usage, and guardrail-flag rate.
- AC-17.7.3 A breach of a cost or error threshold raises an alert visible in the console.
- AC-17.7.4 A user can drill from a metric to a call's trace (metadata and IDs only, never raw transcript content).

#### BR-17.8 - Feedback module
Review and route coach/QA feedback on APIX outputs.
**Acceptance Criteria**
- AC-17.8.1 The console shows corrections tied to a call, segment, and the prompt/model version that produced the output.
- AC-17.8.2 A reviewer can triage a correction and route it to the golden-dataset certification queue.
- AC-17.8.3 Each feedback item's lifecycle status (captured / in-review / certified / rejected) is visible.

#### BR-17.9 - Access & Roles module
Manage identity, roles, and access across the platform.
**Acceptance Criteria**
- AC-17.9.1 Users sign in via Microsoft Entra SSO; roles (admin, coach, manager, QA, agent) are assignable in the console.
- AC-17.9.2 A coach sees only their own agents' data across every module.
- AC-17.9.3 Every access and role change is recorded in the audit trail.

#### BR-17.10 - APIX Operations module
Operate the APIX pipeline and manage its program configuration.
**Acceptance Criteria**
- AC-17.10.1 A user triggers an APIX pipeline run (denoise -> analysis -> summary -> individual metrics -> KPI) for a program and date.
- AC-17.10.2 The console shows each step's status and surfaces failures.
- AC-17.10.3 A produced per-agent weekly report can be viewed with its KPIs, behavior scores, and evidence.
- AC-17.10.4 Program configuration (KPI targets, thresholds, status bands) can be edited without a code change.

#### BR-17.11 - Governance & Audit view
Expose change governance and the audit trail through the console.
**Acceptance Criteria**
- AC-17.11.1 A production change requires eval-gate pass plus human approval, enforced in the console.
- AC-17.11.2 The audit view reconstructs any disputed score from its prompt, model, and data version.
- AC-17.11.3 Every change, approval, access, and guardrail action is queryable in one place.

#### Cross-cutting Ops Platform criteria
- AC-17.0.1 Every LLMOps component (BR-1 to BR-7) is represented by an operable module in the Ops Platform, reflecting live APIX data.
- AC-17.0.2 Every action in the console is governed by role and recorded in the audit trail.
- AC-17.0.3 The Ops Platform is the single surface for operating the platform; no capability requires direct backend access to use.

---

### Group C - APIX Application (End to End)

### BR-8 - Data Ingestion
- BR-8.1 Ingest call transcripts on a scheduled basis into a validated store.
- BR-8.2 Validate data quality and handle incomplete records without failing the batch.
- BR-8.3 Support historical backfill.

**Acceptance Criteria**
- AC-8.1 A scheduled run ingests and validates new transcripts.
- AC-8.2 Malformed records are quarantined, not fatal to the batch.

### BR-9 - AI Pipeline
- BR-9.1 Process transcripts through denoise -> analysis -> summary -> individual metrics -> KPI roll-up.
- BR-9.2 Produce per-call evaluations via program-specific structured output.
- BR-9.3 Produce per-employee weekly reports.

**Acceptance Criteria**
- AC-9.1 A weekly run yields per-agent reports and KPIs.
- AC-9.2 Every AI judgment carries its evidence segment reference.

### BR-10 - KPI / Scoring Engine
- BR-10.1 Codify each program's KPI rulebook (Telesales, WCC, PSO).
- BR-10.2 Compute the composite performance score (Quality 40% / Target 30% / Trend 20% / Penalty 10%).
- BR-10.3 Apply status bands (>=80 Strong, >=70 Developing, else Focus) and risk flags.
- BR-10.4 Ensure the composite score is fair across all programs.

**Acceptance Criteria**
- AC-10.1 Scores match SME-certified samples.
- AC-10.2 The same methodology scores care and sales programs fairly.

### BR-11 - Coaching Dashboard
- BR-11.1 Present scorecards, behavior scores, team comparisons, and AI coaching tips with evidence.
- BR-11.2 Scope views by role (coach / manager / QA / agent).

**Acceptance Criteria**
- AC-11.1 A coach sees their agents' reports with evidence excerpts.
- AC-11.2 A user sees only data permitted by role.

### BR-12 - Analytics Chatbot
- BR-12.1 Answer natural-language questions over performance data, scoped to the user's agents.
- BR-12.2 Provide conversation history and feedback capture.

**Acceptance Criteria**
- AC-12.1 A valid question returns a correct, scope-limited answer.
- AC-12.2 Out-of-scope data is never returned.

### BR-13 - Reporting & Export
- BR-13.1 Generate scheduled coaching reports from KPI output.
- BR-13.2 Export to PDF / Excel / CSV and deliver via notifications.

**Acceptance Criteria**
- AC-13.1 Weekly reports generate and deliver automatically.

### BR-14 - User Management & Access
- BR-14.1 Provide SSO via Microsoft Entra ID.
- BR-14.2 Enforce RBAC across admin / coach / manager / QA / agent.
- BR-14.3 Provide user provisioning and an admin configuration panel (programs, users, thresholds) without code.

**Acceptance Criteria**
- AC-14.1 Access is governed by role.
- AC-14.2 An admin changes configuration without a release.

### BR-15 - Notifications & Audit Viewer
- BR-15.1 Notify users (email / Teams) of key events.
- BR-15.2 Provide an audit-trail viewer for compliance and dispute resolution.

**Acceptance Criteria**
- AC-15.1 Event notifications are delivered.
- AC-15.2 Compliance can view who / what / when / why.

---

### Group D - Reusability

### BR-16 - Platform Reuse
- BR-16.1 Onboard a future use case through configuration (models, prompts, guardrail policy, golden dataset) without rebuilding the core.
- BR-16.2 Provide a documented onboarding path.

**Acceptance Criteria**
- AC-16.1 A new use case reaches evaluation using config + golden data only, with no change to shared platform code.

---

## 6. Success Criteria
APIX runs in production at parity with the proven POC; every LLM change passes the evaluation gate in CI/CD; guardrails enforce the agreed policy; cost is visible at real rates; RBAC is live; the Ops Platform manages all capabilities; pilot and full rollout are complete; a future use case can be onboarded by configuration.
