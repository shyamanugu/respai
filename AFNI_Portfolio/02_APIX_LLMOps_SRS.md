# Software Requirements Specification (SRS)
## APIX LLMOps Platform

> Elaborates the BRD (01_APIX_LLMOps_BRD) business requirements into detailed system/functional behaviour. Each functional requirement (FR) traces to a BR/AC. Audience: engineering, QA, design.

---

## 1. Introduction

### 1.1 Purpose
Specify the functional behaviour of the APIX LLMOps Platform: the Ops Platform (control plane), the reusable LLMOps core, and the APIX application. This document defines what each screen, service, and workflow must do so the system can be built and tested against it.

### 1.2 Scope
Covers all BRD requirements (BR-1 to BR-17). In scope: Ops Platform modules, LLMOps core services, APIX ingestion/pipeline/scoring/dashboard/chatbot/reporting/user-management/notifications/audit. Out of scope: a second use case build; real-time/streaming ingestion.

### 1.3 References
- 01_APIX_LLMOps_BRD (business requirements, source of truth)
- 03_APIX_LLMOps_Architecture (technical design)
- 00_LLMOps_Brainstorming (capability decisions)

### 1.4 Definitions & Acronyms
- **Task** — a governed unit binding a prompt version + model alias + parameters for one LLM call (e.g. `call_analysis`).
- **Alias** — a named role (e.g. `reasoning`, `bulk`) resolving to an Azure OpenAI deployment per environment.
- **Golden dataset** — certified inputs with expected outputs, versioned, used by the evaluation gate.
- **Probe** — a single checked field in an evaluation (e.g. `escalations`), scored against a tolerance.
- **Seam** — the shared invocation path every LLM call passes through (render → guardrail-in → resolve → call → guardrail-out → trace).
- **Promotion** — moving a change from a lower environment to a higher one after passing the evaluation gate and approval.
- SRS, BRD, AC, RBAC, SSO, PII, KPI, CI/CD, p95.

---

## 2. System Overview

### 2.1 System Context
The platform ingests Verizon contact-center call transcripts, analyzes them with Azure OpenAI through the governed seam, produces per-agent performance intelligence, and exposes everything for operation and consumption through the Ops Platform and APIX end-user surfaces. All LLM traffic flows through the LLMOps core; nothing calls a model directly.

### 2.2 Major Subsystems
- **Ops Platform** — web application; the primary control plane (11 modules).
- **LLMOps Core** — backend services for prompt, model, evaluation, guardrails, observability, feedback, governance; exposes the shared seam and management APIs.
- **APIX Application** — ingestion, AI pipeline, KPI/scoring engine, coaching dashboard, analytics chatbot, reporting, notifications.

### 2.3 Operating Environments
Dev, Test, Prod. Each resolves aliases, thresholds, and guardrail policy independently via configuration.

---

## 3. Actors & Roles

| Actor | Description | Core permissions |
|-------|-------------|------------------|
| Admin | Platform operator | Full access to all Ops modules, config, user/role management |
| Coach | Front-line coach | View own agents' reports; submit feedback; run chatbot scoped to own agents |
| Manager | Team manager | Team overview, rankings, escalations; read reports for their org |
| QA | Quality reviewer | Review/certify feedback and golden data; run evaluations (no promote) |
| Agent | Scored employee | View own report; raise an appeal/contest |

Rules: every screen enforces role scope; a Coach/Agent never sees data outside their hierarchy; all privileged actions are audited.

---

## 4. Functional Requirements by Module

> Convention: **FR-<module>.<n>**. Each lists behaviour, rules, and the BR/AC it satisfies.

### 4.A LLMOps Ops Platform

