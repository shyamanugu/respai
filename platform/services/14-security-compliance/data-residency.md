# Data Residency

## Current state
Every component's Bicep defaults `location` to `eastus` (see `docs/decisions/0003-model-management-scope.md`, point 3). This is a **default, not a data residency guarantee** — it was chosen because AFNI's client base spans multiple regions and no single region was ever the right universal choice, not because `eastus` satisfies any specific client's contractual requirement.

## The gap this component surfaces, doesn't solve
No usecase or client has specified a data residency requirement yet, so none has been built. If a BPO client's contract requires data to stay in a specific geography (EU, UK, APAC, etc.), that requirement is **not currently enforced anywhere in this platform** — every component would need its `location` parameter overridden per-deployment for that client, and for components with per-client isolation already built (Data & Tools' per-client Search index, ADR 0007), the index's region would need to match the *service's* region, which is currently one shared region for everyone.

## What per-client region override would actually require
- Model Management (03): `location` parameter override per environment/client — already parameterized, just needs a value other than the default
- Data & Tools (07): the shared Azure AI Search service (ADR 0007) is one region for all clients today — a client requiring a different region would need either a second regional Search service (breaking the "one shared service, N indexes" cost model) or accepting that client's data sits in the shared service's region regardless of their preference
- Every other component: same pattern — parameters exist, values are not yet client-aware

This is deliberately not built now, per the same reasoning as ADR 0003's original deferral: building a per-client region override mechanism before any client's actual contract requires it risks guessing at the wrong shape (per-client parameter? separate resource group per region? separate subscription?).

## Revisit when
A specific client contract specifies a data residency requirement. At that point, design the override starting from Model Management's already-parameterized `location`, and treat Data & Tools' shared-Search-service assumption (ADR 0007) as the first thing to re-examine — it's the component most structurally committed to "one region for everyone."
