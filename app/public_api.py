"""Public, token-free endpoints for driver self-registration and read-only
reference data used by the public registration page.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from app import db, drivers

router = APIRouter(prefix="/api/public", tags=["public"])

DRIVER_TERMS_VERSION = "driver-1"


@router.get("/areas")
def list_public_areas() -> dict:
    """Active area names only — no internal phone numbers."""
    with db.session_scope() as session:
        rows = session.scalars(
            select(db.Area.name).where(db.Area.active == True).order_by(db.Area.name)
        ).all()
        return {"areas": [str(name) for name in rows]}


@router.post("/drivers")
def register_driver(request: Request, payload: dict) -> dict:
    """Self-registration: creates a pending driver record with explicit terms
    consent. No admin token is required, but the terms checkboxes are mandatory.
    """
    phone = str(payload.get("phone") or "").strip()
    name = str(payload.get("name") or "").strip()
    car_model = str(payload.get("car_model") or "").strip()
    seats = payload.get("seats")
    areas = payload.get("areas")

    if not phone:
        raise HTTPException(status_code=422, detail="phone required")
    if not name:
        raise HTTPException(status_code=422, detail="name required")
    if not car_model:
        raise HTTPException(status_code=422, detail="car_model required")
    try:
        seats_int = int(seats) if seats is not None else 4
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="seats must be a number") from exc
    if seats_int < 1:
        raise HTTPException(status_code=422, detail="seats must be at least 1")
    if not isinstance(areas, list) or not areas:
        raise HTTPException(status_code=422, detail="at least one area required")

    terms_accepted = bool(payload.get("terms_accepted"))
    has_documents = bool(payload.get("has_documents"))
    accepts_rides_limit = bool(payload.get("accepts_rides_limit"))
    if not (terms_accepted and has_documents and accepts_rides_limit):
        raise HTTPException(
            status_code=422,
            detail="terms must be accepted and both liability clauses confirmed",
        )

    client_ip = request.client.host if request.client else None
    phone = db.normalize_phone(phone)

    with db.session_scope() as session:
        existing = drivers.get_by_phone(session, phone)
        if existing is not None and existing.status != "pending":
            raise HTTPException(status_code=409, detail="phone already registered")

        driver = drivers.register(
            session,
            phone,
            name=name or None,
            home_area=str(areas[0]).strip() if areas else None,
            car_model=car_model or None,
            seats=seats_int,
            smartphone=True,
            voice_offers=True,
            status="pending",
            terms_accepted=True,
            terms_accepted_at=datetime.utcnow(),
            terms_version=DRIVER_TERMS_VERSION,
            terms_ip=client_ip,
            has_documents=True,
            accepts_rides_limit=True,
        )
        drivers.set_areas(session, driver, [str(a).strip() for a in areas if a])
        return {
            "ok": True,
            "id": driver.id,
            "phone": driver.phone,
            "status": driver.status,
            "message": "הרישום התקבל ויעבור אישור משרד",
        }