#### 4.A.1 Prompt Management module  (traces BR-1, BR-17.1)
**Screens:** Prompt list; Prompt detail/editor; Version history; Diff view.
- FR-PM.1 The prompt list shall display every prompt grouped by APIX program (Telesales, WCC, PSO) and pipeline step (denoise, analysis, summary), showing the active version per environment. *(AC-17.1.1)*
- FR-PM.2 The editor shall allow an authorized user to edit prompt text and save it as a new version with status `draft`. *(BR-1.1)*
- FR-PM.3 The system shall allow activating a version (status `active`), superseding the prior active version (set to `archived`), with no code deployment. *(AC-1.1, AC-17.1.2)*
- FR-PM.4 Activation shall be blocked unless the change has passed the evaluation gate and required approval (see 4.A.11). *(BR-7.1)*
- FR-PM.5 Each version shall record author, timestamp, and change note. *(BR-1.3)*
- FR-PM.6 The version history shall allow rollback to any prior version in a single action, creating a new active version. *(AC-1.2, AC-17.1.3)*
- FR-PM.7 The diff view shall show two selected versions side by side with additions/deletions highlighted. *(AC-17.1.5)*
- FR-PM.8 Each produced output shall carry the prompt version id that generated it, viewable from the report. *(BR-1.4, AC-1.3, AC-17.1.4)*
- **Validations:** change note mandatory on save; cannot activate a `draft` that failed the gate; cannot edit an `archived` version (must clone).

#### 4.A.2 Model Management module  (traces BR-2, BR-17.2)
**Screens:** Alias list; Task binding editor; Version pinning.
- FR-MM.1 The alias list shall show each alias and its resolved deployment per environment (e.g. `reasoning → gpt-5-nano` in Prod). *(AC-17.2.1)*
- FR-MM.2 A task record shall display its bound prompt version + model alias + parameters (temperature, max tokens) as one unit. *(BR-2.3, AC-17.2.2)*
- FR-MM.3 The system shall allow re-binding a task to a different alias/model via configuration, effective in the APIX pipeline with no code change. *(AC-2.1, AC-17.2.3)*
- FR-MM.4 The system shall allow pinning a specific model version per deployment; changing a pin is a governed, audited action. *(BR-2.4, AC-17.2.4)*
- FR-MM.5 On startup and on change, the system shall validate that every alias resolves to a live deployment and surface any unresolved alias. *(supports BR-2)*
- **Rules:** a task change (prompt and/or model) is a single change record that must pass the gate before promotion.

#### 4.A.3 Evaluation module  (traces BR-3, BR-17.3)
**Screens:** Evaluation run launcher; Scorecard; Fairness panel; Promotion control.
- FR-EV.1 A user shall launch an evaluation of a candidate task (e.g. `analysis v4` for PSO) against a selected golden dataset version. *(AC-17.3.1)*
- FR-EV.2 The run shall execute each golden input through the candidate and compare each probe to its expected value using the probe's tolerance (exact for counts; band for scores). *(BR-3.2)*
- FR-EV.3 The scorecard shall show per-probe pass/fail, the tolerance applied, and the overall pass rate. *(AC-3.2, AC-17.3.2)*
- FR-EV.4 The system shall compare the pass rate to the environment threshold (e.g. Dev 0.80 / Test 0.90 / Prod 0.95) and mark the run Promotable or Blocked. *(BR-3.3, AC-3.1, AC-17.3.3)*
- FR-EV.5 The fairness panel shall show score distribution/disparity across agent segments (program, tenure, language) and flag material disparity. *(BR-3.5, AC-3.3, AC-17.3.4)*
- FR-EV.6 A Promotable change may be promoted from the console by an authorized role; the action is audited. *(AC-17.3.5)*
- FR-EV.7 Evaluations shall run at per-call (per-task) and end-to-end (full pipeline) levels. *(BR-3.4)*
- **Rules:** promotion disabled when Blocked; QA may run but not promote; Admin may promote.

#### 4.A.4 Playground module  (traces BR-17.4)
**Screens:** Combination builder; Side-by-side results.
- FR-PG.1 A user shall select/enter a sample transcript, a prompt version, a model, and parameters, and run the combination. *(AC-17.4.1)*
- FR-PG.2 The system shall run two or more combinations and display outputs side by side with tokens, cost, and latency per run. *(AC-17.4.2)*
- FR-PG.3 Playground runs shall not alter any active production prompt, model, or configuration. *(AC-17.4.3)*
- FR-PG.4 A Playground combination may be promoted into the formal evaluation flow in one action. *(AC-17.4.4)*
- **Rules:** Playground uses sample/non-production data only; runs are labelled non-production in observability.

