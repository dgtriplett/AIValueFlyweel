"""Onboarding / bulk-import: Excel export template + upload-import with dry-run.

Lets a utility circulate a spreadsheet, fill it offline, and upload to populate
the app from the clean day-1 state. Round-trips by stable id/key columns.
"""
import io

from fastapi import APIRouter, HTTPException, Request, UploadFile, File
from fastapi.responses import StreamingResponse

from .. import accounts
from ..common import current_user, write_audit
from ..db import db

router = APIRouter(prefix="/onboarding", tags=["onboarding"])

_STATUSES = ("not_started", "scoping", "in_progress", "live", "value_realized")
_INGEST = ("not_started", "landed", "curated", "governed")


@router.get("/export.xlsx")
async def export_template():
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation

    wb = Workbook()
    hdr_fill = PatternFill("solid", fgColor="1B3139")
    hdr_font = Font(color="FFFFFF", bold=True)
    edit_fill = PatternFill("solid", fgColor="FFF3D6")

    def style_header(ws, ncols):
        for c in range(1, ncols + 1):
            cell = ws.cell(row=1, column=c)
            cell.fill = hdr_fill
            cell.font = hdr_font

    def add_column_validation(ws, dv, column):
        """Attach `dv` to `column` over the DATA rows only, if there are any.

        On a fresh install every sheet is header-only, so `max_row` is 1 and the
        obvious f"{column}2:{column}{ws.max_row}" spells "E2:E1" — a reversed range
        that makes the workbook invalid, which turned the very first template a new
        customer downloads into a 500. An empty sheet simply gets no dropdown: there
        is no data cell to constrain, and the validation reappears on the next
        export once rows exist.
        """
        if ws.max_row < 2:
            return
        ws.add_data_validation(dv)
        dv.add(f"{column}2:{column}{ws.max_row}")

    # Instructions sheet
    ws0 = wb.active
    ws0.title = "Instructions"
    for i, line in enumerate([
        ["AI Value Flywheel — Onboarding Workbook"],
        [""],
        ["Fill the highlighted (yellow) columns and re-upload via Data → Onboarding → Upload."],
        ["Do NOT edit the 'id' / 'key' columns — they map your edits back to the app."],
        [""],
        ["Data Sources: set ingestion_status to what you actually have (not_started/landed/curated/governed); vendor optional."],
        ["Use Cases: set project status, owner LOB, priority, notes, and value base ($M) for your reality."],
        ["Assumptions: set each global assumption to your organization's value."],
    ]):
        ws0.append(line)
    ws0["A1"].font = Font(bold=True, size=14, color="FF3621")

    # Data Sources sheet
    ws1 = wb.create_sheet("Data Sources")
    ws1.append(["id", "source_category", "module", "vendor", "ingestion_status"])
    style_header(ws1, 5)
    assets = await db.fetch("SELECT id, source_category, module, vendor, ingestion_status FROM data_assets ORDER BY source_category, id")
    for a in assets:
        ws1.append([a["id"], a["source_category"], a["module"], a["vendor"] or "", a["ingestion_status"]])
    dv = DataValidation(type="list", formula1='"not_started,landed,curated,governed"', allow_blank=False)
    add_column_validation(ws1, dv, "E")
    for r in range(2, ws1.max_row + 1):
        ws1.cell(row=r, column=4).fill = edit_fill
        ws1.cell(row=r, column=5).fill = edit_fill
    for col, w in zip("ABCDE", (6, 26, 34, 18, 16)):
        ws1.column_dimensions[col].width = w

    # Use Cases sheet
    ws2 = wb.create_sheet("Use Cases")
    ws2.append(["id", "title", "domain", "phase", "status", "owner_lob", "priority", "value_base_mm", "notes"])
    style_header(ws2, 9)
    ucs = await db.fetch("""
        SELECT uc.id, uc.title, l.name AS domain, uc.phase, uc.status, uc.priority_score,
               (uc.hypothesized_value_json->>'mid_mm')::numeric AS value_mm
        FROM use_cases uc LEFT JOIN lobs l ON l.id = uc.lob_id ORDER BY uc.id""")
    for u in ucs:
        ws2.append([u["id"], u["title"], u["domain"], u["phase"], u["status"], u["domain"],
                    float(u["priority_score"]) if u["priority_score"] else "", float(u["value_mm"]) if u["value_mm"] else "", ""])
    dv2 = DataValidation(type="list", formula1='"not_started,scoping,in_progress,live,value_realized"', allow_blank=False)
    add_column_validation(ws2, dv2, "E")
    for r in range(2, ws2.max_row + 1):
        for c in (5, 6, 7, 8, 9):
            ws2.cell(row=r, column=c).fill = edit_fill
    for col, w in zip("ABCDEFGHI", (6, 40, 18, 8, 16, 20, 10, 14, 30)):
        ws2.column_dimensions[col].width = w

    # Assumptions sheet
    ws3 = wb.create_sheet("Assumptions")
    ws3.append(["key", "label", "unit", "value"])
    style_header(ws3, 4)
    account_id = await accounts.current()
    if account_id is not None:
        assumptions = await db.fetch("""
            SELECT DISTINCT ON (key) key, label, unit, value, category
            FROM value_assumptions
            WHERE account_id = $1 OR account_id IS NULL
            ORDER BY key, (account_id IS NULL)
        """, account_id)
        assumptions = sorted(assumptions,
                             key=lambda a: (a["category"] or "", a["key"] or ""))
    else:
        assumptions = await db.fetch(
            "SELECT key, label, unit, value FROM value_assumptions ORDER BY category, key")
    for a in assumptions:
        ws3.append([a["key"], a["label"], a["unit"] or "", float(a["value"])])
    for r in range(2, ws3.max_row + 1):
        ws3.cell(row=r, column=4).fill = edit_fill
    for col, w in zip("ABCD", (24, 32, 12, 16)):
        ws3.column_dimensions[col].width = w

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(
        buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="grid-atlas-onboarding.xlsx"'})


def _parse_workbook(content: bytes):
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(content), data_only=True)
    out = {"data_sources": [], "use_cases": [], "assumptions": []}

    def rows(sheet, cols):
        if sheet not in wb.sheetnames:
            return []
        ws = wb[sheet]
        headers = [str(c.value).strip() if c.value is not None else "" for c in ws[1]]
        idx = {h: i for i, h in enumerate(headers)}
        res = []
        for row in ws.iter_rows(min_row=2, values_only=True):
            if all(v is None for v in row):
                continue
            res.append({c: (row[idx[c]] if c in idx and idx[c] < len(row) else None) for c in cols})
        return res

    out["data_sources"] = rows("Data Sources", ["id", "ingestion_status", "vendor"])
    out["use_cases"] = rows("Use Cases", ["id", "status", "priority", "value_base_mm", "notes"])
    out["assumptions"] = rows("Assumptions", ["key", "value"])
    return out


