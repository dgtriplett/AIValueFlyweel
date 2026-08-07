"""Instance branding — show the customer's name and logo in the header.

    GET    /api/branding        what to render (public-ish, cacheable)
    PUT    /api/branding        set name / subtitle / accent colour
    POST   /api/branding/logo   upload a logo
    DELETE /api/branding/logo   remove it
    GET    /api/branding/logo   serve the bytes

Small feature, but it is what makes the app read as the customer's during a
workshop instead of as a generic tool.

The name falls back to the researched company profile, so an instance that has run
company research is already branded without anyone setting anything.
"""
from __future__ import annotations

import re

from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field

from ..common import current_user, write_audit
from ..db import db

router = APIRouter(prefix="/branding", tags=["branding"])

# A logo is a header image. 2MB is generous for that and small enough that holding
# the bytes in a row costs nothing; anything larger is a photo pasted by mistake.
MAX_LOGO_BYTES = 2 * 1024 * 1024

# Raster + SVG only. No arbitrary content types: these bytes get served back with
# the caller's declared MIME, so an unrestricted type is a stored-XSS vector via
# something like image/svg+xml-adjacent HTML.
ALLOWED_MIME = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
}

_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")

# Databricks Lava, the app's default accent.
DEFAULT_ACCENT = "#FF3621"


class BrandingIn(BaseModel):
    display_name: str | None = Field(default=None, max_length=120)
    subtitle: str | None = Field(default=None, max_length=200)
    accent_color: str | None = None


@router.get("")
async def get_branding():
    """What the header should render.

    Never raises: branding is chrome, and a missing table or empty row must not
    break the page it decorates.
    """
    row = None
    try:
        row = await db.fetchrow(
            "SELECT display_name, subtitle, accent_color, logo_mime, "
            "logo_filename, updated_at, "
            "(logo_bytes IS NOT NULL) AS has_logo "
            "FROM branding WHERE id = 1")
    except Exception:  # noqa: BLE001 - table may not exist on an un-migrated install
        pass

    company = None
    try:
        profile = await db.fetchrow(
            "SELECT company_name FROM company_profile WHERE id = 1")
        company = profile["company_name"] if profile else None
    except Exception:  # noqa: BLE001
        pass

    data = dict(row) if row else {}
    # Explicit branding wins; otherwise a researched company name brands the
    # instance for free; otherwise the product name.
    display_name = (data.get("display_name") or company or "Grid Atlas")
    return {
        "display_name": display_name,
        "subtitle": data.get("subtitle")
                    or "Power & Utilities — Data & AI Catalog, Value & Roadmap",
        "accent_color": data.get("accent_color") or DEFAULT_ACCENT,
        "has_logo": bool(data.get("has_logo")),
        "logo_url": "/api/branding/logo" if data.get("has_logo") else None,
        # So the UI can say where the name came from rather than implying someone
        # typed it.
        "source": ("custom" if data.get("display_name")
                   else "company_profile" if company else "default"),
        "company_name": company,
        "updated_at": data.get("updated_at"),
    }


@router.put("")
async def set_branding(body: BrandingIn, request: Request):
    accent = (body.accent_color or "").strip() or None
    if accent and not _HEX_COLOR.match(accent):
        raise HTTPException(
            422, f"accent_color must be a hex colour like {DEFAULT_ACCENT}, "
                 f"got {accent!r}")
    actor = current_user(request)
    await db.execute("""
        INSERT INTO branding (id, display_name, subtitle, accent_color, updated_by)
        VALUES (1, $1, $2, $3, $4)
        ON CONFLICT (id) DO UPDATE SET
          display_name = EXCLUDED.display_name,
          subtitle = EXCLUDED.subtitle,
          accent_color = EXCLUDED.accent_color,
          updated_by = EXCLUDED.updated_by,
          updated_at = now()
    """, (body.display_name or "").strip() or None,
        (body.subtitle or "").strip() or None, accent, actor)
    await write_audit("branding", None, "update", actor, body.model_dump())
    return await get_branding()