#### 4.A.5 Golden Dataset module  (traces BR-3.1, BR-6.3, BR-17.5)
**Screens:** Dataset browser; Entry editor; Certification queue.
- FR-GD.1 The browser shall show golden calls per program with certified expected values and the dataset version. *(AC-17.5.1)*
- FR-GD.2 A new/edited entry shall enter a certification queue with status `candidate`. *(BR-6.2)*
- FR-GD.3 Certification shall require two distinct reviewers' concurrence to set status `certified`; a single reviewer cannot certify. *(AC-17.5.2)*
- FR-GD.4 Certifying entries shall produce a new, immutable golden dataset version available to subsequent evaluations. *(AC-17.5.3, BR-3.1)*
- **Rules:** rejected entries are retained with reason; dataset versions are never edited in place.

#### 4.A.6 Guardrails module  (traces BR-4, BR-17.6)
**Screens:** Policy editor; Guardrail activity.
- FR-GR.1 The policy editor shall show the current action per category (PII, secrets, prompt injection, content safety) as flag / redact / hard-block. *(AC-17.6.1)*
- FR-GR.2 Changing a category's action (e.g. SSN → hard-block) shall be a configuration change effective without rebuild. *(BR-4.2, AC-17.6.2)*
- FR-GR.3 The activity view shall show guardrail flag counts on APIX runs and link each flag to its audit record. *(BR-4.4, AC-17.6.3)*
- **Rules:** policy applies to input and output of every call via the seam; raw flagged content is not displayed, only type + reference.

#### 4.A.7 Monitoring module  (traces BR-5, BR-17.7)
**Screens:** Cost dashboard; Operational dashboard; Trace drill-down; Alerts.
- FR-MO.1 Dashboards shall show cost per program per day (Telesales, WCC, PSO) using real per-token rates. *(AC-5.1, AC-17.7.1)*
- FR-MO.2 Dashboards shall show latency p95, error rate, token usage, and guardrail-flag rate. *(AC-17.7.2)*
- FR-MO.3 The system shall raise an alert when a cost or error threshold is breached, visible in the console. *(BR-5.3, AC-5.2, AC-17.7.3)*
- FR-MO.4 A user shall drill from a metric to an individual call's trace showing metadata and IDs only — never raw transcript content. *(BR-5.4, AC-5.3, AC-17.7.4)*
- **Rules:** every trace record carries tokens, cost, latency, model/prompt version, step, guardrail outcome (BR-5.1).

#### 4.A.8 Feedback module  (traces BR-6, BR-17.8)
**Screens:** Feedback inbox; Item detail; Routing.
- FR-FB.1 The inbox shall show corrections tied to a call, segment, and the prompt/model version that produced the output. *(BR-6.1, AC-17.8.1)*
- FR-FB.2 A reviewer shall triage an item and route it to the golden-dataset certification queue. *(AC-17.8.2)*
- FR-FB.3 Each item shall display lifecycle status: captured / in-review / certified / rejected. *(AC-17.8.3)*
- **Rules:** uncertified feedback never alters the golden dataset (BR-6.3, AC-6.2); agents' appeals arrive here as `captured`.

#### 4.A.9 Access & Roles module  (traces BR-14, BR-17.9)
**Screens:** User list; Role assignment.
- FR-AR.1 Users shall authenticate via Microsoft Entra SSO. *(BR-14.1, AC-17.9.1)*
- FR-AR.2 An admin shall assign roles (admin/coach/manager/QA/agent); role scope is enforced across every module. *(BR-14.2)*
- FR-AR.3 A coach/agent shall see only their own hierarchy's data across all modules. *(AC-17.9.2, AC-11.2)*
- FR-AR.4 Every access and role change shall be recorded in the audit trail. *(AC-17.9.3)*

