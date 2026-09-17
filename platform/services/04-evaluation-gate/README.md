# Evaluation Gate

## What this is
Runs a golden dataset's cases through whatever is being changed — a model swap, a prompt edit, a pipeline change — and decides pass or fail. This is the mechanism every "reviewed, gated change" reference elsewhere in this platform (ADR 0001, 0004, 0005, 0006, 0007) has been pointing at: it's what CI/CD (09) will eventually call before allowing a deploy.

**Current scope: a library, not a deployed service or a running CI check.** No trigger exists yet — CI/CD (09) doesn't exist. This component is buildable and provable on its own now; wiring it into an actual pipeline gate is future work, same posture as Orchestration (ADR 0005).

## Golden datasets are usecase-owned content
Same story as Model Management's aliases and Prompt Management's prompts: the *mechanism* (dataset loading, evaluators, gate aggregation) is platform code; a golden dataset's actual cases are usecase-owned content, living in that usecase's own repo, not here. `tests/fixtures/usecase_demo/golden_dataset.jsonl` stands in for that, the same pattern used in components 02 and 07.

Dataset format — one JSON object per line:
```json
{"id": "sentiment_001", "input": {"message": "This is broken"}, "expected": "negative", "evaluator": "exact_match"}
{"id": "format_001", "input": {"message": "give me json"}, "output_schema": {...}, "evaluator": "schema"}
{"id": "tone_001", "input": {"message": "Where's my refund?"}, "rubric": "Reply must acknowledge frustration and offer escalation", "evaluator": "llm_judge"}
```
Each case declares which evaluator it wants — explicit per case, never auto-detected from the case's shape.

## Three evaluators
- **`ExactMatchEvaluator`** — actual output equals expected value exactly. Cheapest; use for deterministic cases (classification labels, structured fields).
- **`SchemaEvaluator`** — validates actual output against a JSON schema. The first real consumer of Prompt Management's `output_schema` field, reserved but unused since ADR 0006.
- **`LLMJudgeEvaluator`** — resolves the `judge` alias (already reserved in Model Management's `models.yaml`) and asks it to score actual output against a rubric, for anything not exact-match-able (tone, completeness, whether escalation was offered). Parses a single-line `"PASS: <reason>"` / `"FAIL: <reason>"` verdict rather than depending on the judge model reliably producing structured JSON.

Not built: a semantic-similarity/embedding-based evaluator. The three above cover what's been asked for; a fourth evaluator is a small, additive change if a usecase needs one later.

## The gate itself
`EvaluationGate.run(usecase, cases, system_under_test)` runs every case through `system_under_test` — a plain callable (`EvalCase -> Any`), not a dependency on Orchestration's `Pipeline` type. A caller wraps `pipeline.run(...)` in a lambda if that's what's being tested, or passes something narrower (a single prompt render, a single model call) — this component doesn't need to know which. Each result is scored by the evaluator its case declares, then aggregated into a `GateResult` (`passed`, `pass_rate`, `threshold`, per-case `results`).

Threshold defaults to **100%** — every case must pass — on the assumption that golden-dataset cases are curated, critical cases, not a representative sample. `config/gates.yaml` lets a usecase override to something softer per environment. See `docs/decisions/0008-evaluation-gate-scope.md` for why the default is strict rather than a percentage.

## File layout
```
config/
└── gates.yaml                        # usecase -> environment -> pass_threshold override

src/evaluation_gate/
├── types.py                          # EvalCase, EvalResult, GateResult, UnknownEvaluatorError
├── dataset_loader.py                  # load_dataset(path) -> list[EvalCase]
├── model_client.py                    # provider factory bridging component 03, for the judge evaluator
├── gate.py                            # EvaluationGate — runs cases, aggregates a GateResult
└── evaluators/
    ├── base.py                        # Evaluator protocol
    ├── exact_match.py
    ├── schema_evaluator.py
    └── llm_judge.py

tests/
├── fakes.py                           # FakeJudgeProvider
├── fixtures/usecase_demo/golden_dataset.jsonl   # demo dataset, stands in for usecase-owned content
├── test_dataset_loader.py
├── test_exact_match_evaluator.py
├── test_schema_evaluator.py
├── test_llm_judge_evaluator.py
└── test_gate.py                       # end-to-end: aggregation, threshold behavior, unknown-evaluator error
```

## Prerequisites
- Component 03 (Model Management) present as a sibling folder — `LLMJudgeEvaluator` resolves the `judge` alias through it

## Local development
```bash
pip install -r requirements.txt
pytest
```
Every test runs against `FakeJudgeProvider` — no Azure credentials, no network call.

Importable as the `evaluation_gate` package, per `docs/decisions/0004-python-package-naming.md`.

## Azure resources used
None. This component provisions nothing of its own — it calls Model Management's already-provisioned `judge` deployment.

## Future CI/CD integration
Not built yet — documented now so the plan exists ahead of the work, same posture as Orchestration's "Future Deployment Path" (ADR 0005).

1. CI/CD (component 09) runs `EvaluationGate.run(...)` as a pipeline step whenever a model config, prompt file, or pipeline definition changes.
2. A failing `GateResult` blocks the deploy; a passing one lets it proceed — this is the literal mechanism behind "a model swap is a reviewed, gated change" (Model Management's README) and the equivalent language in Prompt Management's and Orchestration's READMEs.
3. `GateResult`'s per-case `results` are surfaced in the CI check output so a failure points at the specific case(s) that regressed, not just a pass/fail bit.

## Dependencies
- Depends on: component 03 (judge model resolution)
- Depended on by: CI/CD (09, once it exists); any usecase or component wanting to gate a change before shipping it

## Cost notes
No fixed cost. Running the gate costs whatever the `judge` alias's token usage is for the cases evaluated with `llm_judge` — `exact_match` and `schema` are free (no model call).
