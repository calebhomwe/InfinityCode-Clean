"""Infinity Code API router: credit engine (wallet, ledger, analytics).

Routes follow the monolith-extraction pattern: reads/writes app.state.credits,
never touches credentials, and every endpoint degrades to {"enabled": false}
when the wallet is not configured.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from main import app

router = APIRouter()


def _engine() -> Optional[Any]:
    engine = getattr(app.state, "credits", None)
    if engine is None or not getattr(engine, "enabled", False):
        return None
    return engine


class PackRequest(BaseModel):
    pack_id: str = Field(..., min_length=1, max_length=64)


class PlanRequest(BaseModel):
    plan: str = Field(..., min_length=1, max_length=32)


class LicenseRequest(BaseModel):
    key: str = Field(..., min_length=4, max_length=256)


@router.get("/credits")
def credits_status() -> Dict[str, Any]:
    """Wallet state: balance, plan, BYOK flag, monthly usage, rollover."""
    engine = _engine()
    if engine is None:
        return {"enabled": False}
    return engine.balance()


@router.get("/credits/plans")
def credits_plans() -> Dict[str, Any]:
    """Plan + pack catalog (config-driven; nothing to leak)."""
    engine = _engine()
    if engine is None:
        return {"enabled": False, "plans": [], "packs": []}
    return {
        "enabled": True,
        "plans": engine.plans,
        "packs": engine.packs,
    }


@router.get("/credits/ledger")
def credits_ledger(limit: int = Query(50, ge=1, le=500)) -> Dict[str, Any]:
    engine = _engine()
    if engine is None:
        return {"enabled": False, "entries": []}
    return {"enabled": True, "entries": engine.ledger(limit)}


@router.post("/credits/packs")
def credits_buy_pack(req: PackRequest) -> Dict[str, Any]:
    """Purchase a credit pack (MVP: simulated purchase; Stripe is later)."""
    engine = _engine()
    if engine is None:
        raise HTTPException(503, "Credit engine not configured")
    result = engine.add_pack(req.pack_id)
    if not result.get("ok"):
        raise HTTPException(400, result.get("detail", "unknown pack"))
    return result


@router.post("/credits/plan")
def credits_set_plan(req: PlanRequest) -> Dict[str, Any]:
    """Switch plan directly (MVP: config-driven plans)."""
    engine = _engine()
    if engine is None:
        raise HTTPException(503, "Credit engine not configured")
    result = engine.set_plan(req.plan)
    if not result.get("ok"):
        raise HTTPException(400, result.get("detail", "unknown plan"))
    return result


@router.post("/credits/license")
def credits_activate(req: LicenseRequest) -> Dict[str, Any]:
    """Offline license activation (key -> plan). Stripe checkout is later."""
    engine = _engine()
    if engine is None:
        raise HTTPException(503, "Credit engine not configured")
    result = engine.activate_license(req.key)
    if not result.get("ok"):
        raise HTTPException(401, result.get("detail", "invalid license key"))
    return result


@router.post("/credits/byok")
def credits_set_byok(enabled: bool = True) -> Dict[str, Any]:
    """Toggle bring-your-own-key mode (wallet bypass)."""
    engine = _engine()
    if engine is None:
        raise HTTPException(503, "Credit engine not configured")
    engine.set_byok(enabled)
    return {"ok": True, "byok": enabled}


@router.get("/credits/preview")
def credits_preview(
    model: str = Query(..., min_length=1),
    prompt: str = Query("", max_length=200_000),
    max_tokens: int = Query(4000, ge=1, le=200_000),
) -> Dict[str, Any]:
    """Pre-execution cost preview — the 'no surprises' feature."""
    engine = _engine()
    if engine is None:
        return {"enabled": False, "cost_usd": 0.0, "credits": 0.0}
    cost = engine.estimate_cost_usd(model, prompt, max_tokens)
    return {
        "enabled": True,
        "model": model,
        "cost_usd": cost,
        "credits": round(cost * engine.rate_credits_per_usd, 2),
    }


@router.get("/credits/analytics")
def credits_analytics() -> Dict[str, Any]:
    """Sales-ready usage analytics + savings + burn-rate forecast."""
    engine = _engine()
    if engine is None:
        return {"enabled": False}
    return engine.analytics()


@router.get("/credits/export")
def credits_export(format: str = Query("json", pattern="^(json|csv)$")) -> Any:
    """Usage export for expense reports / customer success."""
    engine = _engine()
    if engine is None:
        raise HTTPException(503, "Credit engine not configured")
    body = engine.export(format)
    from fastapi.responses import PlainTextResponse
    media = "text/csv" if format == "csv" else "application/json"
    return PlainTextResponse(body, media_type=media)
