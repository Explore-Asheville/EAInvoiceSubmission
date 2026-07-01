# EA Invoice Submission (FY27 budget coding)

A small web service modeled after the Asana "Invoice Submission" form. It keeps the
same fields and order, but upgrades Program, Ledger/GL Code, and Spend Category from
free-text boxes to a validated cascade, so only real budget combinations can be
submitted. On submit it creates one coded task in an existing Asana project and
attaches the uploaded invoice files to it.

## Field order

```
Vendor Name (text, autocompletes from the vendor list)
Amount      (text)
Department  (dropdown)  ->  Fund -> Program -> Spend Category -> Ledger Account/GL Code
Memo        (long text)
Upload invoice and supporting documents (one or more files, required)
```

The field order follows accounting's request. Entity through Spend Category is a
single dependent chain in that order: each choice filters the next, so every
submission lands on a valid coded line. Program Hierarchy and the FY26/FY27 budget
figures for the chosen line are looked up automatically and written onto the task
record (they are not shown on the form).

## What is in here

| File | Purpose |
|------|---------|
| `app.py` | FastAPI backend: serves the form, validates the path, creates the task, attaches files |
| `index.html` | The form (front end), matching the Asana form's fields and order |
| `budget_tree.json` | The cascade: entity > fund > department > program > GL code > spend category, with the budget figures on each line |
| `discover_fields.py` | Lists your project's custom fields and GIDs for the optional field mapping |
| `requirements.txt` | Python dependencies |
| `render.yaml` | Render web-service blueprint |
| `.env.example` | Template for local environment variables |

## How it works

1. The browser loads `index.html` and fetches `budget_tree.json`.
2. Each dropdown filters the next; downstream choices reset if an upstream choice
   changes, so only valid paths assemble. Submit stays disabled until every required
   field is set and at least one file is attached.
3. On submit, the form POSTs the fields and files (multipart) to `/api/submit`.
4. The backend re-validates the full path against the budget (the browser is never
   trusted), creates the task via `POST /tasks`, places it in the
   "Invoices to be Processed" section, stamps a coding cover page onto each uploaded
   PDF, uploads the files via `POST /attachments`, and returns the task permalink and
   attachment count.

The task is named `Invoice: Vendor | Department | Spend Category`, and the full
record (vendor, the six coded levels, Program Hierarchy, and the three budget
figures, plus the memo) goes in the description. With the optional field map, the
values are also written to custom fields.

## Configuration

Set these as environment variables (in Render: dashboard env vars / secrets). Never
commit real values.

| Variable | Required | Notes |
|----------|----------|-------|
| `ASANA_PAT` | yes | Asana personal access token (Asana > Settings > Apps > Developer apps > Personal access tokens) |
| `ASANA_PROJECT_GID` | yes | The project tasks land in (the long number in the project URL) |
| `ASANA_FIELD_MAP` | no | JSON mapping fields to custom field GIDs; leave unset to write into the description only |

### Optional: map fields to custom fields

1. Add custom fields to the project. Use **text** fields for Vendor Name, Entity,
   Fund, Department, Program Hierarchy, Program, GL Code, and Spend Category; use
   **number** fields for FY26 Budget, FY26 Forecast, and FY27 Proposed. Text fields
   take the string directly, which avoids per-option GID lookups.
2. Find their GIDs:
   ```bash
   export ASANA_PAT=...    ASANA_PROJECT_GID=...
   python discover_fields.py
   ```
3. Set `ASANA_FIELD_MAP`, for example:
   ```
   {"vendor_name":"<gid>","entity":"<gid>","fund":"<gid>","department":"<gid>",
    "program_hierarchy":"<gid>","program":"<gid>","gl_code":"<gid>","spend_category":"<gid>",
    "fy26_budget":"<gid>","fy26_forecast":"<gid>","fy27_proposed":"<gid>"}
   ```

## Run locally

```bash
pip install -r requirements.txt
cp .env.example .env        # fill in your values
set -a; source .env; set +a
uvicorn app:app --reload --port 8000
# open http://localhost:8000
```

## Deploy on Render

1. Push this folder to a Git repo.
2. Create a web service from the repo (the included `render.yaml` sets the build and
   start commands), or use New > Blueprint.
3. Add `ASANA_PAT`, `ASANA_PROJECT_GID`, and optionally `ASANA_FIELD_MAP` as secrets.
4. Deploy. The form is at the service root URL.

## Vendor autocomplete

The Vendor Name field autocompletes from `vendors.json` (served by the app and
filtered in the browser). Recommended workflow: keep the master vendor list as the
Excel on SharePoint where accounting maintains it, and regenerate `vendors.json` from
it when it changes:

```bash
pip install openpyxl
python build_vendors.py "Vendor List.xlsx"     # writes vendors.json
git commit -am "Update vendor list" && git push # Render redeploys
```

`build_vendors.py` auto-detects the vendor/name column (or pass `--col "Header"`) and
accepts `.xlsx` or `.csv`. This keeps autocomplete instant (no per-keystroke API
calls) and avoids live SharePoint/Graph auth in the request path. A scheduled
Power Automate export to CSV plus this script can fully automate the refresh later.

## PDF cover page

Each uploaded PDF gets a one-page coding cover sheet prepended (Vendor, Amount,
Entity, Fund, GL, Department, Program, Spend Category, Memo) in a sans-serif font,
so the coding travels with the document, not just the Asana task. Non-PDF uploads
are attached unchanged. The cap is 50 MB per file (`MAX_FILE_MB`).

## Notes

- Funds display with their Sage names (e.g. "133 - EA Operating"). Explore Asheville
  shows 131/132/133; BCTDA shows 130/320/321.
- Fund 130 (TDA Operating) has no budget lines, so selecting it switches Department,
  Program, GL Code, and Spend Category to free-text entry; those values are accepted
  as typed and no budget figures are attached. Every other fund stays a validated
  cascade. If TDA Operating should use a fixed Department or a real coding list,
  send it and the cascade can be restored for 130.
- File uploads are capped at 50 MB each in `app.py` (`MAX_FILE_MB`); Asana's own
  limit is higher. Adjust if needed.
- The dropdown data comes from the six FY27 budgeting workbooks. The source form is
  titled "FY26 Invoice Submission"; this build is labeled FY27 to match the data. If
  you code invoices against a different year, swap in that year's `budget_tree.json`.
- One Business Development line ("Administrative / 6060-Office Expenses /
  Subscriptions") had a blank Entity and Fund in the source. It inherits Business
  Development's Entity (Explore Asheville) and Fund (133) so it does not create an
  empty dropdown option. Correct it at the source if that assumption is wrong.

## Updating the budget

Replace `budget_tree.json` with a regenerated version of the same shape: a
`departments` list, an `entity_by_fund` map, and a `cascade` of
`department > fund > program > spend_category > {gl_code: {program_hierarchy,
fy26_budget, fy26_forecast, fy27_proposed}}`. Redeploy; the form and validation pick
up the new data with no code change.

## Security

- The Asana token lives only in the server environment. It is never sent to the
  browser and never hardcoded.
- The backend validates every submission against the budget before creating a task.
- By default anyone who can reach the URL can submit. Put the service behind your
  existing auth, an allowlist, or Render access controls before sharing it widely.
