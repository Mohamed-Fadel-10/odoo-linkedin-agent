# Project conventions

## 1. General
- Python 3.14.
- Type hints on functions and methods.
- Pydantic for models and input validation.
- Prefer simple, readable code over extra layers.
- Check that new dependencies support Python 3.14 before adding them.

## 2. Data sources
- Do not scrape LinkedIn or pull restricted LinkedIn data.
- Allowed sources in this version: Hunter API and local CSV files.
- Hunter gives domain contact data; it does not guarantee someone still works there.
- A valid email is not proof of current employment.
- Do not invent missing employee fields.
- Keep source details and dates when the provider sends them.
- Leave missing optional fields as `None`.

## 3. Architecture
- Providers share a common interface.
- CSV is the main provider during development; Hunter is the live external source.
- Keep business rules in the pipeline, not in Hermes prompts.
- Normalization, validation, mapping, and deduplication should be plain deterministic code.
- All Odoo API traffic goes through `odoo/client.py`.
- MCP tools are thin wrappers around existing pipeline functions.
- Hermes handles conversation flow only; it should not reimplement processing or import logic.
- MCP surface is limited to:
  - `find_employees`
  - `import_contacts`
  - `get_report`

## 4. Data processing
- Handle each record on its own; one failure must not stop the rest.
- Normalize and validate before import.
- Email is required to create a contact; phone and location are optional.
- Normalize emails and LinkedIn URLs before comparing them.
- Deduplicate by email first, then LinkedIn URL.
- Same name + company is a possible duplicate and needs review.
- Do not auto-update or auto-delete existing Odoo contacts.
- Report every skipped or failed record with a clear reason.

## 5. Odoo
- Keep Odoo calls inside `odoo/client.py`.
- Use the API that matches the installed Odoo version.
- Use least-privilege credentials.
- Look up an existing company before creating a new one.
- Relational fields (`parent_id`, `country_id`, …) must use real Odoo IDs.
- Do not expose raw Odoo RPC through MCP.
- First version is create-only (no update/delete tools).

## 6. Import safety
- Dry-run must not write to Odoo.
- Real import needs explicit user approval for a specific `run_id` and selected records.
- Preview and import must use the same saved run snapshot.
- Reject unknown or already-processed selections.
- Re-runs should be idempotent.
- Serialize concurrent imports for the same run so duplicates are not created.
- A previous dry-run alone is not approval.

## 7. MCP and Hermes
- Validate MCP tool inputs.
- Do not send Odoo credentials or API keys to Hermes.
- Do not put secrets in tool outputs, prompts, or logs.
- Show the preview before asking for approval.
- Do not run a real import without explicit approval.
- Surface errors clearly; avoid endless retries.

## 8. Security and config
- Local secrets live in `.env` only.
- Commit `.env.example` with placeholders.
- Ignore `.env`, venvs, caches, logs, and local DBs in git.
- Do not commit real API keys or real personal contact data.
- Validate external URLs and inputs.
- Do not log auth headers or secret values.

## 9. Working on the repo
- Finish the current scoped change before starting unrelated work.
- Check what is already installed before adding tools.
- Avoid changing Hermes global config or other MCP servers unless needed.
- Keep commits focused; describe what changed and why.
