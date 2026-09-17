# Prompt Management

## What this is
Loads, versions, and renders prompt templates by name, and resolves a small
set of shared text fragments that any usecase can compose into its own
prompts. It is the mechanism that lets a prompt be treated the same way
Model Management (03) treats a model: something the platform resolves by a
stable name, never a string baked directly into application code.

## Why not Azure AI Foundry prompt assets
Foundry prompt flow gives a UI, built-in versioning, and evaluation hooks —
the natural Azure-native choice. Standing it up requires Foundry project
RBAC (hub connections, project-level role assignments) that Contributor-only
access doesn't grant, the same gap already documented for Key Vault
(`docs/decisions/0001-repo-foundation-approach.md`) and voice infrastructure
(`docs/decisions/0003-model-management-scope.md`).

Git-backed YAML files instead give full version history, pull-request review,
and diffing for free — arguably a better fit for prompts that should be
change-controlled the same way code is. See
`docs/decisions/0006-prompt-management-git-backed-storage.md` for the
Foundry swap path once the access gap closes.

## The reusability problem this solves
Prompt *text* is inherently usecase-specific — usecase A and usecase B need
different wording. What's reusable is the *mechanism* around that text:

- **Loading and rendering** (`src/prompt_management/`) — one registry class
  that loads `*.yaml` files from whatever directories it's given, validates
  that every variable a template declares gets supplied before rendering,
  and expands shared fragments. This code has no knowledge of any specific
  usecase.
- **Shared fragments** (`prompts/shared/`) — common building blocks (a
  safety preamble, a JSON-output instruction) that any usecase composes into
  its own templates via a `{{fragment:name}}` token, instead of every
  usecase re-writing the same phrasing. This is the one place where actual
  *content*, not just mechanism, is genuinely reusable.

Onboarding usecase #2's prompts means pointing a new `PromptRegistry` at that
usecase's own prompt folder — nothing in this component changes. `tests/`
proves this by resolving prompts from `tests/fixtures/usecase_demo/`, a
folder that stands in for a real usecase's own repo, deliberately kept
outside this component's `prompts/` folder.

## Prompt file format
```yaml
name: classify_sentiment
version: 1
description: Classifies customer message sentiment for triage
model_capability: nano        # an alias from Model Management's models.yaml — never a model name
input_variables: [message]
output_schema: null            # reserved for Evaluation Gate (04); unused until it exists
template: |
  Classify the sentiment of this customer message as positive, negative, or neutral.
  Message: {message}
```
`version` is a plain field, not a separate file per version — git history is
the real audit trail. Per-environment version pinning (the way
`models.yaml` pins a model per environment) is deliberately not built yet;
there's no evaluation mechanism to justify choosing between versions. See
"Revisit When" in ADR 0006.

Templating is plain `{variable}` substitution, not a templating engine —
every declared `input_variable` is a literal find-and-replace token. This
was chosen over Jinja2 to avoid a dependency that today's prompts (no
loops, no conditionals) don't need. If a usecase genuinely needs
conditional logic in a prompt, that's the trigger to reopen this choice.

## File layout
```
src/prompt_management/
├── types.py       # PromptSpec, FragmentSpec, and the errors the registry raises
├── loader.py       # parses one YAML file into a spec — no directory scanning
└── registry.py      # PromptRegistry: loads directories, resolves by name, renders + expands fragments

prompts/shared/
├── safety_preamble.yaml            # opt-in fragment, not an enforced guardrail
└── json_output_instruction.yaml     # opt-in fragment for machine-parseable output

tests/
├── fixtures/usecase_demo/prompts/   # stands in for a real usecase's own prompt folder
└── test_registry.py                 # proves resolution, rendering, fragment expansion, error paths
```

## Local development
```bash
pip install -r requirements.txt
pytest
```
Everything here reads local files — no Azure credentials, no network call.

Importable as the `prompt_management` package, per the naming convention in
`docs/decisions/0004-python-package-naming.md`.

## Integration with Orchestration (08)
`ModelStep` now accepts either a raw `prompt_template` string (unchanged, for
quick one-off steps) or `prompt_name` + `prompt_registry` (resolves through
this component). Exactly one must be set — see `08-orchestration/src/orchestration/step.py`.
This closes the seam that component's README originally left open for
"Prompt source."

## Path to Azure AI Foundry (once RBAC allows it)
`PromptRegistry` is a concrete class today, not yet an interface, because
there is only one implementation. If Foundry prompt assets become available
later, the swap is: introduce a `PromptSource` protocol with the same
`resolve`/`render` shape, keep this file-backed registry as one
implementation, add a Foundry-backed one, and let `models.yaml`-style config
pick which backend an environment uses — the same pattern already used for
`ModelProvider` in Model Management (03). Nothing in Orchestration would need
to change.

## Dependencies
- Depends on: nothing (no other platform component is a prerequisite)
- Depended on by: Orchestration (08, renders prompts before calling a model), eventually Evaluation Gate (04, once `output_schema` is enforced)

## Cost notes
None — this component only reads local YAML files. No Azure resource is
provisioned or planned for it while it remains git-backed.
