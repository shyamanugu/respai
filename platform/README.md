# LLMOps Platform

Reusable, Azure-first LLMOps libraries. Consumed via **PYTHONPATH src-layout** — not
installed as wheels — so the reusability acceptance test holds: **a usecase edits
zero files under `platform/services/**/src`.**

## Wiring the platform

Load `bootstrap.py` by file path (it appends each service's `src/` to `sys.path`),
then import services by their bare package names:

```python
import runpy, pathlib
runpy.run_path(pathlib.Path("platform/bootstrap.py"))   # or the app's llmops helper

from model_management.model_router import resolve
from guardrails.builder import build_guardrail
from observability.tracer import NullTracer
from observability.cost import compute_cost
```

Set `LLMOPS_PLATFORM_ROOT` to point at this `platform` dir (or its `services/`) when the
tree is not at its default location — e.g. inside containers. The directory is named
`platform`, which shadows the stdlib `platform` module, so **never** `import platform.bootstrap`.

## Services

Real, importable libraries: `02-prompt-management` (`prompt_management`),
`03-model-management` (`model_management`), `04-evaluation-gate` (`evaluation_gate`),
`05-observability` (`observability`), `06-guardrails` (`guardrails`),
`07-data-tools` (`data_tools`), `08-orchestration` (`orchestration`),
`10-serving-hosting` (`serving`), `11-feedback` (`feedback`).

IaC / policy only (no Python API): `01-repo-foundation`, `12-finops`,
`14-security-compliance` — their bicep is harvested into `../infra/`.

## Test status (vendored as-is)

Running each service's own suite: **75 pass / 7 fail**. The 7 failures are
**pre-existing in the upstream source** (identical results there) — the shipped
example `models.yaml` sets `reason`/`bulk` to `null` (fail-open for usecases) and
`pricing.yaml` carries real placeholder rates, which a few of the platform's own
example tests still assert against the old values. They are **not** caused by this
vendoring and do not affect any APIX-relevant behavior (fail-open `resolve`,
`build_guardrail`, `compute_cost` all verified working). The `src` is intentionally
left untouched to preserve the zero-edit reusability contract.
