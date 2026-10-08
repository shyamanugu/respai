# AFNI LLMOps — Brainstorming Notes (APIX first use case)

> Living document. Captures decisions and options as we discuss. Techno-functional + architecture working notes.

---

## 1. What LLMOps Covers

The operating layer around every LLM call:

1. **Prompt management** — version prompts, change without redeploying code, roll back.
2. **Model management** — route to models by config, swap models/versions safely.
3. **Evaluation gates** — score changes against a golden dataset before they ship.
4. **Guardrails** — PII, secrets, injection, content safety on input/output.
5. **Observability** — tokens, cost, latency, errors, per call.
6. **Feedback loops** — human corrections flow back into the golden dataset.
7. **Governance & audit** — who changed what, why; traceability of every AI decision.

**Value for APIX specifically:**
- **Trust** — coaches/QA act on AI scores affecting real people; eval gates + evidence-traceability make a score defensible.
- **Cost control** — scoring 100% of calls = heavy token use; observability makes spend visible before the bill.
- **Safety/compliance** — transcripts carry PII (insurance, customer data); guardrails mandatory for a BPO.
- **Quality that doesn't drift** — a prompt tweak helping one program can break another; eval gate catches it pre-prod.
- **Speed to improve** — coach feedback actually improves the model over time, not just logged.

---

## 2. Prompt Management

### Options under consideration (3 registry approaches)

Ruled out: **in-code constants** (pure POC, already exists — too rigid), **YAML config** (still needs git push, technical-only editing), **third-party e.g. LangSmith** (data leaves Azure boundary — compliance no-go for BPO/PII).

Carried forward: **(A) JSON versioned in-repo**, **(B) Cosmos DB registry**, **(C) Azure AI Foundry Prompt Flow**.

> Note: original options 2 (YAML config) and 3 (DB/Blob registry) are **merged** — instead of Blob + SQL we use a single **Cosmos DB** store as the registry, holding prompt text + version + active-flag + metadata as documents.

| Variable | A. JSON versioned in-repo | B. Cosmos DB registry | C. Foundry Prompt Flow |
|---|---|---|---|
| **Change without redeploy** | ❌ No (git push) | ✅ Yes | ✅ Yes |
| **Versioning / rollback** | ✅ Git history | ✅ Doc versions + active flag | ✅ Managed |
| **Non-engineer editing** | ⚠️ Technical | ✅ Via admin UI | ✅ Studio UI |
| **Built-in evaluation** | ❌ Build it | ❌ Build it | ✅ Native |
| **Approval workflow** | PR review | ⚠️ Build it | ⚠️ Partial |
| **Output version stamping** | Manual | ✅ Easy | ✅ Native |
| **Azure-native / in boundary** | ✅ Yes | ✅ Yes | ✅ Yes |
| **Compliance fit (BPO/PII)** | ✅ High | ✅ High | ✅ High |
| **Build effort** | Low | Medium–High | Low–Med |
| **Ongoing maintenance** | Low | High (we own it) | Low (Azure owns) |
| **Cost** | Free | Cosmos usage (see below) | Azure usage (see below) |
| **Lock-in risk** | None | None (we own data) | Medium (Azure) |

### Cost — how it actually incurs

- **A. JSON in-repo** — $0. Prompts ride in the git repo; no runtime infra.
- **B. Cosmos DB (serverless)** — billed on **Request Units (RUs)** + **storage**. Rough order: ~**$0.25 per 1M RUs**, storage ~**$0.25/GB/month**. Prompts are tiny text docs read infrequently (cached in app) → realistically a **few dollars/month**, dominated by reads not storage. Provisioned-throughput mode would cost more (~$24/mo per 400 RU/s) — use serverless.
- **C. Foundry Prompt Flow** — prompt storage itself is negligible; real cost is **evaluation runs** = model **token cost** + any **compute** for the flow. I.e. you pay when you *test* prompts, not to store them. Needs real token rates to put a number on it.

### Open question to revisit
- For B, confirm Cosmos is the right store vs. Azure SQL (we already use SQL for KPIs). Cosmos = flexible schema + cheap document reads; SQL = one less data technology. **Decision pending.**

---

## 3. Model Management

**What it is:** controlling which model each LLM call uses and being able to change it safely — without touching code.

**Core pattern: alias → deployment.** Code calls a *role* (`reasoning`, `bulk`), not a model name. Config maps the alias to an actual Azure OpenAI deployment per environment.

**Why it's needed even in a stable prod** (not an experimentation tool):
- Azure **retires/deprecates** models on their schedule — swap must be config, not a release.
- Adopt a **cheaper/better** model later without re-engineering.
- Prod **incident** (latency/cost spike, outage) needs an immediate switch lever.
- **Version pinning** so Azure doesn't auto-upgrade under us.

