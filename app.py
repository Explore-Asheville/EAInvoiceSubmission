"""
Explore Asheville - Invoice Submission (FY27 budget coding)
Standalone intake form. The coding cascade runs here in the order accounting uses:

    Vendor -> Amount -> Entity -> Fund -> Account/GL -> Department -> Program
           -> Spend Category -> Memo -> invoice upload

On submit it creates one coded task in an existing Asana project, drops it in the
"Invoices to be Processed" section, stamps a coding cover page onto each uploaded
PDF, and attaches the files. Entity/Fund/Account/GL/Department/Program/Spend that
have budget data validate against the cascade; a fund with no budget lines (130)
accepts the lower fields as free text.

Secrets are read from environment variables only; the token is never hardcoded.
"""

import os
import io
import json
import pathlib
import httpx
from typing import List
from fastapi import FastAPI, HTTPException, Form, File, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas as rl_canvas
from pypdf import PdfReader, PdfWriter

BASE = pathlib.Path(__file__).parent
ASANA_API = "https://app.asana.com/api/1.0"
MAX_FILE_MB = 50

ASANA_PAT = os.environ.get("ASANA_PAT", "")
ASANA_PROJECT_GID = os.environ.get("ASANA_PROJECT_GID", "")
ASANA_SECTION_GID = os.environ.get("ASANA_SECTION_GID", "")
try:
    ASANA_FIELD_MAP = json.loads(os.environ.get("ASANA_FIELD_MAP", "{}"))
except json.JSONDecodeError:
    ASANA_FIELD_MAP = {}

with open(BASE / "budget_tree.json") as f:
    BUDGET = json.load(f)
CASCADE = BUDGET["cascade"]

FUND_LABELS = {
    "130": "TDA Operating", "131": "TDA Earned Income", "132": "Always Asheville",
    "133": "EA Operating", "320": "Tourism Product Development",
    "321": "Legacy Investment From Tourism Fund",
}

app = FastAPI(title="EA Invoice Submission")


def lookup(entity, fund, gl_code, department, program, spend_category):
    """Return (leaf, is_freeform). Cascade order: entity > fund > gl > dept > program > spend.
    is_freeform=True when the fund exists but has no budget lines (e.g. 130)."""
    fund_node = CASCADE.get(entity, {}).get(fund)
    if fund_node is None:
        return None, False
    if not fund_node:
        return {}, True
    try:
        return fund_node[gl_code][department][program][spend_category], False
    except KeyError:
        return None, False


def money(v):
    return f"${v:,.0f}" if isinstance(v, (int, float)) else "n/a"


def fund_label(fund):
    return f"{fund} - {FUND_LABELS[fund]}" if fund in FUND_LABELS else fund


def coded_lines(fields, leaf):
    """The coding block, in accounting's field order."""
    return [
        ("Vendor Name", fields["vendor_name"]),
        ("Amount", fields["amount"]),
        ("Entity", fields["entity"]),
        ("Fund", fund_label(fields["fund"])),
        ("Ledger Account / GL Code", fields["gl_code"]),
        ("Department", fields["department"]),
        ("Program", fields["program"]),
        ("Spend Category", fields["spend_category"]),
        ("Memo", fields["memo"]),
    ]