async def _compute_import(parsed):
    """Diff parsed workbook vs current DB. Returns changes + errors (no writes)."""
    changes = {"data_sources": [], "use_cases": [], "assumptions": []}
    errors = []

    account_id = await accounts.current()
    if account_id is not None:
        cur_assets = {a["id"]: a for a in await db.fetch("""
            SELECT da.id, da.source_category, da.module,
                   COALESCE(s.ingestion_status, 'not_started') AS ingestion_status,
                   da.vendor
            FROM data_assets da
            LEFT JOIN asset_status_by_account s
                   ON s.data_asset_id = da.id AND s.account_id = $1
        """, account_id)}
    else:
        cur_assets = {a["id"]: a for a in await db.fetch(
            "SELECT id, source_category, module, ingestion_status, vendor FROM data_assets")}
    for r in parsed["data_sources"]:
        try:
            aid = int(r["id"])
        except (TypeError, ValueError):
            continue
        a = cur_assets.get(aid)
        if not a:
            errors.append(f"Data source id {aid} not found")
            continue
        st = (str(r["ingestion_status"]).strip() if r["ingestion_status"] else "")
        if st and st not in _INGEST:
            errors.append(f"Data source {aid}: invalid ingestion_status '{st}'")
        elif st and st != a["ingestion_status"]:
            changes["data_sources"].append({"id": aid, "label": f"{a['source_category']} · {a['module']}",
                                            "field": "ingestion_status", "from": a["ingestion_status"], "to": st})

    cur_ucs = {u["id"]: u for u in await db.fetch("SELECT id, title, status, priority_score FROM use_cases")}
    for r in parsed["use_cases"]:
        try:
            uid = int(r["id"])
        except (TypeError, ValueError):
            continue
        u = cur_ucs.get(uid)
        if not u:
            errors.append(f"Use case id {uid} not found")
            continue
        st = (str(r["status"]).strip() if r["status"] else "")
        if st and st not in _STATUSES:
            errors.append(f"Use case {uid}: invalid status '{st}'")
        elif st and st != u["status"]:
            changes["use_cases"].append({"id": uid, "title": u["title"], "field": "status", "from": u["status"], "to": st})

    if account_id is not None:
        cur_ass = {a["key"]: float(a["value"]) for a in await db.fetch("""
            SELECT DISTINCT ON (key) key, value
            FROM value_assumptions
            WHERE account_id = $1 OR account_id IS NULL
            ORDER BY key, (account_id IS NULL)
        """, account_id)}
    else:
        cur_ass = {a["key"]: float(a["value"]) for a in await db.fetch(
            "SELECT key, value FROM value_assumptions")}
    for r in parsed["assumptions"]:
        key = r.get("key")
        if not key or key not in cur_ass:
            continue
        try:
            val = float(r["value"])
        except (TypeError, ValueError):
            continue
        if abs(val - cur_ass[key]) > 1e-9:
            changes["assumptions"].append({"key": key, "from": cur_ass[key], "to": val})
    return changes, errors