**Where the switch happens:** the **call-site abstraction**. Every call declares a role; all roles resolve in one central registry. One place to change, not N call sites.

**Key design decision — bind prompt + model per call.** A prompt is coupled to its model. Changing a prompt may require changing the model for that call. So the unit isn't "prompt" or "model" separately — it's a **task**:

`task → { prompt version, model alias, params }`

- Call site just says "run task `call_analysis`."
- Change prompt *and* model for that task = one config entry, no code.
- The **eval gate tests the pair** (prompt+model), which is what actually matters.
- Registry evolves from "prompt store" → **task config store**.

**Foundry's role:** Foundry provides the **deployments** (model inventory). It does **not** natively give semantic roles like `bulk`/`reasoning` — that alias/task layer is **application-side config we own** (thin). Prompt Flow lets you pick a model per node, but the app-wide role abstraction is ours.

**Risks & mitigations:**

| Risk | Mitigation |
|---|---|
| Silent behavior change on swap | Model/prompt swap must pass eval gate |
| Deployment drift (alias → missing deployment) | Validate aliases resolve at startup |
| Azure auto-upgrades model | Pin specific model versions |

**Open question:** task config lives in the same registry as prompts (Cosmos / Foundry / in-repo) — confirm single store for both. *Decision pending.*

---

## 4. Evaluation Gate

**What it is:** before any change ships, score the candidate against a certified **golden dataset**; pass the threshold → promote, fail → blocked.

**When the gate runs:**
- On a **prompt change**, **model/task change**, or **golden-dataset update**.
- As a scheduled **regression check** (catches model drift with no change).
- Wired into **CI/CD** as an automated, blocking step.

**How evaluation runs:** golden inputs → run through candidate task (prompt+model pair) → compare output to certified expected output via a metric → aggregate pass rate → compare to threshold.

**Fairness (RAI) — golden dataset criteria:** must be **stratified across segments we could be unfair to**, not just accurate overall. For APIX: **program, agent tenure, language/accent (native vs non-native), shift/channel**. Evaluate score **disparity** across segments — systematic lower scores for the same behavior = fairness flag. Needs stratified data + a disparity metric on top of accuracy.

**How much golden data:** meaningful sample **per segment per program**, not one pool. Start ~**50–100 certified calls per program** (more for the most granular program); grows via the feedback loop. Final number = D-3 decision with SMEs.

**Metrics — fixed vs range (by output type):**

| Output type | Metric approach |
|---|---|
| KPI counts / rates (arithmetic) | Exact / tight tolerance |
| Behavior / soft-skill scores (0–1) | Tolerance band (e.g. ±0.1) |
| Free-text coaching | Similarity or LLM-as-judge (range) |

**Who provides it:** AFNI **SMEs/coaches certify**, with **2-reviewer concurrence**. Engineering builds tooling; SMEs own ground truth. This is the real bottleneck, not the tech.

**Where it's managed:** versioned store (Foundry dataset feature, or Cosmos/SQL + Blob for call text). **Versioning is mandatory** — which dataset version a gate ran against matters for audit.

**Framework:** **Azure AI Foundry evaluation** (built-in: groundedness, relevance, similarity + custom + LLM-as-judge). Off-Azure options (promptfoo, DeepEval, RAGAS) exist but pull us off-stack.

**Evaluation level — per-call AND end-to-end** (APIX has denoise → analysis → summary):
- **Per-call** — each task has its own golden set + metric; localizes *where* a regression is.
- **End-to-end** — final coaching output / KPI correct after the full chain; catches compounding errors.
- APIX note: KPI is "correct by construction" if the **analysis call** is correct → analysis is the **highest-value eval point**, end-to-end as backstop.

**Open questions:** golden-dataset store (shared with prompts/tasks?); fairness disparity thresholds; exact per-program volume. *Pending (ties to D-3).*

---

## 5. Guardrails

**What it is:** policy checks on every LLM call — on **input** (before model) and **output** (after model).

**What we guard against (APIX-specific):**
- **PII** — transcripts carry customer names, DOB, policy numbers, possibly SSN/card data (insurance BPO). Biggest one.
- **Secrets** — unlikely in transcripts but cheap to check.
- **Prompt injection** — caller/agent text like "ignore your instructions" landing in a transcript the model processes.
- **Content safety** — abusive/toxic call content; also keeping the AI's *output* coaching professional.

