# odoo-linkedin-agent

Find company contacts with the Hunter API, process them in Python, preview an import, then create **Accepted** contacts in Odoo after the user approves. Hermes can drive the MCP tools; a Chrome extension talks to a local FastAPI backend for the same flow from a LinkedIn company tab URL.

## Solution overview

```text
LinkedIn company URL (tab only)
        │
        ▼
 Chrome extension ──HTTP──► local FastAPI backend (127.0.0.1)
        │                         │
        │                         ├── Hermes (`hermes -z`) → MCP tools
        │                         │         │
        │                         │         ▼
        │                         │   find_employees / import_contacts / get_report
        │                         │         │
        │                         ├── Python dry-run preview + confirmation token
        │                         └── Approve → real create-only Odoo import
        ▼
   PostgreSQL (runs, run_records, import_attempts, discovery_jobs)
        ▲
 Hunter API ──► normalize/validate/dedupe ──► Odoo JSON-2 (lookups + create)
```

**MCP tools:** `find_employees`, `import_contacts`, `get_report`. Processing and import rules are implemented in Python.

**Also in Python:** Hunter mapping, normalization, validation, deduplication, Odoo client, run storage, confirmation tokens, import safety, and the extension backend.

## Data source and permissions

- Live source: Hunter domain search.
- No LinkedIn scraping; the extension has no LinkedIn host permission.
- The extension reads the active tab URL only (`activeTab` + `storage`), not page content.
- A valid email does not mean the person still works there; missing optional fields stay empty/`None`.
- Dry-run does not write to Odoo. Real import only creates new contacts (no update/delete).

## Prerequisites

- Python 3.14
- PostgreSQL
- Odoo with JSON-2 API access (API key)
- Hunter API key (discovery spends credits)
- Hermes CLI on `PATH` (or set `HERMES_BIN`)
- Chrome (for the extension)

## Quick setup

Run every command from the **project root** (the folder that contains this README).

### 1. Python environment

**Windows (PowerShell):**

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

**macOS / Linux:**

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` with your secrets (never commit `.env`).

### 2. PostgreSQL

```sql
CREATE DATABASE odoo_agent;
```

Set `DATABASE_URL` in `.env` so the path ends with `/odoo_agent` (example: `postgresql://USER:PASSWORD@localhost:5432/odoo_agent`).

Then create tables:

```powershell
# Windows
.\.venv\Scripts\python.exe scripts\init_db.py
```

```bash
# macOS / Linux
.venv/bin/python scripts/init_db.py
```

`init_db.py` refuses any database name other than `odoo_agent`. It does not create/drop databases or touch Odoo’s DB. The backend also calls `ensure_schema` on start.

### 3. Odoo addon

Install the addon under `odoo/addons/linkedin_contact_extension` on your Odoo instance (adds `res.partner.x_linkedin_url`). Restart Odoo / update the app list, then install/upgrade the module.

### 4. Environment variables

| Variable | Purpose |
|----------|---------|
| `HUNTER_API_KEY` | Hunter domain-search key |
| `HUNTER_LIMIT` | Domain Search `limit` (1–100, default `10`; free plans often need ≤10) |
| `ODOO_URL` | Odoo base URL |
| `ODOO_DATABASE` | Odoo database name |
| `ODOO_API_KEY` | Odoo JSON-2 bearer token |
| `DATABASE_URL` | App DB (`…/odoo_agent`) |
| `CONFIRMATION_TOKEN_SECRET` | HMAC secret for import tokens (auto-written if missing) |
| `EXTENSION_HTTP_PORT` | Local backend port (default `8765`) |
| `EXTENSION_ID` | Chrome unpacked extension ID |
| `EXTENSION_PAIRING_TOKEN` | Shared secret header for the extension (auto-written if missing) |
| `HERMES_BIN` | Hermes executable name/path (default `hermes`) |
| `HERMES_MODEL` | Optional; passed as `hermes -m` before `-z` when set |
| `HERMES_PROVIDER` | Optional; passed as `hermes --provider` when set |
| `HERMES_REASONING` | Optional; `none`/`minimal`/`low`/`medium`/`high`/`xhigh`/`max`/`ultra` → `--reasoning` |
| `HERMES_TOOLSETS` | Optional; comma-separated `-t` toolsets. When set, include `odoo-linkedin-agent` (or `mcp-odoo-linkedin-agent`) or MCP tools are dropped |
| `HERMES_MAX_TURNS` | Optional; `--max-turns` (leave empty if your Hermes rejects it with top-level `-z`) |
| `DISCOVERY_REUSE_MINUTES` | Reuse a recent non-imported run for the same slug+domain (default `30`) |

