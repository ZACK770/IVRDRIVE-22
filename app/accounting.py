"""The books: what came in, what went out, and what each driver owes.

Revenue is the office's commission on completed rides, not the fare — the fare
belongs to the driver, and counting it as income would overstate the business
several times over. Rides paid for with credits earn no commission but still
cost the club its liability, so they are reported separately rather than
quietly dropped.

Outstanding credits are a real liability: every unspent credit is a future free
ride, so the report values them at the current redemption rate.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import db


def _window(days: int) -> datetime:
    return datetime.utcnow() - timedelta(days=days)


def profit_and_loss(session: Session, days: int = 30) -> dict:
    since = _window(days)
    done = session.scalars(
        select(db.Order).where(db.Order.status == "done", db.Order.created_at >= since)
    ).all()
    rate = db.setting_float("commission_rate")

    fares = sum(float(o.price or 0.0) for o in done)
    commission = sum(
        float(o.commission if o.commission is not None else float(o.price or 0.0) * rate)
        for o in done
    )
    point_rides = [o for o in done if o.points_spent]
    expenses = session.scalars(
        select(db.Expense).where(db.Expense.spent_on >= since)
    ).all()
    expense_total = sum(float(e.amount or 0.0) for e in expenses)

    outstanding = int(
        session.scalar(select(func.coalesce(func.sum(db.PointsEntry.delta), 0))) or 0
    )
    redeem_cost = db.setting_int("redeem_points") or 1

    by_category: dict[str, float] = {}
    for row in expenses:
        by_category[row.category] = by_category.get(row.category, 0.0) + float(row.amount or 0.0)

    return {
        "days": days,
        "rides_done": len(done),
        "fares": round(fares, 2),
        "commission_income": round(commission, 2),
        "expenses": round(expense_total, 2),
        "profit": round(commission - expense_total, 2),
        "expenses_by_category": {k: round(v, 2) for k, v in sorted(by_category.items())},
        "point_rides": len(point_rides),
        "points_outstanding": outstanding,
        "points_liability_rides": round(outstanding / redeem_cost, 2),
    }


def driver_balance(session: Session, driver_id: int) -> float:
    """Total charges minus total payments for one driver."""
    total_charges = float(
        session.scalar(
            select(func.coalesce(func.sum(db.DriverCharge.amount), 0.0)).where(
                db.DriverCharge.driver_id == driver_id
            )
        )
        or 0.0
    )
    total_payments = float(
        session.scalar(
            select(func.coalesce(func.sum(db.DriverPayment.amount), 0.0)).where(
                db.DriverPayment.driver_id == driver_id
            )
        )
        or 0.0
    )
    return round(total_charges - total_payments, 2)


def driver_payments(session: Session, driver_id: int, days: int = 30) -> list[dict]:
    """Payments a driver made during the window."""
    since = _window(days)
    rows = session.scalars(
        select(db.DriverPayment)
        .where(
            db.DriverPayment.driver_id == driver_id,
            db.DriverPayment.paid_at >= since,
        )
        .order_by(db.DriverPayment.paid_at.desc())
    ).all()
    return [
        {
            "id": p.id,
            "amount": round(float(p.amount), 2),
            "paid_at": p.paid_at.isoformat() if p.paid_at else None,
            "method": p.method,
            "notes": p.notes,
        }
        for p in rows
    ]


def ensure_driver_charge(session: Session, order: db.Order) -> db.DriverCharge | None:
    """Create or update the driver's debt for a finished ride."""
    if not order.driver_id:
        return None
    rate = db.setting_float("commission_rate")
    amount = round(
        float(
            order.commission
            if order.commission is not None
            else float(order.price or 0.0) * rate
        ),
        2,
    )
    charge = session.scalars(
        select(db.DriverCharge).where(db.DriverCharge.order_id == order.id)
    ).first()
    if charge is None:
        charge = db.DriverCharge(
            driver_id=order.driver_id,
            order_id=order.id,
            amount=amount,
        )
        session.add(charge)
    else:
        charge.driver_id = order.driver_id
        charge.amount = amount
        charge.updated_at = datetime.utcnow()
    session.flush()
    return charge


def void_driver_charge(session: Session, order_id: int) -> None:
    """Remove a debt when a ride did not happen."""
    session.execute(
        db.DriverCharge.__table__.delete().where(db.DriverCharge.order_id == order_id)
    )


