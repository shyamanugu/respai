# Pipeline Runbook — Running Each Step for a Given Week

This guide explains how to run the AI pipeline **one step at a time** for a single
week, using the `--program pso` (VZ Mobile service) example. The same commands
work for any program (`telesales`, `wcc`, `pso`) or mode (`--mode ...`).

---

## 1. Concepts

The pipeline has 5 steps, run in this order:

| # | Step                 | Granularity | Reads                          | Writes                                   |
|---|----------------------|-------------|--------------------------------|------------------------------------------|
| 1 | `denoise`            | Per **day** | raw transcripts                | denoised transcripts (`<date>.parquet`)  |
| 2 | `analysis`           | Per **day** | denoised transcripts           | per-call evaluations (`<date>.parquet`)  |
| 3 | `summary`            | Per **week**| 7 days of analysis             | per-employee JSON reports (`<date>/<emp>.json`) |
| 4 | `individual_metrics` | Per **week**| Azure SQL                      | per-employee coaching metrics            |
| 5 | `kpi`                | Per **week**| per-employee JSON reports      | flattened KPI CSV                        |

### Date semantics (important)

- **`denoise` and `analysis` are per-day.** You run them once for **each of the
  7 days** in the target week.
- **`summary`, `individual_metrics`, and `kpi` are weekly.** You run them **once**,
  passing the **week-ending date** (`--date`). The `summary` step automatically
  looks back 7 days from that date (`summary_lookback_days = 7`).

> Example week: **2026-08-10 (Mon) → 2026-08-16 (Sun)**.
> Here the week-ending date is **2026-08-16**.

---

## 2. Prerequisites

Run everything from the repository root, using the pipeline virtual environment.

```powershell
cd C:\Work\APIX\code\AFNI\Afni-Performance-Intelligence-Index

# Activate the venv (PowerShell)
.\ai_pipeline\pipeline_venv\Scripts\Activate.ps1
```

Confirm `.env` maps the program name to its mode:

```
PSO_PROGRAMS="VZ Mobile service"
```

### Coach enrichment (Azure SQL) — optional but recommended

`denoise` and the coach inventory attach **Coach ID / Coach Name** to each call by
looking up the agents present in the file against Azure SQL
(`[mdm].[dim_angel]` → `[mdm].[dim_employee_hcht]`, joined on
`agent_pbxid = SourceID`). Only the agents in the file are queried — no full-table
scan — and the active coach is resolved by the call's date window.

Set these env vars so the lookup can run (uses `DefaultAzureCredential`). The
pipeline **reuses the application's `APP_AZURE_SQL_*` variables** — if your
`application/.env` already has them, just copy the block into `ai_pipeline/.env`:

```
APP_AZURE_SQL_SERVER=asqldweap.database.windows.net
APP_AZURE_SQL_DATABASE=asqldweap
APP_AZURE_SQL_PORT=1433
APP_AZURE_SQL_DRIVER=ODBC Driver 18 for SQL Server
APP_AZURE_SQL_LOGIN_TIMEOUT=90
APP_AZURE_SQL_QUERY_TIMEOUT=0
```

> Pipeline-specific `AI_PIPELINE_AZURE_SQL_*` variables take precedence when set;
> otherwise the `APP_AZURE_SQL_*` values above are used. Requires the `pyodbc`
> package and the *ODBC Driver 18 for SQL Server* (both already in
> `ai_pipeline/requirements.txt`).
>
> If none are set, enrichment is skipped gracefully: Coach ID / Coach Name show
> `-` and denoise processes **all** coaches. Use `--agent` (EmployeeID) to scope
> instead in that case.

---

## 3. Run each step, one by one

Set the week once, then run the steps in order.

```powershell
# Week-ending date (last day of the 7-day window)
$WEEKEND = "2026-08-16"

# The 7 days in the week (used by the per-day steps)
$DAYS = @(
    "2026-08-10","2026-08-11","2026-08-12","2026-08-13",
    "2026-08-14","2026-08-15","2026-08-16"
)
```

### Step 1 — Denoise (per day)

```powershell
foreach ($d in $DAYS) {
    python -m ai_pipeline.main --program pso --step denoise --date $d
}
```