def build_task(fields, leaf):
    dept_short = fields["department"].split("(")[0].strip()
    name = f"Invoice: {fields['vendor_name']} | {dept_short} | {fields['spend_category']}"
    block = [f"{k}: {v}" for k, v in coded_lines(fields, leaf)]
    ref = [
        "",
        "Budget reference",
        f"Program Hierarchy: {leaf.get('program_hierarchy') or 'n/a'}",
        f"FY26 Budget: {money(leaf.get('fy26_budget'))}",
        f"FY26 Forecast: {money(leaf.get('fy26_forecast'))}",
        f"FY27 Proposed Budget: {money(leaf.get('fy27_proposed'))}",
    ]
    notes = "Invoice submission\n\n" + "\n".join(block) + "\n" + "\n".join(ref)
    data = {"name": name, "notes": notes, "projects": [ASANA_PROJECT_GID]}

    if ASANA_FIELD_MAP:
        cf = {}
        text_vals = {
            "vendor_name": fields["vendor_name"], "entity": fields["entity"],
            "fund": fields["fund"], "department": fields["department"],
            "program_hierarchy": leaf.get("program_hierarchy"), "program": fields["program"],
            "gl_code": fields["gl_code"], "spend_category": fields["spend_category"],
        }
        for k, v in text_vals.items():
            gid = ASANA_FIELD_MAP.get(k)
            if gid and v:
                cf[gid] = v
        amount_gid = ASANA_FIELD_MAP.get("amount")
        if amount_gid and fields["amount"]:
            try:
                cf[amount_gid] = float(str(fields["amount"]).replace(",", "").replace("$", ""))
            except ValueError:
                pass
        for k in ("fy26_budget", "fy26_forecast", "fy27_proposed"):
            gid = ASANA_FIELD_MAP.get(k)
            if gid and isinstance(leaf.get(k), (int, float)):
                cf[gid] = leaf[k]
        if cf:
            data["custom_fields"] = cf
    return data


def make_cover_pdf(fields, leaf) -> bytes:
    """A one-page coding cover sheet (sans-serif) to prepend to the invoice PDF."""
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=letter)
    w, h = letter
    navy = (0.086, 0.337, 0.533)  # Blue Ridge #165788

    c.setFillColorRGB(*navy)
    c.rect(0, h - 0.9 * inch, w, 0.9 * inch, fill=1, stroke=0)
    c.setFillColorRGB(1, 1, 1)
    c.setFont("Helvetica-Bold", 18)
    c.drawString(0.9 * inch, h - 0.58 * inch, "Invoice Coding")
    c.setFont("Helvetica", 10)
    c.drawRightString(w - 0.9 * inch, h - 0.56 * inch, "Explore Asheville")

    y = h - 1.5 * inch
    for label, value in coded_lines(fields, leaf):
        c.setFillColorRGB(0.4, 0.4, 0.4)
        c.setFont("Helvetica-Bold", 9)
        c.drawString(0.9 * inch, y, label.upper())
        c.setFillColorRGB(0.12, 0.16, 0.18)
        c.setFont("Helvetica", 12)
        # wrap long values
        text = str(value) if value else "n/a"
        max_chars = 78
        lines = [text[i:i + max_chars] for i in range(0, len(text), max_chars)] or ["n/a"]
        for i, ln in enumerate(lines):
            c.drawString(0.9 * inch, y - 14 - (i * 14), ln)
        y -= 14 + 14 * len(lines) + 12

    c.setFillColorRGB(0.4, 0.4, 0.4)
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(0.9 * inch, 0.7 * inch,
                 "Generated by the Explore Asheville Invoice Submission form. Coding details follow on the next page(s).")
    c.showPage()
    c.save()
    return buf.getvalue()


def stamp_pdf(original: bytes, cover: bytes) -> bytes:
    """Prepend the cover page to the original PDF. Returns combined PDF bytes."""
    writer = PdfWriter()
    for page in PdfReader(io.BytesIO(cover)).pages:
        writer.add_page(page)
    for page in PdfReader(io.BytesIO(original)).pages:
        writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


@app.get("/")
def index():
    return FileResponse(BASE / "index.html")


@app.get("/budget_tree.json")
def tree():
    return FileResponse(BASE / "budget_tree.json")


@app.get("/vendors.json")
def vendors():
    return FileResponse(BASE / "vendors.json")


@app.get("/api/diag")
async def diag():
    if not ASANA_PAT:
        return {"token_set": False, "detail": "ASANA_PAT is not set."}
    auth = {"Authorization": f"Bearer {ASANA_PAT}"}
    out = {"token_set": True, "token_length": len(ASANA_PAT)}
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            me = await client.get(f"{ASANA_API}/users/me", headers=auth, params={"opt_fields": "name,email"})
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
                pr = await client.get(f"{ASANA_API}/projects/{ASANA_PROJECT_GID}", headers=auth, params={"opt_fields": "name"})
                out["project"] = ({"ok": True, "name": pr.json().get("data", {}).get("name")}
                                  if pr.status_code < 400 else
                                  {"ok": False, "status": pr.status_code,
                                   "error": pr.json().get("errors", [{}])[0].get("message", pr.text)})
            except httpx.HTTPError as exc:
                out["project"] = {"ok": False, "error": str(exc)}
    return out


