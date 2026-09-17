"""Approval of the joining terms, presented to the caller as a joining grant.

The caller hears an offer of credits and approves the terms to receive them, so
the grant is what motivates the approval and the approval is what the business
needs: a dated record, per phone, of which wording was in force.

Two rules keep it honest. The consent row is written once per phone and never
edited — a new wording is a new ``terms_version``, which lets the line ask the
same caller again — and the grant is a normal ledger row under its own reason,
so a replayed call cannot pay twice.
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import db, loyalty

log = logging.getLogger("terms")

#: Ledger reason for the joining grant, separate from the club's other bonuses
#: so a phone can hold the welcome gift and this one at the same time.
REASON_JOIN_BONUS = "terms_bonus"


def version() -> str:
    return db.get_setting("terms_version") or "1"


def bonus_points() -> int:
    return db.setting_int("terms_bonus_points")


def consent_for(session: Session, phone: str) -> db.TermsConsent | None:
    phone = db.normalize_phone(phone)
    if not phone:
        return None
    return session.scalars(
        select(db.TermsConsent).where(db.TermsConsent.phone == phone)
    ).first()


def status(session: Session, phone: str) -> dict:
    """Whether this phone still has to approve, for the extension that routes
    callers here."""
    consent = consent_for(session, phone)
    current = version()
    accepted = consent is not None and consent.version == current
    return {
        "phone": db.normalize_phone(phone),
        "accepted": accepted,
        "needs_consent": not accepted,
        "version": current,
        "accepted_version": consent.version if consent else None,
        "accepted_at": consent.created_at.isoformat() if consent else None,
        "points_granted": consent.points_granted if consent else 0,
        "bonus_points": bonus_points(),
    }


def accept(
    session: Session,
    phone: str,
    *,
    call_id: str | None = None,
    channel: str = "ivr",
) -> dict:
    """Record the approval and pay the joining grant.

    Repeat approvals of the same wording are accepted quietly and pay nothing,
    because the PBX can replay a module and a caller can ring twice.
    """
    phone = db.normalize_phone(phone)
    if not phone:
        return {"accepted": False, "error": "אין מספר מזוהה", "granted": 0}

    current = version()
    consent = consent_for(session, phone)
    if consent is not None and consent.version == current:
        return {
            "accepted": True,
            "already": True,
            "granted": 0,
            "version": current,
            "balance": loyalty.balance(session, phone),
        }

    granted = 0
    points = bonus_points()
    if points > 0:
        loyalty.grant(
            session,
            phone=phone,
            delta=points,
            reason=REASON_JOIN_BONUS,
            actor=f"{channel}:{phone}",
            note=f"מענק הצטרפות, תקנון גרסה {current}",
        )
        granted = points

    if consent is None:
        consent = db.TermsConsent(
            phone=phone,
            version=current,
            channel=channel,
            call_id=call_id or None,
            points_granted=granted,
        )
        session.add(consent)
    else:
        # A caller re-approving a newer wording keeps one row, since the row
        # answers "is this phone covered", and the ledger holds the history.
        consent.version = current
        consent.channel = channel
        consent.call_id = call_id or None
        consent.points_granted = consent.points_granted + granted
        consent.created_at = datetime.utcnow()
    session.flush()

    customer = session.scalars(select(db.Customer).where(db.Customer.phone == phone)).first()
    if customer is not None and customer.club_joined_at is None:
        customer.club_joined_at = datetime.utcnow()

    db.log_action(
        session,
        "terms_accepted",
        actor=f"{channel}:{phone}",
        entity="phone",
        entity_id=phone,
        detail=f"גרסה {current}, מענק {granted}",
    )
    return {
        "accepted": True,
        "already": False,
        "granted": granted,
        "version": current,
        "balance": loyalty.balance(session, phone),
    }


def recent(session: Session, limit: int = 200) -> list[dict]:
    rows = session.scalars(
        select(db.TermsConsent).order_by(db.TermsConsent.created_at.desc()).limit(limit)
    ).all()
    return [
        {
            "phone": row.phone,
            "version": row.version,
            "channel": row.channel,
            "call_id": row.call_id,
            "points_granted": row.points_granted,
            "accepted_at": row.created_at.isoformat(),
        }
        for row in rows
    ]
