# odoo-linkedin-agent

Turns a LinkedIn company page into Odoo contacts. You open a company page, confirm its domain, and the tool pulls known employee emails from Hunter, cleans and de-duplicates them, shows a preview, and creates the contacts you approve in Odoo.

## How it works

![Architecture](Architecture%20and%20Workflow%20Diagram.png)

A Chrome extension reads the URL of the active LinkedIn tab and nothing else. It sends that URL, plus the domain you confirm, to a small FastAPI backend running on localhost. The backend starts Hermes in one-shot mode (`hermes -z`), and Hermes calls the `find_employees` MCP tool. That tool fetches the employees from Hunter, processes them, and saves the result in PostgreSQL under a run id. The backend then builds a dry-run preview for the dashboard.

When you press Approve, the backend imports the selected contacts itself, in plain Python. Hermes is not in that step, so the model can never decide on its own to write to Odoo. The import uses a confirmation token tied to the run and the exact selected ids, and it only creates contacts; it never updates or deletes existing ones.

Hermes can also be used directly from its own chat. The skill in `skills/SKILL.md` makes it run `find_employees`, show a dry-run preview, wait for your explicit approval, import with the token, and print the report.

**Why MCP.** The agent needs a narrow, typed set of actions. The three tools (`find_employees`, `import_contacts`, `get_report`) are the whole surface Hermes gets: no arbitrary Odoo calls, no access to credentials, no delete. Everything with real consequences (validation, dedup, token checks, Odoo writes) stays in Python where it can be read and reasoned about.

## Data source

Employee data comes from the Hunter domain-search API, an authorized commercial source. The project does not scrape LinkedIn. The extension has no LinkedIn host permission and no content scripts (`activeTab` and `storage` only), so it cannot read the page even if it wanted to. The company name comes from the URL; the domain is entered or confirmed by the user.

## Requirements

- Python 3.14
- PostgreSQL
- An Odoo instance with the JSON-2 API (tested on Odoo 20)
- A Hunter API key (each discovery uses credits)
- Hermes CLI on your `PATH`, or set `HERMES_BIN`
- Chrome

## Setup

Run everything from the project root.

### 1. Python environment

Windows (PowerShell):

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

macOS / Linux:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
```

Fill in `.env` (it is git-ignored).

### 2. PostgreSQL

Create a database for the app. It has to be called `odoo_agent`, and it should be separate from Odoo's own database.

```sql
CREATE DATABASE odoo_agent;
```

Point `DATABASE_URL` at it, for example `postgresql://USER:PASSWORD@localhost:5432/odoo_agent`, then create the tables:

```powershell
.\.venv\Scripts\python.exe scripts\init_db.py
```

```bash
.venv/bin/python scripts/init_db.py
```

The script only creates tables and refuses any database name other than `odoo_agent`. The backend also makes sure the schema exists when it starts.

### 3. Odoo

The project stores the LinkedIn URL in a custom field, `x_linkedin_url`, which a small addon provides. Add `odoo/addons/linkedin_contact_extension` to your addons path, restart Odoo, update the apps list, and install the module `linkedin_contact_extension`.

Then create an API key for the Odoo user the integration will run as (user profile, Account Security, New API Key) and put it in `ODOO_API_KEY`. The client talks to the JSON-2 API with that key as a bearer token.

### 4. Environment variables

| Variable | Purpose |
|---|---|
| `HUNTER_API_KEY` | Hunter key |
| `HUNTER_LIMIT` | Emails requested per discovery, 1 to 100 (default 10; free plans allow at most 10) |
| `ODOO_URL` | Odoo base URL |
| `ODOO_DATABASE` | Odoo database name |
| `ODOO_API_KEY` | Odoo JSON-2 bearer token |
| `DATABASE_URL` | App database, ending in `/odoo_agent` |
| `CONFIRMATION_TOKEN_SECRET` | HMAC secret for import tokens (generated if missing) |
| `EXTENSION_HTTP_PORT` | Backend port (default 8765) |
| `EXTENSION_ID` | ID of the unpacked extension in Chrome |
| `EXTENSION_PAIRING_TOKEN` | Shared secret the extension sends to the backend (generated if missing) |
| `HERMES_BIN` | Hermes executable (default `hermes`) |
| `HERMES_MODEL`, `HERMES_PROVIDER`, `HERMES_REASONING`, `HERMES_MAX_TURNS` | Optional. Passed to `hermes` as `-m`, `--provider`, `--reasoning`, `--max-turns` when set. Leave `HERMES_MAX_TURNS` empty if your Hermes version rejects it with `-z` |
| `HERMES_TOOLSETS` | Optional, comma-separated `-t` value. If you set it, include `odoo-linkedin-agent` (or `mcp-odoo-linkedin-agent`), otherwise the MCP tools are dropped |
| `DISCOVERY_REUSE_MINUTES` | Reuse a recent, not yet imported run for the same company and domain (default 30) |

Hunter, Odoo and database credentials live only in `.env`. They are never sent to the extension or to Hermes.

### 5. Register the MCP server in Hermes

Use absolute paths to this repo's venv Python and `mcp_server/server.py`.

Windows (PowerShell):

```powershell
$ROOT = (Resolve-Path .).Path
hermes mcp add odoo-linkedin-agent `
  --command "$ROOT\.venv\Scripts\python.exe" `
  --args "$ROOT\mcp_server\server.py"

hermes mcp list
hermes mcp test odoo-linkedin-agent
```

