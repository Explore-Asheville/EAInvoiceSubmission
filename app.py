"""
Explore Asheville - Invoice Submission (FY27 budget coding)
Modeled after the Asana "Invoice Submission" form, with the coding fields upgraded
from free text to a validated cascade:

    Vendor Name -> Entity -> Fund -> Department -> Program -> Ledger/GL Code
                -> Spend Category -> Memo -> invoice file upload

The cascade runs in this service, so only valid budget combinations can be chosen.
On submit, one coded task is created in an existing Asana project and the uploaded
files are attached to it. Program Hierarchy and the FY26/FY27 budget figures for the
chosen line are looked up and carried onto the task record.

Secrets are read from environment variables only. This service never hardcodes a
token and never asks the browser for one.
"""

import os
import json
import pathlib
import httpx
from typing import List
from fastapi import FastAPI, HTTPException, Form, File, UploadFile
from fastapi.responses import FileResponse, JSONResponse

BASE = pathlib.Path(__file__).parent
ASANA_API = "https://app.asana.com/api/1.0"
MAX_FILE_MB = 50

ASANA_PAT = os.environ.get("ASANA_PAT", "")
ASANA_PROJECT_GID = os.environ.get("ASANA_PROJECT_GID", "")
# Section new tasks land in (the workflow entry point). Leave unset to drop them
# into the project ungrouped.
ASANA_SECTION_GID = os.environ.get("ASANA_SECTION_GID", "")
# Optional: map fields onto custom fields by GID. Text fields for the codes and
# vendor/entity/fund/program_hierarchy; number fields for the dollar figures.
# Recognized keys: vendor_name, entity, fund, department, program_hierarchy,
# program, gl_code, spend_category, fy26_budget, fy26_forecast, fy27_proposed.
try:
    ASANA_FIELD_MAP = json.loads(os.environ.get("ASANA_FIELD_MAP", "{}"))
except json.JSONDecodeError:
    ASANA_FIELD_MAP = {}

with open(BASE / "budget_tree.json") as f:
    BUDGET = json.load(f)
CASCADE = BUDGET["cascade"]

app = FastAPI(title="EA Invoice Submission")

FUND_LABELS = {
    "130": "TDA Operating",
    "131": "TDA Earned Income",
    "132": "Always Asheville",
    "133": "EA Operating",
    "320": "Tourism Product Development",
    "321": "Legacy Investment From Tourism Fund",
}


def lookup(entity, fund, department, program, gl_code, spend_category):
    """Return (leaf, is_freeform). is_freeform=True when the fund exists but has no
    cascade data (e.g. 130 TDA Operating), so the lower fields are accepted as typed."""
    fund_node = CASCADE.get(entity, {}).get(fund)
    if fund_node is None:
        return None, False          # entity/fund not valid
    if not fund_node:
        return {}, True             # freeform fund: no budget lines below it
    try:
        return fund_node[department][program][gl_code][spend_category], False
    except KeyError:
        return None, False


def money(v):
    return f"${v:,.0f}" if isinstance(v, (int, float)) else "n/a"


def build_task(fields: dict, leaf: dict) -> dict:
    dept_short = fields["department"].split("(")[0].strip()
    name = f"Invoice: {fields['vendor_name']} | {dept_short} | {fields['spend_category']}"
    lines = [
        f"Vendor Name: {fields['vendor_name']}",
        "",
        f"Entity: {fields['entity']}",
        f"Fund: {fields['fund']}" + (f" - {FUND_LABELS[fields['fund']]}" if fields['fund'] in FUND_LABELS else ""),
        f"Department: {fields['department']}",
        f"Program Hierarchy: {leaf.get('program_hierarchy') or 'n/a'}",
        f"Program: {fields['program']}",
        f"Ledger / GL Code: {fields['gl_code']}",
        f"Spend Category: {fields['spend_category']}",
        "",
        f"FY26 Budget: {money(leaf.get('fy26_budget'))}",
        f"FY26 Forecast: {money(leaf.get('fy26_forecast'))}",
        f"FY27 Proposed Budget: {money(leaf.get('fy27_proposed'))}",
        "",
        f"Memo: {fields['memo']}",
    ]
    notes = "Invoice submission\n\n" + "\n".join(lines)
    data = {"name": name, "notes": notes, "projects": [ASANA_PROJECT_GID]}

    if ASANA_FIELD_MAP:
        cf = {}
        text_vals = {
            "vendor_name": fields["vendor_name"],
            "entity": fields["entity"],
            "fund": fields["fund"],
            "department": fields["department"],
            "program_hierarchy": leaf.get("program_hierarchy"),
            "program": fields["program"],
            "gl_code": fields["gl_code"],
            "spend_category": fields["spend_category"],
        }
        for k, v in text_vals.items():
            gid = ASANA_FIELD_MAP.get(k)
            if gid and v:
                cf[gid] = v
        for k in ("fy26_budget", "fy26_forecast", "fy27_proposed"):
            gid = ASANA_FIELD_MAP.get(k)
            if gid and isinstance(leaf.get(k), (int, float)):
                cf[gid] = leaf[k]
        if cf:
            data["custom_fields"] = cf
    return data


@app.get("/")
def index():
    return FileResponse(BASE / "index.html")


@app.get("/budget_tree.json")
def tree():
    return FileResponse(BASE / "budget_tree.json")