### Step 2 — Analysis (per day)

```powershell
foreach ($d in $DAYS) {
    python -m ai_pipeline.main --program pso --step analysis --date $d
}
```

### Step 3 — Summary (once, week-ending date)

```powershell
python -m ai_pipeline.main --program pso --step summary --date $WEEKEND
```

### Step 4 — Individual metrics (once, week-ending date)

```powershell
python -m ai_pipeline.main --program pso --step individual_metrics --date $WEEKEND
```

### Step 5 — KPI aggregation (once, week-ending date)

```powershell
python -m ai_pipeline.main --program pso --step kpi --date $WEEKEND
```

---

## 4. Shortcut — run per-day steps as a date range

Instead of the `foreach` loops, `denoise` and `analysis` accept a date range:

```powershell
# Denoise + analysis for the whole week in one command each
python -m ai_pipeline.main --program pso --step denoise  --start 2026-08-10 --end 2026-08-16
python -m ai_pipeline.main --program pso --step analysis --start 2026-08-10 --end 2026-08-16

# Then the weekly steps (single week-ending date)
python -m ai_pipeline.main --program pso --step summary            --date 2026-07-24
python -m ai_pipeline.main --program pso --step individual_metrics --date 2026-08-16
python -m ai_pipeline.main --program pso --step kpi                --date 2026-08-16
```

---

## 5. Run the full pipeline (all steps) for the week

Omit `--step` to run all steps in order. For per-day steps this processes each day
in the range; the weekly steps run per date processed.

```powershell
# Full pipeline for each day of the week
python -m ai_pipeline.main --program pso --start 2026-08-10 --end 2026-08-16
```

> For a clean weekly run, prefer running the per-day steps over the range
> (Section 4), then the weekly steps once on the week-ending date.

---

## 6. Mode-based runs (multiple programs at once)

Use `--mode` to filter by the program names mapped in `.env`. Combine modes with `|`.

```powershell
python -m ai_pipeline.main --mode pso --step summary --date 2026-08-16
python -m ai_pipeline.main --mode "telesales|wcc|pso" --step analysis --date 2026-08-16
```

---

## 7. Coach / agent inventory (who is in the data)

Before running denoise for specific coaches, use the inventory utility to list the
coaches (or agents) present in the raw transcripts for a day, week, or date range.
It shares the **same date semantics** as the pipeline (`--date`, `--start/--end`,
plus a `--week` shortcut). `--program` / `--mode` are **optional** — omit both to
inventory **all programs** at once.

Coach ID / Coach Name are fetched from Azure SQL for exactly the agents in the
file (see *Coach enrichment* in Section 2). When the SQL env vars are not set,
both columns show `-`.

```powershell
# All programs (omit --program/--mode)
python -m ai_pipeline.utils.coach_inventory --start 2026-07-18 --end 2026-07-24

# Single program
python -m ai_pipeline.utils.coach_inventory --program wcc --date 2026-08-16

# 7-day window ending on --date (matches the summary lookback)
python -m ai_pipeline.utils.coach_inventory --program wcc --date 2026-08-16 --week
```

Output is a final table with one row per **(Program, Coach)**:

```text
Program Name  Coach ID  Coach Name  Unique Agents  Total Conversations
```

- **Coach ID / Coach Name** = resolved from Azure SQL for the agents in the file.
- **Unique Agents** = distinct `EmployeeID`s under that coach.
- **Total Conversations** = all calls handled by those agents.
- When Azure SQL is not configured (or no coach matches), Coach ID / Coach Name
  show `-` (rows are then per-program).

```powershell
# Print ONLY the id list — CoachIDs if present, else EmployeeIDs — to pipe on
python -m ai_pipeline.utils.coach_inventory --program wcc --date 2026-08-16 --ids-only

# Write the JSON report (local file and/or coach-hierarchy container)
python -m ai_pipeline.utils.coach_inventory --program wcc --week --date 2026-08-16 --write --out inventory.json
```

---

## 8. Denoise for a single, multiple, or all coaches

`denoise` accepts a `--coach` allow-list so you can process **one coach**, a
**set of coaches**, or **all coaches**. After the raw file is read, each call is
enriched with its `CoachID` from Azure SQL (agents in the file only), then the
allow-list is applied **in-memory** before any LLM calls — so no denoise work is
wasted on coaches you filtered out.