macOS / Linux:

```bash
ROOT="$(pwd)"
hermes mcp add odoo-linkedin-agent \
  --command "$ROOT/.venv/bin/python" \
  --args "$ROOT/mcp_server/server.py"

hermes mcp list
hermes mcp test odoo-linkedin-agent
```

You should see exactly three tools: `find_employees`, `import_contacts` and `get_report`.

## Running it

### Backend

```powershell
.\.venv\Scripts\python.exe -m extension_backend
```

```bash
.venv/bin/python -m extension_backend
```

It listens on `127.0.0.1:8765` only. A request is accepted only if the Host is localhost, the Origin is `chrome-extension://<EXTENSION_ID>`, and the `X-Extension-Pairing-Token` header matches.

### Extension

1. Open `chrome://extensions`, turn on Developer mode, choose **Load unpacked**, and select the `extension/` folder.
2. Copy the extension ID into `.env` as `EXTENSION_ID` and restart the backend.
3. Open the extension's Options page, paste `EXTENSION_PAIRING_TOKEN` from `.env`, and save.

If you change `EXTENSION_HTTP_PORT`, also update the port in `extension/api.js`, `extension/utils.js` and `extension/manifest.json`.

### Using it

1. Open a LinkedIn company page, for example `https://www.linkedin.com/company/<slug>`.
2. Click the extension icon, check the company name and domain (the suggested domain is only a guess, so correct it if needed), and press **Discover in dashboard**.
3. The dashboard shows the processed records: accepted, duplicate, possible duplicate, skipped and rejected. Select the accepted ones you want and press **Approve import**.
4. The report shows what was created, what was a duplicate, and what failed. You can filter it, search it and download it as CSV.
5. Runs are stored in PostgreSQL, so reloading the dashboard brings them back without a new discovery. Discovering the same company again within `DISCOVERY_REUSE_MINUTES` opens the existing run instead of spending more Hunter credits.

Deleting a run from the dashboard removes its stored results (names, emails and so on). Contacts already created in Odoo are not touched, and the import audit rows (ids and outcomes only) are kept.

### From Hermes directly

Start `hermes` and ask it to find the employees of a company, giving the company name and domain. Hermes calls `find_employees`, shows a dry-run preview, and asks for your approval before calling `import_contacts` with the token. `get_report` gives the final report.

## Trying it end to end

There are no automated tests; I checked the flow by hand. To repeat that check:

1. Start PostgreSQL and Odoo, set up `.env`, run `scripts/init_db.py`, and start the backend.
2. Confirm `hermes mcp test odoo-linkedin-agent` lists the three tools.
3. Open a company page, discover, and look at the preview. Nothing should exist in Odoo yet.
4. Approve a few records and check the new contacts and their company in Odoo (job title, email, LinkedIn URL and notes should be filled in where Hunter had the data).
5. Discover the same company again: the run opens immediately. Try importing the same people again: they come back as duplicates.
6. Open the report, download the CSV, then delete the run and confirm the Odoo contacts are still there.

## Troubleshooting

| Symptom | What to check |
|---|---|
| Popup says the backend is unavailable | Is the backend running? Is the port 8765? |
| 403 from the backend | `EXTENSION_ID` matches Chrome, the Options token matches `.env`, and the backend was restarted after editing `.env` |
| Hermes tools missing | `hermes mcp list` and `hermes mcp test odoo-linkedin-agent`; use this repo's venv Python in the registration |
| Discover returns older results | A run for the same company and domain exists within `DISCOVERY_REUSE_MINUTES`. Delete it to start fresh |
| Second Discover click does nothing new | A discovery is already running for that company; wait for it to finish |
| Database errors | `DATABASE_URL` must point at `odoo_agent`, and the database must exist |
| Odoo authentication errors | `ODOO_URL`, `ODOO_DATABASE` and `ODOO_API_KEY` |
| Hunter plan or pagination errors | Lower `HUNTER_LIMIT` (10 on free plans) |

## Field mapping

| Hunter | Odoo contact |
|---|---|
| first and last name | `name` |
| email | `email` |
| position | `function` |
| phone number | `phone` |
| LinkedIn | `x_linkedin_url` |

Seniority, department, Twitter, email confidence, email verification status and the number of sources are written as lines in the contact's internal notes, starting with `Source: Hunter.io`. Lines without a value are left out.

Company-level data (street, city, zip, country, state) goes on the company contact, not on the people, because Hunter returns the company's address and not the employee's. The company's description, industry and headcount go into its notes. An existing company is never updated.

## Limitations

- A valid email does not prove the person still works there. Hunter returns emails it has seen for the domain; it is not a live employee directory.
- Free Hunter plans return at most 10 emails per request, and the project does not paginate.
- Phone numbers are often missing, and the location is the company's, not the person's.
- Every real discovery spends Hunter credits.
- Odoo writes are create-only. Existing contacts are never updated, so stale data in Odoo stays as it is.
- The confirmation token ties an import to a run and a set of ids, and it expires. It cannot prove that a human approved the import; in the extension that approval is the click on Approve, and in Hermes chat it is the skill's instruction to ask first.
- Concurrent imports for the same run are serialized with `SELECT ... FOR UPDATE`.
- Hunter is the only data provider. There is no CSV or other fallback.
- No automated tests.
