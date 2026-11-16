# Project Rules

## 1. General
- Use Python 3.14.
- Use type hints for all functions and methods.
- Use Pydantic for data models and input validation.
- Use clear, simple, maintainable code.
- Avoid unnecessary abstractions, services, helpers, and overengineering.
- Check dependency compatibility with Python 3.14 before installation.

## 2. Data Sources
- Never scrape LinkedIn or access its restricted data.
- Allowed sources in this version: Hunter API and local CSV files.
- Hunter is a company-domain contact data provider, not a guaranteed current-employee directory.
- A valid email does not prove that a person currently works for the company.
- Never fabricate missing employee information.
- Preserve source details and source dates whenever available.
- Keep missing optional fields as None.
- Do not spend Hunter API credits during tests.
- Use saved, sanitized fixtures for automated tests.

## 3. Architecture
- Providers must implement a common interface.
- CSV is the primary development and testing provider.
- Hunter is the external data provider.
- Business logic belongs in the pipeline, not in AI prompts.
- Normalization, validation, mapping, and deduplication must be deterministic.
- All Odoo API calls must live inside `odoo/client.py`.
- MCP tools must be thin wrappers over existing pipeline functions.
- Hermes manages workflow decisions; it must not implement business logic.
- Keep the MCP interface limited to the three approved tools:
  - `find_employees`
  - `import_contacts`
  - `get_report`

## 4. Data Processing
- Process every record independently.
- One failing record must not stop the remaining records.
- Normalize and validate data before importing.
- A valid email is required to create a contact.
- Phone and location are optional.
- Normalize email addresses and LinkedIn URLs before comparison.
- Deduplicate using email, then LinkedIn URL.
- Name plus company is a possible duplicate and requires review.
- Do not update or delete existing Odoo contacts automatically.
- Report every skipped or failed record with a clear reason.

## 5. Odoo Integration
- Keep all Odoo communication inside `odoo/client.py`.
- Use the API supported by the actual Odoo version.
- Use least-privilege credentials.
- Find an existing company before creating a new one.
- Use valid Odoo record IDs for relational fields such as `parent_id` and `country_id`.
- Do not expose arbitrary Odoo RPC execution through MCP.
- Do not add update or delete operations in the first version.

## 6. Import Safety
- Dry-run must never write to Odoo.
- Every real import requires explicit user approval.
- Approval must be tied to a specific `run_id` and selected records.
- Use a saved execution snapshot so preview and import operate on the same data.
- Reject unknown or already processed selections.
- Repeated runs must be idempotent.
- Prevent concurrent imports from creating duplicate contacts.
- Never infer approval from a previous dry-run.

## 7. MCP and Hermes
- MCP tools must validate their inputs.
- Never expose Odoo credentials or API keys to Hermes.
- Never include secrets in tool outputs, prompts, or logs.
- Hermes must show the preview before requesting approval.
- Hermes must not perform a real import without explicit approval.
- Handle errors clearly and do not retry indefinitely.

## 8. Security and Configuration
- Store local secrets in `.env` only.
- Commit `.env.example` with placeholder values only.
- Add `.env`, virtual environments, caches, logs, and local databases to `.gitignore`.
- Never commit real API keys or real personal contact data.
- Sanitize fixtures before committing them.
- Validate external URLs and inputs.
- Do not log authentication headers or secret values.

## 9. Testing
- Use pytest.
- Test normalization, validation, deduplication, mapping, providers, and runner behavior.
- Mock external API and Odoo calls in unit tests.
- Automated tests must not consume Hunter credits.
- Use fake data for tests.
- Run only relevant tests during each phase.

## 10. Development Workflow
- Work phase by phase.
- Do not start a new phase before the current phase's Done when condition is met.
- Review the existing project and environment before installing or changing tools.
- Do not reinstall working tools unnecessarily.
- Keep changes within the current phase scope.
- Do not modify existing Hermes global configuration or unrelated MCP servers without a clear reason.
- Commit at the end of every completed phase.
- Explain completed work and verification results briefly.
- Do not claim a phase is complete unless its Done when condition is satisfied.