@app.get("/healthz")
def healthz():
    return {"ok": True, "asana_configured": bool(ASANA_PAT and ASANA_PROJECT_GID),
            "field_mapping": sorted(ASANA_FIELD_MAP.keys()), "entities": list(CASCADE.keys())}


@app.post("/api/submit")
async def submit(
    vendor_name: str = Form(...),
    amount: str = Form(...),
    entity: str = Form(...),
    fund: str = Form(...),
    gl_code: str = Form(...),
    department: str = Form(...),
    program: str = Form(...),
    spend_category: str = Form(...),
    memo: str = Form(...),
    files: List[UploadFile] = File(default=[]),
):
    fields = {
        "vendor_name": vendor_name.strip(), "amount": amount.strip(), "entity": entity,
        "fund": fund, "gl_code": gl_code, "department": department, "program": program,
        "spend_category": spend_category, "memo": memo.strip(),
    }
    for key, label in (("vendor_name", "Vendor Name"), ("amount", "Amount"), ("memo", "Memo")):
        if not fields[key]:
            raise HTTPException(status_code=422, detail=f"{label} is required.")

    leaf, freeform = lookup(entity, fund, gl_code, department, program, spend_category)
    if freeform:
        if not (gl_code.strip() and department.strip() and program.strip() and spend_category.strip()):
            raise HTTPException(status_code=422, detail="Account/GL, Department, Program, and Spend Category are required.")
        leaf = {}
    elif leaf is None:
        raise HTTPException(status_code=422,
            detail="That Entity, Fund, Account/GL, Department, Program, and Spend Category "
                   "combination is not in the budget. Reselect from the dropdowns.")

    real_files = [f for f in files if f.filename]
    if not real_files:
        raise HTTPException(status_code=422, detail="Attach the invoice (at least one file is required).")

    payloads = []
    for f in real_files:
        content = await f.read()
        if len(content) > MAX_FILE_MB * 1024 * 1024:
            raise HTTPException(status_code=413, detail=f"{f.filename} is larger than {MAX_FILE_MB} MB.")
        payloads.append((f.filename, content, f.content_type or "application/octet-stream"))

    if not (ASANA_PAT and ASANA_PROJECT_GID):
        raise HTTPException(status_code=503, detail="Asana is not configured on the server. Set ASANA_PAT and ASANA_PROJECT_GID.")

    # stamp a coding cover page onto each PDF; leave other file types untouched
    cover = make_cover_pdf(fields, leaf)
    stamped = []
    for filename, content, ctype in payloads:
        is_pdf = ctype == "application/pdf" or filename.lower().endswith(".pdf")
        if is_pdf:
            try:
                content = stamp_pdf(content, cover)
            except Exception:
                pass  # if stamping fails, attach the original untouched
        stamped.append((filename, content, ctype))

    auth = {"Authorization": f"Bearer {ASANA_PAT}"}
    async with httpx.AsyncClient(timeout=90) as client:
        try:
            r = await client.post(f"{ASANA_API}/tasks",
                headers={**auth, "Content-Type": "application/json"},
                json={"data": build_task(fields, leaf)})
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

        if ASANA_SECTION_GID and task_gid:
            try:
                await client.post(f"{ASANA_API}/sections/{ASANA_SECTION_GID}/addTask",
                    headers={**auth, "Content-Type": "application/json"},
                    json={"data": {"task": task_gid}})
            except httpx.HTTPError:
                pass

        attached, failed = 0, []
        for filename, content, ctype in stamped:
            try:
                ar = await client.post(f"{ASANA_API}/attachments", headers=auth,
                    data={"parent": task_gid}, files={"file": (filename, content, ctype)})
                if ar.status_code < 400:
                    attached += 1
                else:
                    failed.append(filename)
            except httpx.HTTPError:
                failed.append(filename)

    return JSONResponse({"ok": True, "task_gid": task_gid, "permalink": task.get("permalink_url"),
                         "name": task.get("name"), "attached": attached, "failed": failed})