**Policy per category (flag / redact / hard-block)** — the key decision:
- PII → likely **redact or flag-for-audit** (content still needed to score the call — can't just drop it).
- Secrets / card / SSN → **hard-block** candidates.
- Injection → **block or neutralize**.
- This policy is a **D-1 decision** (depends on confirmed compliance standards).

**Where/how — the options:**

| Option | Covers | Note |
|---|---|---|
| Azure AI Content Safety | Hate/violence/self-harm/sexual + prompt-shield | Managed, per-call cost |
| Azure AI Language (PII) | PII entity detection/redaction | Managed |
| Custom regex/rules | AFNI-specific patterns (policy #, internal IDs) | Managed services won't know these |
| Foundry content filters | Baseline safety at deployment level | The floor |

**Recommended:** layered — Foundry deployment filter as floor, Azure AI Language for PII redaction, Content Safety for toxicity/injection, thin custom-rules layer for AFNI identifiers. All sit in the **shared invocation seam** so every call gets them uniformly.

**Risks & mitigations:**

| Risk | Mitigation |
|---|---|
| Over-redaction kills analysis signal | Tune + test against golden data |
| Latency/cost (each guardrail = another call) | Batch/async where possible |
| Audit gaps | Log every guardrail decision (ties to audit trail + RAI) |

**Functional catch:** PII redact-vs-flag can't be finalized until compliance standards are confirmed (D-1). Design the layer **policy-configurable** so changing behavior is config, not a rebuild.

**Open questions:** per-category policy (D-1); which managed services vs. custom; latency budget per call. *Pending.*

---

## 6. Observability

**What it is:** capturing what happens on every LLM call — **tokens, cost, latency, model/prompt version, step, guardrail outcome, errors.**

**Why it matters for APIX:**
- **Cost** — scoring 100% of calls is heavy token spend; invisible without this until the bill.
- **Debugging** — trace a wrong coaching output back to the exact call, prompt version, model.
- **Drift detection** — latency creeping, error rate rising, cost-per-call jumping.
- **Audit/RAI** — ties every output to the versions that produced it.

**How/where — options:**

| Option | Use |
|---|---|
| Azure Monitor / Application Insights | Native default — traces, metrics, dashboards, alerts (prod) |
| Foundry tracing | Built-in trace view for prompt flow runs |
| Local JSONL tracer | POC/dev only, not prod |

**What to instrument:** the **shared invocation seam** emits one structured trace record per call → App Insights. Same seam as guardrails/model-routing, so instrumentation is automatic, not per-call code.

**Dashboards/alerts:** cost per program/day, latency p95, error rate, guardrail-flag rate, budget threshold alerts.

**Risks & mitigations:**

| Risk | Mitigation |
|---|---|
| PII leaking into traces (BPO concern) | Log metadata + IDs, never raw transcript content |
| Telemetry volume/cost | Sample where safe; keep cost/error data complete |

---

## 7. Feedback Loop

**What it is:** capturing human corrections/ratings on AI output and routing them back into the golden dataset so the system improves with real usage.

**What kind of feedback:**
- **Structured** (golden-usable): thumbs up/down, accept/edit/reject, corrected value, correct/incorrect flag on a judgment.
- **Free-text** (diagnostic context): "missed the empathy in line 12" — useful for diagnosis, not directly ingestible.
- **Highest value:** a **corrected judgment tied to a specific call segment** — a ready-made golden entry (input → certified correct output).

**Who provides it:**
- **Coaches** — primary; review reflections/scores in daily workflow.
- **QA reviewers** — more rigorous, catch systematic issues.
- **Managers** — spot-check / escalations.
- **Agents** — only via **contest/appeal** path; flags a case for coach/QA review, never edit golden data directly.

**How it improves the golden set (pipeline, not auto-ingest):**
1. Coach correction captured → **candidate** entry.
2. Enters **certification queue**.
3. **2-reviewer concurrence** certifies → only then enters golden dataset.
4. New golden version → tightens next eval gate.
- **Rule:** feedback ≠ ground truth until certified. One opinion doesn't move the benchmark; two concurring reviewers do.

**Where tracked:**
- **Capture** — in the **dashboard** review screen, tied to call ID + segment + **prompt/model version** that produced the output.
- **Store** — **feedback table (Azure SQL)**, separate from golden store until certified.
- **Lifecycle status** — `captured → in-review → certified / rejected`; shows pending vs. actioned, and is auditable.

**Functional catch:** feedback must be **stamped with the prompt+model version** it was given against — else a correction on an old version pollutes evaluation of a new one.

**Design decision (D-4 / governance): advisory vs. approval gate:**
- **Advisory** — AI output goes to agent directly; feedback improves it later.
- **Approval gate** — coach must review/approve before agent sees it.
- Differs in trust posture + coach workload. Depends on AFNI's initial trust in the AI.

**Open questions:** advisory vs. approval (D-4); SME certification capacity vs. feedback volume; feedback store shared with golden store or separate. *Pending.*

---

## 8. Governance & Audit

**What it is:** the control layer over everything else — who can change what, who approves, and a record of every change and AI decision.

**A. Change governance** (controls on prompt/model/task/threshold changes):
- **Who can edit** vs. **who approves** before prod (segregation of duties).
- Approval workflow — a change passes the **eval gate AND a human approver** before activation.
- This is where **D-4** lands (approval ownership).

**B. Audit trail** (the record):
- Every **AI decision** traceable to the prompt + model + data version that produced it.
- Every **change** logged (who / what / when / why).
- Every **access + guardrail decision** logged.
- APIX: agent disputes a score → reconstruct exactly how it was generated. The compliance/dispute backbone.

**Where it lives:**
- Audit log → **Azure SQL** (queryable) + compliance viewer UI.
- Change approvals → in the registry / CI-CD flow.
- Access control → **Entra ID + RBAC** (admin / coach / manager / QA / agent).

**Why it's not optional for AFNI:** BPO handling insurance/customer data — "show me why the AI said this" and "who changed this prompt" are compliance questions. Ties to RAI pillars (transparency, accountability, human oversight).

**Risks & mitigations:**

| Risk | Mitigation |
|---|---|
| Audit gaps (capability bypasses the seam) | Enforce logging centrally through shared seam |
| Over-restrictive approval slows improvement | Tiered approval (minor prompt tweak vs. model swap) |

**Open questions:** approval ownership + tiers (D-4); audit retention period (ties to D-1 compliance). *Pending.*

---

## Priority of the 8 Core Capabilities (for APIX go-live)

| Tier | Capability | Why |
|---|---|---|
| **P0 — can't run prod without** | Guardrails, Observability, Prompt Mgmt, Model Mgmt | PII compliance, cost visibility, versioned no-redeploy changes, call resolution |
| **P1 — trust/quality at launch** | Evaluation Gate, Governance & Audit | Safe changes, defensible scores, "why did AI say this" |
| **P2 — matures with usage** | Feedback Loop | Needs live coaches/data; grows golden set over time |

---

## 9. Additional Capabilities (Least Priority — revisit later)

Beyond the 8, genuinely LLMOps-specific but lower priority for first APIX cut:

1. **Rate limiting / quota / throughput** — Azure OpenAI TPM/RPM caps are a real bottleneck at 100% call volume. Needs batching, retry/backoff, possibly provisioned throughput. *(Borderline P0 due to APIX volume — watch this one.)*
2. **Cost / FinOps control** — beyond observing cost: budgets, spend caps, per-program chargeback. Observability *sees* cost; FinOps *controls* it.
3. **Caching** — prompt + response caching; big cost + latency lever at APIX volume.
4. **Deployment / release & environment promotion** — formal dev→test→prod promotion strategy beyond basic CI/CD.
5. **Incident response / rollback / kill-switch** — pull a misbehaving model fast in prod (RAI requirement).
6. **Model/prompt deprecation lifecycle** — retire old versions cleanly without breaking audit trails.

---

## 10. Patterns & Frameworks (for manager presentation)

LLMOps is the operating discipline; **patterns** are *how you build the app*, **frameworks** are *what you build it with*. LLMOps wraps all of them.

### The 3 patterns

| Pattern | What it's for | When to use |
|---|---|---|
| **RAG** | Ground answers in AFNI's own knowledge (policies, playbooks, call history) | Q&A, knowledge assist, citations needed |
| **Agent** | One LLM that calls tools / takes actions | Chatbot NL→SQL, dynamic tool use |
| **Multi-Agent** | Several specialized agents coordinated | Complex reasoning, fan-out/orchestrated tasks |

### Pattern → framework mapping

| Pattern | Framework |
|---|---|
| RAG | Azure AI Search + Azure OpenAI |
| Agent | Azure Foundry Agents |
| Multi-Agent | LangGraph + Azure Foundry Agents |

### Applying to APIX — recommendation

- **Core pipeline (denoise → analysis → summary):** keep as **deterministic orchestrated tasks**, NOT agents. It's fixed, sequential, batch. Agentifying adds nondeterminism, latency, cost, and hurts eval reproducibility — no functional gain.
- **Chatbot:** genuine **Agent** use case (NL→SQL, tool calls, multi-turn). Use Foundry Agents here.
- **Multi-Agent / LangGraph:** position as the path for **future use cases** needing coordinated reasoning — not retrofitted onto APIX batch scoring.

### "Convert AI pipeline agents into Foundry agents — is it good?"
**Not for the core pipeline.** Foundry Agents shine on autonomy/tool-calling/dynamic decisions. A fixed pipeline wants reproducible, testable, cheap task execution. Use agents where autonomy is real (chatbot), keep orchestration where flow is fixed (pipeline).

**Key message:** choose pattern by the *problem*, not by the trend. LLMOps governance (the 8 capabilities) applies regardless of pattern.

---
