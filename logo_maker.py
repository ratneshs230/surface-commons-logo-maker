"""Logo Maker — Commons guild's second-wave tool.

Open-source replacement for Looka / Canva paid logo generation. Renders
PNG logos via Pillow primitives (circle / triangle / hexagon) plus a
DejaVu wordmark. Four styles: minimalist, geometric, badge, wordmark.

Endpoints (auth: service-key + X-Agent-Id, same as the rest of `/commons/*`):
  POST /commons/logo/generate     {brand_name, style, color_hex?} -> PNG
  POST /commons/logo/variations   {brand_name, style, count}      -> ZIP of N PNGs
  GET  /commons/logo/health       (public)
"""
from __future__ import annotations

import hashlib
import io
import math
import os
import time
import uuid
import zipfile
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.service_auth import require_service_key_and_agent
from app.models.agent import Agent
from app.services.ledger import InsufficientFundsError, record_transaction


router = APIRouter(prefix="/commons/logo", tags=["commons-logo"])


STYLES = ("minimalist", "geometric", "badge", "wordmark")
StyleT = Literal["minimalist", "geometric", "badge", "wordmark"]

SC_COST = {
    "generate": float(os.environ.get("LOGO_SC_GENERATE", "3")),
    "variation_each": float(os.environ.get("LOGO_SC_VAR_EACH", "3")),
}

MAX_VARIATIONS = int(os.environ.get("LOGO_MAX_VARIATIONS", "12"))
CANVAS = int(os.environ.get("LOGO_CANVAS_PX", "800"))

# Deterministic palette used when caller omits color_hex.
PALETTE = [
    "#ffb454", "#6ee7a0", "#7aa6ff", "#ff7a90",
    "#c084fc", "#34d399", "#f472b6", "#fbbf24",
    "#60a5fa", "#f87171", "#a78bfa", "#22d3ee",
]

FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


# ── Schemas ────────────────────────────────────────────────────────────────

class GenerateRequest(BaseModel):
    brand_name: str = Field(..., min_length=1, max_length=40)
    style: StyleT = "minimalist"
    color_hex: str | None = Field(None, pattern=r"^#?[0-9a-fA-F]{6}$")


class VariationsRequest(BaseModel):
    brand_name: str = Field(..., min_length=1, max_length=40)
    style: StyleT = "minimalist"
    count: int = Field(4, ge=1, le=MAX_VARIATIONS)


# ── Color helpers ──────────────────────────────────────────────────────────

def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _pick_color(brand_name: str, color_hex: str | None, salt: int = 0) -> tuple[int, int, int]:
    if color_hex:
        return _hex_to_rgb(color_hex)
    digest = hashlib.sha256(f"{brand_name}|{salt}".encode()).digest()
    idx = digest[0] % len(PALETTE)
    return _hex_to_rgb(PALETTE[idx])