@router.post("/import")
async def import_workbook(request: Request, file: UploadFile = File(...), apply: bool = False):
    content = await file.read()
    try:
        parsed = _parse_workbook(content)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(422, f"Could not read workbook: {exc}")
    changes, errors = await _compute_import(parsed)

    if not apply:
        return {"apply": False, "changes": changes, "errors": errors,
                "summary": {"data_sources": len(changes["data_sources"]),
                            "use_cases": len(changes["use_cases"]),
                            "assumptions": len(changes["assumptions"])}}

    actor = current_user(request)
    account_id = await accounts.current()
    applied = 0
    for c in changes["data_sources"]:
        if account_id is not None:
            await db.execute("""
                INSERT INTO account_asset_status
                    (account_id, data_asset_id, ingestion_status, is_user_edited,
                     updated_by, updated_at)
                VALUES ($1,$2,$3,true,$4,now())
                ON CONFLICT (account_id, data_asset_id) DO UPDATE SET
                    ingestion_status = EXCLUDED.ingestion_status,
                    is_user_edited = true,
                    updated_by = EXCLUDED.updated_by,
                    updated_at = now()
            """, account_id, c["id"], c["to"], actor)
        else:
            await db.execute(
                "UPDATE data_assets SET ingestion_status=$1, updated_at=now() WHERE id=$2",
                c["to"], c["id"])
        applied += 1
    for c in changes["use_cases"]:
        await db.execute("UPDATE use_cases SET status=$1, updated_at=now() WHERE id=$2", c["to"], c["id"])
        applied += 1
    for c in changes["assumptions"]:
        if account_id is not None:
            meta = await db.fetchrow(
                """SELECT label, unit, category
                   FROM value_assumptions
                   WHERE key=$1 AND (account_id=$2 OR account_id IS NULL)
                   ORDER BY (account_id IS NULL) LIMIT 1""",
                c["key"], account_id)
            await db.execute("""
                INSERT INTO value_assumptions
                    (account_id, key, label, value, unit, category, source, updated_at)
                VALUES ($1,$2,$3,$4,$5,$6,'manual_import',now())
                ON CONFLICT (account_id, key) DO UPDATE SET
                    value=EXCLUDED.value,
                    source='manual_import',
                    updated_at=now()
            """, account_id, c["key"],
                meta["label"] if meta else c["key"], c["to"],
                meta["unit"] if meta else None,
                meta["category"] if meta else None)
        else:
            await db.execute(
                "UPDATE value_assumptions SET value=$1 WHERE key=$2",
                c["to"], c["key"])
        applied += 1
    await write_audit("onboarding", None, "import", actor, {"applied": applied, "errors": len(errors)})
    return {"apply": True, "applied": applied, "changes": changes, "errors": errors}
