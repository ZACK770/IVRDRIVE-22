"""JSON API consumed by the dispatcher console.

The console is deployed as its own Render service so that a frontend build
never blocks a backend deploy; that makes this the only contract between them.
Everything here is JSON and CORS-enabled — no server-rendered HTML.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Header, HTTPException
from sqlalchemy import select

from app import db

ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")

#: The dispatcher moves an order along this line; `done` is what earns loyalty
#: credits and what the accounting side counts, so nothing else may imply it.
ORDER_STATUSES = ("new", "assigned", "on_route", "done", "cancelled")


def require_token(x_admin_token: Annotated[str | None, Header()] = None) -> None:
    """Open when `ADMIN_TOKEN` is unset, so local development needs no setup."""
    if ADMIN_TOKEN and x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="bad token")


#: Reads carry caller phone numbers and transcripts, so they are as sensitive
#: as the writes; the guard belongs on the router rather than per endpoint.
router = APIRouter(prefix="/api", tags=["api"], dependencies=[Depends(require_token)])


def _order_json(row: db.Order) -> dict:
    return {
        "id": row.id,
        "created_at": row.created_at.isoformat(),
        "phone": row.phone,
        "origin": row.origin,
        "destination": row.destination,
        "passengers": row.passengers,
        "pickup_time": row.pickup_time,
        "price": row.price,
        "notes": row.notes,
        "vehicle_type": row.vehicle_type,
        "luggage": row.luggage,
        "special_requests": row.special_requests,
        "status": row.status,
        "driver_name": row.driver_name,
        "driver_phone": row.driver_phone,
    }


def _call_usage(stats_json: str | None) -> dict:
    if not stats_json:
        return {}
    try:
        stats = json.loads(stats_json)
    except ValueError:
        return {}
    return stats if isinstance(stats, dict) else {}


@router.get("/orders")
def list_orders(limit: int = 200) -> dict:
    with db.session_scope() as session:
        rows = session.scalars(
            select(db.Order).order_by(db.Order.created_at.desc()).limit(limit)
        ).all()
        return {"orders": [_order_json(r) for r in rows]}


@router.patch("/orders/{order_id}")
def update_order(order_id: int, payload: Annotated[dict, Body()]) -> dict:
    status = payload.get("status")
    if status is not None and status not in ORDER_STATUSES:
        raise HTTPException(status_code=422, detail=f"status must be one of {ORDER_STATUSES}")
    with db.session_scope() as session:
        row = session.get(db.Order, order_id)
        if row is None:
            raise HTTPException(status_code=404, detail="no such order")
        if status is not None:
            row.status = status
        if "driver_name" in payload:
            row.driver_name = payload["driver_name"] or None
        if "driver_phone" in payload:
            row.driver_phone = payload["driver_phone"] or None
        if "price" in payload:
            row.price = payload["price"]
        if "passengers" in payload:
            row.passengers = int(payload["passengers"]) or 1
        if "pickup_time" in payload:
            row.pickup_time = payload["pickup_time"] or None
        if "notes" in payload:
            row.notes = payload["notes"] or None
        if "vehicle_type" in payload:
            row.vehicle_type = payload["vehicle_type"] or None
        if "luggage" in payload:
            row.luggage = payload["luggage"] or None
        if "special_requests" in payload:
            row.special_requests = payload["special_requests"] or None
        session.flush()
        return _order_json(row)


@router.get("/summary")
def summary() -> dict:
    """The numbers the dispatcher screen shows above the board."""
    since = datetime.utcnow() - timedelta(hours=24)
    with db.session_scope() as session:
        orders = session.scalars(
            select(db.Order).where(db.Order.created_at >= since)
        ).all()
        calls = session.scalars(
            select(db.CallLog).where(db.CallLog.started_at >= since)
        ).all()
        by_status = {name: 0 for name in ORDER_STATUSES}
        for row in orders:
            by_status[row.status] = by_status.get(row.status, 0) + 1
        cost = sum(
            float((_call_usage(c.stats_json).get("usage") or {}).get("cost_usd") or 0.0)
            for c in calls
        )
        usd_to_ils = db.setting_float("usd_to_ils")
        return {
            "orders_24h": len(orders),
            "calls_24h": len(calls),
            "cost_usd_24h": round(cost, 4),
            "cost_ils_24h": round(cost * usd_to_ils, 2),
            "by_status": by_status,
        }


@router.get("/calls")
def list_calls(limit: int = 100) -> dict:
    with db.session_scope() as session:
        rows = session.scalars(
            select(db.CallLog).order_by(db.CallLog.started_at.desc()).limit(limit)
        ).all()
        usd_to_ils = db.setting_float("usd_to_ils")
        return {
            "calls": [
                {
                    "id": r.id,
                    "call_id": r.call_id,
                    "phone": r.phone,
                    "started_at": r.started_at.isoformat(),
                    "summary": r.summary,
                    "cost_usd": cost_usd,
                    "cost_ils": round(cost_usd * usd_to_ils, 2),
                }
                for r in rows
                for cost_usd in [
                    float((_call_usage(r.stats_json).get("usage") or {}).get("cost_usd") or 0.0)
                ]
            ]
        }


@router.get("/calls/{call_pk}")
def call_detail(call_pk: int) -> dict:
    with db.session_scope() as session:
        row = session.get(db.CallLog, call_pk)
        if row is None:
            raise HTTPException(status_code=404, detail="no such call")
        cost_usd = float((_call_usage(row.stats_json).get("usage") or {}).get("cost_usd") or 0.0)
        return {
            "id": row.id,
            "call_id": row.call_id,
            "phone": row.phone,
            "started_at": row.started_at.isoformat(),
            "transcript": row.transcript,
            "summary": row.summary,
            "cost_usd": cost_usd,
            "cost_ils": round(cost_usd * db.setting_float("usd_to_ils"), 2),
            "stats": _call_usage(row.stats_json),
        }


@router.get("/botconfig")
def get_botconfig() -> dict:
    return {"config": db.get_botconfig("system")}


@router.put("/botconfig")
def set_botconfig(payload: Annotated[dict, Body()]) -> dict:
    config = payload.get("config") or {}
    if not isinstance(config, dict):
        raise HTTPException(status_code=422, detail="config must be an object")
    db.set_botconfig("system", config)
    return {"config": db.get_botconfig("system")}


@router.post("/botconfig/reset")
def reset_botconfig() -> dict:
    db.set_botconfig("system", db.DEFAULT_BOTCONFIG)
    return {"config": db.get_botconfig("system")}


@router.get("/customers")
def list_customers() -> dict:
    with db.session_scope() as session:
        rows = session.scalars(select(db.Customer).order_by(db.Customer.phone)).all()
        return {
            "customers": [
                {
                    "id": r.id,
                    "phone": r.phone,
                    "name": r.name,
                    "default_pickup": r.default_pickup,
                    "notes": r.notes,
                }
                for r in rows
            ]
        }


@router.get("/prompt")
def get_prompt() -> dict:
    """Read-only preview of the generated prompt. Mutations go through /botconfig."""
    return db.prompt_meta("system")