#### 4.A.10 APIX Operations module  (traces BR-8..BR-10, BR-17.10)
**Screens:** Run launcher; Run monitor; Report viewer; Program config.
- FR-OP.1 A user shall trigger an APIX pipeline run (denoise → analysis → summary → individual metrics → KPI) for a selected program and date. *(AC-17.10.1)*
- FR-OP.2 The run monitor shall show each step's status (queued/running/succeeded/failed) and surface failures with cause. *(AC-17.10.2)*
- FR-OP.3 The report viewer shall display a produced per-agent weekly report with KPIs, behavior scores, and evidence segments. *(AC-17.10.3)*
- FR-OP.4 An admin shall edit program configuration (KPI targets, thresholds, status bands) without a code change. *(AC-17.10.4, BR-14.3)*
- **Rules:** a run respects the active prompt/model/guardrail config at trigger time; config used is stamped on outputs.

#### 4.A.11 Governance & Audit view  (traces BR-7, BR-17.11)
**Screens:** Approval queue; Audit explorer.
- FR-GA.1 A production change shall require evaluation-gate pass plus human approval, enforced before activation/promotion. *(BR-7.1, AC-7.1, AC-17.11.1)*
- FR-GA.2 The audit explorer shall reconstruct any AI decision/score from its prompt, model, and data version. *(BR-7.3, AC-7.2, AC-17.11.2)*
- FR-GA.3 Every change, approval, access, and guardrail action shall be queryable in one place. *(BR-7.2, AC-17.11.3)*

#### Cross-cutting (traces AC-17.0.x)
- FR-X.1 Every LLMOps capability (BR-1..BR-7) is represented by an operable module reflecting live APIX data.
- FR-X.2 Every console action is role-governed and audited.
- FR-X.3 No capability requires direct backend access to operate.

### 4.B APIX Application

#### 4.B.1 Data Ingestion  (traces BR-8)
- FR-IN.1 A scheduled job shall ingest transcripts into a validated store on a defined cadence. *(BR-8.1, AC-8.1)*
- FR-IN.2 The job shall validate required fields (call id, employee id, program, transcript text) and quarantine malformed records without failing the batch. *(BR-8.2, AC-8.2)*
- FR-IN.3 The system shall support on-demand historical backfill for a date range/program. *(BR-8.3)*

#### 4.B.2 AI Pipeline  (traces BR-9)
- FR-AI.1 The pipeline shall execute denoise → analysis → summary → individual metrics → KPI roll-up. *(BR-9.1)*
- FR-AI.2 The analysis step shall produce per-call evaluations via the program-specific structured-output schema (SalesAgentEvaluation / WccAgentEvaluation / PsoAgentEvaluation). *(BR-9.2)*
- FR-AI.3 Every AI judgment shall include its evidence segment reference(s). *(AC-9.2)*
- FR-AI.4 The summary step shall produce per-employee weekly reports. *(BR-9.3, AC-9.1)*
- **Rules:** every model call routes through the seam (guardrails, model resolution, trace).

#### 4.B.3 KPI / Scoring Engine  (traces BR-10)
- FR-KPI.1 The engine shall codify each program's KPI rulebook (Telesales/WCC/PSO). *(BR-10.1)*
- FR-KPI.2 The engine shall compute the composite performance score = Quality 40% + Target 30% + Trend 20% + Penalty 10%. *(BR-10.2)*
- FR-KPI.3 The engine shall assign status bands (≥80 Strong, ≥70 Developing, else Focus) and risk flags. *(BR-10.3)*
- FR-KPI.4 Outputs shall validate against SME-certified samples; the methodology shall score care and sales programs fairly. *(BR-10.4, AC-10.1, AC-10.2)*

#### 4.B.4 Coaching Dashboard  (traces BR-11)
- FR-DB.1 The dashboard shall present scorecards, behavior scores, team comparisons, and AI coaching tips with evidence excerpts. *(BR-11.1, AC-11.1)*
- FR-DB.2 Views shall be scoped by role; a user sees only permitted data. *(BR-11.2, AC-11.2)*