> Requires the `APP_AZURE_SQL_*` env vars (Section 2). Without them the
> `CoachID` cannot be resolved, so `--coach` matches nothing — use `--agent`
> (EmployeeID) instead.

```powershell
# Single coach
python -m ai_pipeline.main --program pso --step denoise --start 2026-07-18 --end 2026-07-24 --coach 9043143

# Multiple coaches (comma-separated, no spaces)
python -m ai_pipeline.main --program pso --step denoise --start 2026-08-10 --end 2026-08-16 --coach 9043143,3000510

# All coaches (either omit --coach, or pass 'all')
python -m ai_pipeline.main --program pso --step denoise --start 2026-08-10 --end 2026-08-16 --coach all
python -m ai_pipeline.main --program pso --step denoise --start 2026-08-10 --end 2026-08-16
```

Chain the inventory list directly into denoise for the whole set of coaches found:

```powershell
$COACHES = python -m ai_pipeline.utils.coach_inventory --program pso --date 2026-08-16 --ids-only
python -m ai_pipeline.main --program pso --step denoise --start 2026-08-10 --end 2026-08-16 --coach $COACHES
```

> **No Azure SQL configured** (coach can't be resolved): use `--agent` with
> `EmployeeID`s instead. Same single / comma-separated / `all` semantics.
>
> ```powershell
> python -m ai_pipeline.main --program telesales --step denoise --start 2026-07-18 --end 2026-07-24 --agent 12345
> python -m ai_pipeline.main --program telesales --step denoise --start 2026-07-18 --end 2026-07-24 --agent all
> ```

---

## 9. Verifying output

- **Analysis:** check `analysis` container for `<date>.parquet` with `analysis_status = 'ok'`.
- **Summary:** check the `summary` container for `<week-ending-date>/<EmployeeID>.json`.
  For PSO, each report includes a `kpi_groups` block (Customer Experience, Resolve,
  Quality, Customer Care Handling, Operational Efficiency, Repeat Contact Risk),
  plus `behavior_scores`, `call_handling_and_soft_skills`, `escalations`,
  `key_improvements`, and `coaching_tips`.
- **KPI:** check the uploaded CSV for the week; PSO KPIs are grouped under the
  `PSO ...` group names.

---

## 10. Quick reference

```text
Per-day steps  (run for each of the 7 days):  denoise, analysis
Weekly steps   (run once, week-ending date):  summary, individual_metrics, kpi

python -m ai_pipeline.main --program <pso|telesales|wcc> --step <step> --date YYYY-MM-DD
python -m ai_pipeline.main --program <...> --step <denoise|analysis> --start YYYY-MM-DD --end YYYY-MM-DD
python -m ai_pipeline.main --mode <mode[|mode...]> --step <step> --date YYYY-MM-DD

# Coach / agent scope on denoise
python -m ai_pipeline.main --program <...> --step denoise --start YYYY-MM-DD --end YYYY-MM-DD --coach 9040400
python -m ai_pipeline.main --program <...> --step denoise --start YYYY-MM-DD --end YYYY-MM-DD --coach 9040400,3000510
python -m ai_pipeline.main --program <...> --step denoise --start YYYY-MM-DD --end YYYY-MM-DD --coach all
python -m ai_pipeline.main --program <...> --step denoise --start YYYY-MM-DD --end YYYY-MM-DD --agent all   # no CoachID

# Inventory (who is in the data) — program/mode optional; omit for ALL programs
python -m ai_pipeline.utils.coach_inventory --start YYYY-MM-DD --end YYYY-MM-DD      # all programs
python -m ai_pipeline.utils.coach_inventory --program <...> --date YYYY-MM-DD
python -m ai_pipeline.utils.coach_inventory --program <...> --date YYYY-MM-DD --week
python -m ai_pipeline.utils.coach_inventory --program <...> --date YYYY-MM-DD --ids-only
# Table columns: Program Name | Coach ID | Coach Name | Unique Agents | Total Conversations
# Coach ID / Coach Name need APP_AZURE_SQL_* (else shown as -)
```