@router.post("/logo")
async def upload_logo(request: Request, file: UploadFile = File(...)):
    """Store a logo. Replaces any existing one."""
    content = await file.read()
    if not content:
        raise HTTPException(422, "The uploaded file is empty.")
    if len(content) > MAX_LOGO_BYTES:
        raise HTTPException(
            413, f"Logo is {len(content) // 1024}KB; the limit is "
                 f"{MAX_LOGO_BYTES // 1024}KB. A header logo should be well under "
                 "that — try exporting it smaller.")

    mime = (file.content_type or "").split(";")[0].strip().lower()
    if mime not in ALLOWED_MIME:
        raise HTTPException(
            422,
            f"Unsupported image type {mime or 'unknown'!r}. "
            f"Allowed: {', '.join(sorted(ALLOWED_MIME))}.")

    # Verify the bytes match the declared type rather than trusting the header.
    # A mismatch means either a mislabelled file or an attempt to have us serve
    # something other than an image back under an image MIME.
    if not _looks_like(content, mime):
        raise HTTPException(
            422,
            f"The file content does not look like {mime}. Check the file is a "
            "valid image and not renamed from another format.")

    actor = current_user(request)
    await db.execute("""
        INSERT INTO branding (id, logo_bytes, logo_mime, logo_filename, updated_by)
        VALUES (1, $1, $2, $3, $4)
        ON CONFLICT (id) DO UPDATE SET
          logo_bytes = EXCLUDED.logo_bytes, logo_mime = EXCLUDED.logo_mime,
          logo_filename = EXCLUDED.logo_filename,
          updated_by = EXCLUDED.updated_by, updated_at = now()
    """, content, mime, file.filename, actor)
    await write_audit("branding", None, "upload_logo", actor,
                      {"bytes": len(content), "mime": mime})
    return {"ok": True, "bytes": len(content), "mime": mime,
            "logo_url": "/api/branding/logo"}


def _looks_like(content: bytes, mime: str) -> bool:
    """Magic-number check for the allowed types.

    Cheap defence against a mislabelled or hostile upload: we serve these bytes
    back with the declared MIME, so the declaration has to be true.
    """
    if mime == "image/png":
        return content.startswith(b"\x89PNG\r\n\x1a\n")
    if mime == "image/jpeg":
        return content.startswith(b"\xff\xd8\xff")
    if mime == "image/gif":
        return content.startswith((b"GIF87a", b"GIF89a"))
    if mime == "image/webp":
        return content[:4] == b"RIFF" and content[8:12] == b"WEBP"
    if mime == "image/svg+xml":
        head = content[:512].lstrip().lower()
        return head.startswith(b"<?xml") or head.startswith(b"<svg")
    return False


@router.get("/logo")
async def serve_logo():
    """Serve the stored logo bytes."""
    row = None
    try:
        row = await db.fetchrow(
            "SELECT logo_bytes, logo_mime FROM branding WHERE id = 1")
    except Exception:  # noqa: BLE001
        pass
    if row is None or not row["logo_bytes"]:
        raise HTTPException(404, "No logo uploaded.")

    headers = {
        # Short cache: a logo changes rarely, but when someone replaces it during a
        # workshop they expect to see it immediately.
        "Cache-Control": "public, max-age=60",
        # These bytes are user-supplied. An SVG can carry script, so serving it
        # under a restrictive CSP and forbidding sniffing keeps an uploaded logo
        # from becoming an execution vector.
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
        "X-Content-Type-Options": "nosniff",
    }
    return Response(content=bytes(row["logo_bytes"]),
                    media_type=row["logo_mime"] or "image/png", headers=headers)


@router.delete("/logo")
async def delete_logo(request: Request):
    actor = current_user(request)
    await db.execute(
        "UPDATE branding SET logo_bytes=NULL, logo_mime=NULL, logo_filename=NULL, "
        "updated_by=$1, updated_at=now() WHERE id=1", actor)
    await write_audit("branding", None, "delete_logo", actor)
    return await get_branding()
