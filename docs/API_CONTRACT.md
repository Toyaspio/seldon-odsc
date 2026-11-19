# API contract

`GET /api/health` returns service status and mode. `GET /api/companies` returns recommendations only. `POST /api/research` accepts a natural-language `query`, optional `companies`, and optional `as_of_date`; it returns a task ID and initial `pending` state. `GET /api/research/{task_id}` returns progress and a report ID when ready. `GET /api/reports/{report_id}` returns the report.

Task states: `pending`, `researching`, `verifying`, `completed`, `partial`, `failed`. Mock results are `partial` because they have no verified financial evidence. Unknown IDs return HTTP 404; invalid request data returns HTTP 422.

Phase 2 preserves the five routes, request shapes and report schema requirements. During polling, `current_step` can include `planning`, `resolving_companies`, `retrieving`, `checking_evidence`, `follow_up` and `building_report`. Render unknown step strings with a generic label. Fetch a report when a `completed` or `partial` task has a `report_id`.

Optional source and claim fields expose `period_start`, `accounting_basis`, and `actual_or_guidance`. Sources may have `structured_locator` and `issuer_name`. Optional `claim_type` distinguishes disclosed facts, calculations, analysis, scenarios and evidence gaps. Calculated metrics continue to use the existing metric shape. A calculation may cite multiple disclosed inputs. Resolve citations by exact source ID, never by display order. A search lead may have a URL and excerpt without supporting any numeric claim.

`openapi.yaml` and `report.schema.json` are generated from backend models. Regenerate with `python generate_contracts.py` after approved model changes.