def _luminance(rgb: tuple[int, int, int]) -> float:
    r, g, b = (c / 255.0 for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _fg_for(bg: tuple[int, int, int]) -> tuple[int, int, int]:
    return (20, 20, 22) if _luminance(bg) > 0.55 else (245, 245, 248)


# ── Renderer ───────────────────────────────────────────────────────────────

def _font(path: str, size: int):
    from PIL import ImageFont
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def _fit_font_size(text_str: str, max_width: int, max_size: int, path: str) -> int:
    """Binary search the largest font size whose text fits within max_width."""
    from PIL import ImageDraw, Image
    img = Image.new("RGB", (10, 10))
    draw = ImageDraw.Draw(img)
    lo, hi, best = 12, max_size, 12
    while lo <= hi:
        mid = (lo + hi) // 2
        f = _font(path, mid)
        bbox = draw.textbbox((0, 0), text_str, font=f)
        w = bbox[2] - bbox[0]
        if w <= max_width:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def _draw_centered_text(draw, xy, text_str, font, fill):
    bbox = draw.textbbox((0, 0), text_str, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    cx, cy = xy
    draw.text((cx - tw / 2 - bbox[0], cy - th / 2 - bbox[1]), text_str, font=font, fill=fill)


def _hex_polygon(cx: float, cy: float, r: float) -> list[tuple[float, float]]:
    return [
        (cx + r * math.cos(math.radians(60 * i - 30)),
         cy + r * math.sin(math.radians(60 * i - 30)))
        for i in range(6)
    ]


def _tri_polygon(cx: float, cy: float, r: float) -> list[tuple[float, float]]:
    return [
        (cx + r * math.cos(math.radians(90 + 120 * i)),
         cy - r * math.sin(math.radians(90 + 120 * i)))
        for i in range(3)
    ]


def render_logo(
    brand_name: str,
    style: str,
    color: tuple[int, int, int],
    *,
    canvas: int = CANVAS,
) -> bytes:
    """Render one logo PNG. Pure function — no IO, no DB."""
    from PIL import Image, ImageDraw

    bg = (255, 255, 255)
    img = Image.new("RGB", (canvas, canvas), bg)
    draw = ImageDraw.Draw(img)

    cx = canvas / 2
    name = brand_name.strip()

    if style == "minimalist":
        r = canvas * 0.14
        top_y = canvas * 0.36
        # Thin outline ring
        draw.ellipse(
            [cx - r, top_y - r, cx + r, top_y + r],
            outline=color, width=max(4, canvas // 120),
        )
        # Inner dot
        dr = r * 0.18
        draw.ellipse([cx - dr, top_y - dr, cx + dr, top_y + dr], fill=color)
        size = _fit_font_size(name, int(canvas * 0.78), int(canvas * 0.13), FONT_BOLD)
        f = _font(FONT_BOLD, size)
        _draw_centered_text(draw, (cx, canvas * 0.72), name, f, (30, 30, 33))

    elif style == "geometric":
        r = canvas * 0.20
        top_y = canvas * 0.36
        # Choose hex or triangle deterministically by name length parity.
        if len(name) % 2 == 0:
            poly = _hex_polygon(cx, top_y, r)
        else:
            poly = _tri_polygon(cx, top_y, r)
        draw.polygon(poly, fill=color)
        size = _fit_font_size(name, int(canvas * 0.78), int(canvas * 0.13), FONT_BOLD)
        f = _font(FONT_BOLD, size)
        _draw_centered_text(draw, (cx, canvas * 0.74), name, f, (30, 30, 33))

    elif style == "badge":
        cy = canvas / 2
        outer_r = canvas * 0.36
        inner_r = canvas * 0.30
        draw.ellipse([cx - outer_r, cy - outer_r, cx + outer_r, cy + outer_r], fill=color)
        ring_w = max(4, canvas // 140)
        draw.ellipse(
            [cx - inner_r, cy - inner_r, cx + inner_r, cy + inner_r],
            outline=_fg_for(color), width=ring_w,
        )
        fg = _fg_for(color)
        max_w = int(inner_r * 1.7)
        size = _fit_font_size(name, max_w, int(canvas * 0.13), FONT_BOLD)
        f = _font(FONT_BOLD, size)
        _draw_centered_text(draw, (cx, cy), name.upper(), f, fg)

    elif style == "wordmark":
        size = _fit_font_size(name, int(canvas * 0.84), int(canvas * 0.24), FONT_BOLD)
        f = _font(FONT_BOLD, size)
        _draw_centered_text(draw, (cx, canvas * 0.48), name, f, (30, 30, 33))
        # Accent underline
        bbox = draw.textbbox((0, 0), name, font=f)
        tw = bbox[2] - bbox[0]
        bar_y = canvas * 0.62
        bar_h = max(6, canvas // 100)
        draw.rectangle(
            [cx - tw / 2, bar_y, cx + tw / 2, bar_y + bar_h],
            fill=color,
        )
    else:
        raise ValueError(f"unknown style: {style}")

    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()


# ── Audit + billing ────────────────────────────────────────────────────────

def _json_dump(d: dict) -> str:
    import json
    return json.dumps(d, default=str)


async def _log_tool_call(
    db: AsyncSession,
    *,
    agent_id: str,
    tool_name: str,
    input_summary: dict,
    output_len: int,
    sc_cost: float,
    latency_ms: int,
    error: str | None = None,
) -> None:
    await db.execute(
        text(
            """
            INSERT INTO tool_calls
                (id, agent_id, tool_name, input_json, output_len,
                 sc_cost, latency_ms, error, created_at)
            VALUES
                (:id, :agent_id, :tool_name, CAST(:input_json AS JSONB),
                 :output_len, :sc_cost, :latency_ms, :error, NOW())
            """
        ),
        {
            "id": str(uuid.uuid4()),
            "agent_id": agent_id,
            "tool_name": tool_name,
            "input_json": _json_dump(input_summary),
            "output_len": output_len,
            "sc_cost": sc_cost,
            "latency_ms": latency_ms,
            "error": error,
        },
    )


async def _charge(
    db: AsyncSession,
    agent: Agent,
    *,
    amount: float,
    reason: str,
    ref: str,
    note: str,
) -> float:
    if amount <= 0:
        return 0.0
    try:
        await record_transaction(
            db,
            agent_id=agent.id,
            delta=-amount,
            reason=reason,
            ref_id=ref,
            note=note,
        )
    except InsufficientFundsError as e:
        raise HTTPException(
            status_code=402,
            detail={
                "error": "insufficient_sc",
                "balance": e.balance,
                "needed": amount,
                "tool": reason,
            },
        )
    return amount


# ── /health (public) ───────────────────────────────────────────────────────

@router.get("/health")
async def health() -> dict:
    pillow_ok = False
    fonts_ok = False
    try:
        import PIL  # noqa: F401
        pillow_ok = True
    except ImportError:
        pass
    fonts_ok = os.path.exists(FONT_BOLD) and os.path.exists(FONT_REG)
    return {
        "service": "commons.logo",
        "version": "1.0.0",
        "available": {
            "pillow": pillow_ok,
            "fonts": fonts_ok,
            "generate": pillow_ok and fonts_ok,
            "variations": pillow_ok and fonts_ok,
        },
        "all_available": pillow_ok and fonts_ok,
        "styles": list(STYLES),
        "sc_cost": SC_COST,
        "max_variations": MAX_VARIATIONS,
        "canvas_px": CANVAS,
    }


# ── /generate ──────────────────────────────────────────────────────────────

@router.post("/generate")
async def generate(
    body: GenerateRequest,
    db: AsyncSession = Depends(get_db),
    agent: Agent = Depends(require_service_key_and_agent),
) -> Response:
    if body.style not in STYLES:
        raise HTTPException(status_code=400, detail="bad_style")

    t0 = time.monotonic()
    ref = str(uuid.uuid4())
    cost = SC_COST["generate"]
    await _charge(
        db, agent,
        amount=cost,
        reason="commons.logo.generate",
        ref=ref,
        note=f"Logo Maker: {body.style}",
    )

    try:
        color = _pick_color(body.brand_name, body.color_hex)
        png = render_logo(body.brand_name, body.style, color)

        await _log_tool_call(
            db, agent_id=agent.id, tool_name="commons.logo.generate",
            input_summary={
                "brand_name": body.brand_name,
                "style": body.style,
                "color_hex": body.color_hex,
            },
            output_len=len(png),
            sc_cost=cost,
            latency_ms=int((time.monotonic() - t0) * 1000),
        )
        await db.commit()

        safe_name = "".join(c for c in body.brand_name if c.isalnum() or c in "-_")[:32] or "logo"
        return Response(
            content=png,
            media_type="image/png",
            headers={
                "Content-Disposition": f'attachment; filename="{safe_name}_{body.style}.png"',
                "X-SC-Cost": str(cost),
                "X-Style": body.style,
                "X-Color": "#%02x%02x%02x" % color,
            },
        )
    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail={"error": "generate_failed", "msg": str(e)[:200]})


# ── /variations ────────────────────────────────────────────────────────────

@router.post("/variations")
async def variations(
    body: VariationsRequest,
    db: AsyncSession = Depends(get_db),
    agent: Agent = Depends(require_service_key_and_agent),
) -> Response:
    if body.style not in STYLES:
        raise HTTPException(status_code=400, detail="bad_style")
    if body.count < 1 or body.count > MAX_VARIATIONS:
        raise HTTPException(status_code=400, detail="bad_count")

    t0 = time.monotonic()
    ref = str(uuid.uuid4())
    cost = SC_COST["variation_each"] * body.count
    await _charge(
        db, agent,
        amount=cost,
        reason="commons.logo.variations",
        ref=ref,
        note=f"Logo Maker variations: {body.style} x{body.count}",
    )

    try:
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for i in range(body.count):
                color = _pick_color(body.brand_name, None, salt=i)
                png = render_logo(body.brand_name, body.style, color)
                zf.writestr(f"variation_{i + 1:02d}.png", png)
        body_bytes = zip_buf.getvalue()

        await _log_tool_call(
            db, agent_id=agent.id, tool_name="commons.logo.variations",
            input_summary={
                "brand_name": body.brand_name,
                "style": body.style,
                "count": body.count,
            },
            output_len=len(body_bytes),
            sc_cost=cost,
            latency_ms=int((time.monotonic() - t0) * 1000),
        )
        await db.commit()

        safe_name = "".join(c for c in body.brand_name if c.isalnum() or c in "-_")[:32] or "logo"
        return Response(
            content=body_bytes,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{safe_name}_variations.zip"',
                "X-SC-Cost": str(cost),
                "X-Style": body.style,
                "X-Count": str(body.count),
            },
        )
    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail={"error": "variations_failed", "msg": str(e)[:200]})