@app.get("/api/diag")
async def diag():
    """Diagnose Asana auth: confirms the token is valid and the project is reachable.
    Safe and read-only; never returns the token."""
    if not ASANA_PAT:
        return {"token_set": False, "detail": "ASANA_PAT is not set."}
    auth = {"Authorization": f"Bearer {ASANA_PAT}"}
    out = {"token_set": True, "token_length": len(ASANA_PAT)}
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            me = await client.get(f"{ASANA_API}/users/me", headers=auth,
                                  params={"opt_fields": "name,email"})
            if me.status_code < 400:
                d = me.json().get("data", {})
                out["users_me"] = {"ok": True, "name": d.get("name"), "email": d.get("email")}
            else:
                out["users_me"] = {"ok": False, "status": me.status_code,
                                   "error": me.json().get("errors", [{}])[0].get("message", me.text)}
        except httpx.HTTPError as exc:
            out["users_me"] = {"ok": False, "error": str(exc)}

        if ASANA_PROJECT_GID:
            try:
                pr = await client.get(f"{ASANA_API}/projects/{ASANA_PROJECT_GID}",
                                     headers=auth, params={"opt_fields": "name"})
                if pr.status_code < 400:
                    out["project"] = {"ok": True, "name": pr.json().get("data", {}).get("name")}
                else:
                    out["project"] = {"ok": False, "status": pr.status_code,
                                      "error": pr.json().get("errors", [{}])[0].get("message", pr.text)}
            except httpx.HTTPError as exc:
                out["project"] = {"ok": False, "error": str(exc)}
    return out


@app.get("/healthz")
def healthz():
    return {
        "ok": True,
        "asana_configured": bool(ASANA_PAT and ASANA_PROJECT_GID),
        "field_mapping": sorted(ASANA_FIELD_MAP.keys()),
        "entities": list(CASCADE.keys()),
    }


@app.post("/api/submit")
async def submit(
    vendor_name: str = Form(...),
    entity: str = Form(...),
    fund: str = Form(...),
    department: str = Form(...),
    program: str = Form(...),
    gl_code: str = Form(...),
    spend_category: str = Form(...),
    memo: str = Form(...),
    files: List[UploadFile] = File(default=[]),
):
    fields = {
        "vendor_name": vendor_name.strip(),
        "entity": entity,
        "fund": fund,
        "department": department,
        "program": program,
        "gl_code": gl_code,
        "spend_category": spend_category,
        "memo": memo.strip(),
    }
    if not fields["vendor_name"]:
        raise HTTPException(status_code=422, detail="Vendor Name is required.")
    if not fields["memo"]:
        raise HTTPException(status_code=422, detail="Memo is required.")

    leaf, freeform = lookup(entity, fund, department, program, gl_code, spend_category)
    if freeform:
        if not (department.strip() and program.strip() and gl_code.strip() and spend_category.strip()):
            raise HTTPException(
                status_code=422,
                detail="Department, Program, GL Code, and Spend Category are required.",
            )
        leaf = {}  # no budget figures for a freeform fund
    elif leaf is None:
        raise HTTPException(
            status_code=422,
            detail="That Entity, Fund, Department, Program, GL Code, and Spend Category "
            "combination is not in the budget. Reselect from the dropdowns.",
        )

    real_files = [f for f in files if f.filename]
    if not real_files:
        raise HTTPException(status_code=422, detail="Attach the invoice (at least one file is required).")

    # read files now, enforce size
    payloads = []
    for f in real_files:
        content = await f.read()
        if len(content) > MAX_FILE_MB * 1024 * 1024:
            raise HTTPException(status_code=413, detail=f"{f.filename} is larger than {MAX_FILE_MB} MB.")
        payloads.append((f.filename, content, f.content_type or "application/octet-stream"))

    if not (ASANA_PAT and ASANA_PROJECT_GID):
        raise HTTPException(
            status_code=503,
            detail="Asana is not configured on the server. Set ASANA_PAT and ASANA_PROJECT_GID.",
        )

    auth = {"Authorization": f"Bearer {ASANA_PAT}"}
    async with httpx.AsyncClient(timeout=60) as client:
        try:
            r = await client.post(
                f"{ASANA_API}/tasks",
                headers={**auth, "Content-Type": "application/json"},
                json={"data": build_task(fields, leaf)},
            )
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"Could not reach Asana: {exc}")
        if r.status_code >= 400:
            try:
                err = r.json().get("errors", [{}])[0].get("message", r.text)
            except Exception:
                err = r.text
            raise HTTPException(status_code=502, detail=f"Asana rejected the task: {err}")

        task = r.json().get("data", {})
        task_gid = task.get("gid")

        # Move the task into the workflow entry section, if configured.
        if ASANA_SECTION_GID and task_gid:
            try:
                await client.post(
                    f"{ASANA_API}/sections/{ASANA_SECTION_GID}/addTask",
                    headers={**auth, "Content-Type": "application/json"},
                    json={"data": {"task": task_gid}},
                )
            except httpx.HTTPError:
                pass  # task still exists in the project even if section placement fails

        attached, failed = 0, []
        for filename, content, ctype in payloads:
            try:
                ar = await client.post(
                    f"{ASANA_API}/attachments",
                    headers=auth,
                    data={"parent": task_gid},
                    files={"file": (filename, content, ctype)},
                )
                if ar.status_code < 400:
                    attached += 1
                else:
                    failed.append(filename)
            except httpx.HTTPError:
                failed.append(filename)

    return JSONResponse(
        {
            "ok": True,
            "task_gid": task_gid,
            "permalink": task.get("permalink_url"),
            "name": task.get("name"),
            "attached": attached,
            "failed": failed,
        }
    )