#### 4.B.5 Analytics Chatbot  (traces BR-12)
- FR-CB.1 The chatbot shall answer natural-language questions over performance data, scoped to the user's agents. *(BR-12.1, AC-12.1)*
- FR-CB.2 Generated queries shall be validated as read-only and scope-injected; out-of-scope data is never returned. *(AC-12.2)*
- FR-CB.3 The chatbot shall provide conversation history and feedback capture. *(BR-12.2)*

#### 4.B.6 Reporting & Export  (traces BR-13)
- FR-RP.1 The system shall generate scheduled coaching reports from KPI output. *(BR-13.1, AC-13.1)*
- FR-RP.2 The system shall export to PDF/Excel/CSV and deliver via notifications. *(BR-13.2)*

#### 4.B.7 User Management & Access  (traces BR-14)
- Covered by 4.A.9 and FR-OP.4; admin configuration panel for programs/users/thresholds without code. *(BR-14.3, AC-14.2)*

#### 4.B.8 Notifications & Audit Viewer  (traces BR-15)
- FR-NT.1 The system shall notify users via email/Teams of key events (report ready, flagged score, run failure). *(BR-15.1, AC-15.1)*
- FR-NT.2 The audit viewer shall let compliance see who/what/when/why. *(BR-15.2, AC-15.2)*

---

## 5. Data Requirements

### 5.1 Key Entities
Prompt, PromptVersion, Alias, Deployment, Task, GoldenDataset, GoldenEntry, EvaluationRun, ProbeResult, GuardrailPolicy, GuardrailEvent, TraceRecord, FeedbackItem, User, Role, AuditEvent, Program, Agent, CallEvaluation, AgentReport, KPI, BehaviorScore.

### 5.2 Data Dictionary
_To be detailed per entity (attributes, types, keys) — next pass._

### 5.3 Data Retention
Driven by confirmed compliance standards; audit, traces, and golden data retained per policy. _Values TBD with compliance._

---

## 6. Interface Requirements

### 6.1 Internal — the shared invocation seam
Every LLM call: render prompt (active version) → guardrail-check input → resolve alias→deployment → call model → guardrail-check output → record trace + cost. Management APIs expose each Ops module's operations.

### 6.2 External Integrations
Azure OpenAI (models), Azure AI Foundry (prompt flow + evaluation), Azure AI Search (future RAG), Azure Blob Storage (transcripts/outputs), Azure SQL (KPIs/audit/feedback), Microsoft Entra ID (SSO), Azure Key Vault (secrets), Azure Monitor/App Insights (telemetry), Azure AI Content Safety (moderation), Email/Teams (notifications).

---

## 7. Non-Functional Requirements (system level)
- **Performance:** batch pipeline completes within the agreed window at target volume; chatbot responds within interactive latency.
- **Scalability:** onboard a new use case without re-architecting the core; handle program/user growth.
- **Reliability:** platform components fail open where safe; rollback supported on gate failure/incident.
- **Security:** Entra SSO, RBAC, Key Vault secrets, least-privilege managed identities; no PII in traces.
- **Auditability:** segment-level evidence; model/prompt version stamping on every output.
- **Cost transparency:** real-rate cost per call/step/program.

---

## 8. Reports & Notifications
- **Reports:** per-agent weekly coaching report; manager team overview; cost/usage report.
- **Notifications:** report ready, flagged/low score, pipeline run failure, budget/error alert — via email and Teams.

---

## 9. Traceability Matrix
Each FR above cites its BR/AC inline. A consolidated BR → FR → Test-case matrix will be maintained as test design begins. _(Matrix table TBD.)_

---

## 10. Open Items
- Data dictionary detail per entity (5.2).
- Retention values pending compliance confirmation (5.3).
- Consolidated traceability/test matrix (9).
- Registry store choice affects entity persistence detail (ties to architecture ADR-1).