Never put Hunter/Odoo/DB secrets in the extension, Hermes config, tool outputs, or logs.

### 5. Register the MCP server in Hermes

Use **absolute paths on your machine** to this repo’s venv Python and `mcp_server/server.py`:

**Windows (PowerShell):**

```powershell
$ROOT = (Resolve-Path .).Path
hermes mcp add odoo-linkedin-agent `
  --command "$ROOT\.venv\Scripts\python.exe" `
  --args "$ROOT\mcp_server\server.py"

hermes mcp list
hermes mcp test odoo-linkedin-agent
```

**macOS / Linux:**

```bash
ROOT="$(pwd)"
hermes mcp add odoo-linkedin-agent \
  --command "$ROOT/.venv/bin/python" \
  --args "$ROOT/mcp_server/server.py"

hermes mcp list
hermes mcp test odoo-linkedin-agent
```

You should see exactly `find_employees`, `import_contacts`, and `get_report`. Optional workflow skill: `skills/SKILL.md`.

### MCP happy path

1. `find_employees(company, domain)` → `run_id`, counts, compact records
2. `import_contacts(run_id, dry_run=True)` → preview + `confirmation_token`
3. User explicitly approves selected **Accepted** ids
4. `import_contacts(run_id, dry_run=False, selected_ids=[...], confirmation_token=...)`
5. `get_report(run_id)`

## Chrome extension + local backend

### Start the backend

```powershell
# Windows
.\.venv\Scripts\python.exe -m extension_backend
```

```bash
# macOS / Linux
.venv/bin/python -m extension_backend
```

Listens on `http://127.0.0.1:8765` only. Auth: localhost `Host`, `Origin: chrome-extension://{EXTENSION_ID}`, and `X-Extension-Pairing-Token`.

### Load the extension

1. Open `chrome://extensions` → Developer mode → **Load unpacked** → select the `extension/` folder in this repo
2. Copy the extension ID into `.env` as `EXTENSION_ID=...` and restart the backend
3. Open extension **Options**, paste `EXTENSION_PAIRING_TOKEN` from `.env`, Save
4. Reload the extension after code changes

Default backend port is `8765`. If you change `EXTENSION_HTTP_PORT`, also update `extension/api.js`, `extension/utils.js`, `extension/manifest.json`, and any UI text that hard-codes `8765`.

### Extension flow

1. Open a LinkedIn company URL (`https://www.linkedin.com/company/<slug>`)
2. Click the extension icon → confirm/edit company + domain → **Discover in dashboard**
3. Dashboard shows preview; select Accepted contacts → confirm **Approve import** → report
4. Refresh/reopen restores runs from PostgreSQL without rediscovery
5. Opening the dashboard alone never starts Hunter/Hermes

Popup = launcher. Dashboard tab = preview, selection, approve, report, delete history.

Deleting a run removes stored personal results only; Odoo contacts are never deleted. Import audit rows (`import_attempts`) are kept.

## Manual verification

There is no automated test suite. After setup, check end-to-end manually:

1. `GET http://127.0.0.1:8765/health` with pairing token + extension Origin returns ok
2. `hermes mcp list` / `hermes mcp test odoo-linkedin-agent` shows the three tools
3. Extension: Discover on a company tab → preview → select Accepted → Approve → report
4. Confirm dry-run created no Odoo contacts; Approve created new contacts only (no updates/deletes)
5. Re-open the dashboard and confirm the run reloads from PostgreSQL

## Troubleshooting

| Symptom | Check |
|---------|--------|
| Backend unavailable in popup | Backend running? `http://127.0.0.1:8765/health` with pairing token + extension Origin |
| 403 from backend | `EXTENSION_ID` matches Chrome; Options token equals `.env`; Host is localhost |
| Hermes tools missing | `hermes mcp list` / `hermes mcp test odoo-linkedin-agent`; use this repo’s venv Python path |
| Discover shows older results | Within `DISCOVERY_REUSE_MINUTES` the same slug+domain reuses the run (no Hermes/Hunter) |
| Concurrent Discover click | Returns the running job; wait for it to finish |
| DB / init errors | `DATABASE_URL` points at `odoo_agent` |
| Odoo auth errors | `ODOO_URL`, `ODOO_DATABASE`, `ODOO_API_KEY` (JSON-2 bearer) |
| Hunter plan / page errors | Lower `HUNTER_LIMIT` (often `10` on free plans; limit+offset capped) |

## Field mapping (Hunter → Employee → Odoo)

| Hunter field | Employee / company field | Odoo field |
|--------------|--------------------------|------------|
| `first_name` + `last_name` | `name` | `res.partner.name` |
| `value` | `email` | `res.partner.email` |
| `position` | `title` | `res.partner.function` |
| `phone_number` | `phone` | `res.partner.phone` |
| `linkedin` | `linkedin_url` | `res.partner.x_linkedin_url` |
| `seniority` | `seniority` | person `comment` line |
| `department` | `department` | person `comment` line |
| `twitter` | `twitter` | person `comment` line |
| `confidence` | `email_confidence` | person `comment` line |
| `verification.status` | `email_verification` | person `comment` line |
| `sources` (count / first `uri`) | `sources_count` / `first_source_url` | person `comment` lines |
| `data.organization` | `company.organization` | (lookup/create name still from user company) |
| `data.description` | `company.description` | company `comment` line |
| `data.industry` | `company.industry` | company `comment` line |
| `data.headcount` | `company.headcount` | company `comment` line |
| `data.street` | `company.street` | company `street` (create only) |
| `data.city` | `company.city` | company `city` (create only) |
| `data.postal_code` | `company.postal_code` | company `zip` (create only) |
| `data.country` | `company.country` | company `country_id` (lookup, create only) |
| `data.state` | `company.state` | company `state_id` (lookup, create only) |
| `data.city`/`state`/`country` | `location` (joined) | dashboard run header only |

Person `comment` always starts with `Source: Hunter.io` when the source is Hunter; lines with no value are omitted. Company address is never copied onto the person. Existing companies are never updated.

## Known limitations

- Each real Hunter discovery uses API credits.
- A valid email does not mean current employment; Hunter is not a live employee directory.
- Hunter `location` is usually company HQ, not the person’s location.
- `phone_number` is often empty.
- Free Hunter plans allow about 10 emails per request (`limit` + `offset` ≤ 10). This project requests one page (`HUNTER_LIMIT`) and does not paginate.
- Domain search uses `type=personal` (skips generic inboxes like info@ / support@).
- No LinkedIn page scraping; company comes from the tab URL plus the domain the user confirms.
- Dry-run does not write to Odoo; real import only creates contacts.
- `confirmation_token` is tied to `run_id` + selected ids and expires; still ask for approval in chat or the dashboard before importing.
- Concurrent imports for the same `run_id` are locked with `SELECT … FOR UPDATE`.
- CSV provider is not wired up yet; live source is Hunter.
- No automated test suite; verify manually with Chrome + Hermes + Odoo.