def add_driver_payment(
    session: Session,
    driver_id: int,
    amount: float,
    *,
    method: str | None = None,
    notes: str | None = None,
    actor: str = "system",
) -> db.DriverPayment:
    row = db.DriverPayment(
        driver_id=driver_id,
        amount=amount,
        method=method,
        notes=notes,
    )
    session.add(row)
    session.flush()
    db.log_action(
        session,
        "driver_payment_added",
        actor=actor,
        entity="driver",
        entity_id=driver_id,
        detail=f"{amount} {method}",
    )
    return row


def driver_statement(session: Session, driver_id: int, days: int = 30) -> dict:
    """What the office bills one driver: rides, charges, payments and balance."""
    since = _window(days)
    driver = session.get(db.Driver, driver_id)
    if driver is None:
        return {}
    rows = session.scalars(
        select(db.Order)
        .where(
            db.Order.driver_id == driver_id,
            db.Order.status == "done",
            db.Order.created_at >= since,
        )
        .order_by(db.Order.created_at)
    ).all()
    rate = db.setting_float("commission_rate")
    rides = [
        {
            "order_id": o.id,
            "date": (o.finished_at or o.created_at).isoformat(),
            "origin": o.origin,
            "destination": o.destination,
            "price": float(o.price or 0.0),
            "paid_with_points": bool(o.points_spent),
            "commission": round(
                float(o.commission if o.commission is not None else float(o.price or 0.0) * rate),
                2,
            ),
        }
        for o in rows
    ]
    payments = driver_payments(session, driver_id, days)
    total_charges = float(
        session.scalar(
            select(func.coalesce(func.sum(db.DriverCharge.amount), 0.0)).where(
                db.DriverCharge.driver_id == driver_id
            )
        )
        or 0.0
    )
    total_payments = float(
        session.scalar(
            select(func.coalesce(func.sum(db.DriverPayment.amount), 0.0)).where(
                db.DriverPayment.driver_id == driver_id
            )
        )
        or 0.0
    )
    return {
        "driver": {"id": driver.id, "name": driver.name, "phone": driver.phone},
        "days": days,
        "rides": rides,
        "payments": payments,
        "total_fares": round(sum(r["price"] for r in rides), 2),
        "total_commission": round(total_charges, 2),
        "total_payments": round(total_payments, 2),
        "balance": round(total_charges - total_payments, 2),
    }


def rides_by_driver(session: Session, days: int = 30) -> list[dict]:
    since = _window(days)
    rows = session.execute(
        select(
            db.Order.driver_id,
            func.count(db.Order.id),
            func.coalesce(func.sum(db.Order.price), 0.0),
            func.coalesce(func.sum(db.Order.commission), 0.0),
        )
        .where(
            db.Order.status == "done",
            db.Order.created_at >= since,
            db.Order.driver_id.is_not(None),
        )
        .group_by(db.Order.driver_id)
    ).all()
    names = {d.id: (d.name, d.phone) for d in session.scalars(select(db.Driver)).all()}
    charge_totals = dict(
        session.execute(
            select(
                db.DriverCharge.driver_id,
                func.coalesce(func.sum(db.DriverCharge.amount), 0.0),
            ).group_by(db.DriverCharge.driver_id)
        ).all()
    )
    payment_totals = dict(
        session.execute(
            select(
                db.DriverPayment.driver_id,
                func.coalesce(func.sum(db.DriverPayment.amount), 0.0),
            ).group_by(db.DriverPayment.driver_id)
        ).all()
    )
    out = []
    for driver_id, count, fares, commission in rows:
        name, phone = names.get(driver_id, (None, None))
        total_charges = float(charge_totals.get(driver_id, 0.0))
        total_payments = float(payment_totals.get(driver_id, 0.0))
        out.append(
            {
                "driver_id": driver_id,
                "name": name,
                "phone": phone,
                "rides": int(count),
                "fares": round(float(fares), 2),
                "commission": round(float(commission), 2),
                "total_charges": round(total_charges, 2),
                "total_payments": round(total_payments, 2),
                "balance": round(total_charges - total_payments, 2),
            }
        )
    return sorted(out, key=lambda row: -row["rides"])


def add_expense(
    session: Session, *, category: str, amount: float, note: str | None, actor: str
) -> db.Expense:
    row = db.Expense(category=category, amount=amount, note=note)
    session.add(row)
    session.flush()
    db.log_action(
        session,
        "expense_added",
        actor=actor,
        entity="expense",
        entity_id=row.id,
        detail=f"{category} {amount}",
    )
    return row
