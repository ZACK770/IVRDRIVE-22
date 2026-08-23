"""Tools the bot may call mid-call, plus their Gemini function declarations.

Everything here is lazy on purpose: nothing is loaded when a call starts. The
customer record and the previous call are fetched only if the conversation
actually needs them, which keeps the first response fast.
"""

from __future__ import annotations

import logging
import re
import threading
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select

from app import db, dispatch, drivers, loyalty, notify, pbx, referrals

log = logging.getLogger("tools")

#: A caller who redials within this window is treated as continuing one errand.
RECENT_CALL_MINUTES = 10


def _normalise_place(name: str) -> str:
    return "".join(c for c in (name or "") if c not in "-,.'\"()[]/").replace(" ", "").lower()


def _extract_price_from_text(text: str, origin: str, destination: str) -> int | None:
    """Scan a Hebrew price knowledge block for a route and a price."""
    if not text:
        return None
    target_origin = _normalise_place(origin)
    target_destination = _normalise_place(destination)
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        normalised = _normalise_place(line)
        # Match the route in either direction.
        if target_origin in normalised and target_destination in normalised:
            # Find the first integer that looks like a price (typically at the end).
            numbers = [int(n) for n in re.findall(r"\d+", line) if int(n) > 10]
            if numbers:
                return numbers[-1]
    return None


DECLARATIONS: list[dict[str, Any]] = [
    {
        "name": "get_customer",
        "description": "פרטי לקוח לפי מספר טלפון: שם, כתובת איסוף מועדפת והערות.",
        "parameters": {
            "type": "object",
            "properties": {
                "phone": {"type": "string", "description": "מספר טלפון. השאר ריק למתקשר הנוכחי."}
            },
        },
    },
    {
        "name": "get_recent_call",
        "description": (
            "השיחה הקודמת של אותו מתקשר בעשר הדקות האחרונות, אם הייתה. "
            "השתמש בזה כשנראה שהלקוח ממשיך שיחה קודמת."
        ),
        "parameters": {"type": "object", "properties": {"phone": {"type": "string"}}},
    },
    {
        "name": "get_points",
        "description": ("מצב הקרדיטים של המתקשר במועדון הנוסעים, וכמה קרדיטים חסרים לנסיעת חינם."),
        "parameters": {"type": "object", "properties": {"phone": {"type": "string"}}},
    },
    {
        "name": "get_driver_reputation",
        "description": (
            "מוניטין אמיתי של נהג לפי מספר טלפון: ציון כללי, דירוג, מספר נסיעות, "
            "שנת רכב, דגם וסטטוס. השתמש בזה כשנהג שואל על המוניטין או הציון שלו."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "phone": {
                    "type": "string",
                    "description": "מספר טלפון. השאר ריק למתקשר הנוכחי.",
                }
            },
        },
    },
    {
        "name": "get_passenger_ride_history",
        "description": (
            "היסטוריית נסיעות אחרונות של נוסע לפי מספר טלפון. "
            "מחזירה מוצא, יעד, מחיר, סטטוס ותאריך. השתמש בזה כשהלקוח שואל על נסיעות קודמות."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "phone": {
                    "type": "string",
                    "description": "מספר טלפון. השאר ריק למתקשר הנוכחי.",
                },
                "limit": {
                    "type": "integer",
                    "description": "כמה נסיעות להחזיר. ברירת מחדל: 5.",
                },
            },
        },
    },
    {
        "name": "lookup_price",
        "description": (
            "בדיקת מחיר למסלול מוצא-יעד. מחפש תחילה ברשימת המחירים המוגדרת, "
            "ואם אין שם — מחשב ממחירי הזמנות קודמות."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "origin": {"type": "string", "description": "כתובת מוצא"},
                "destination": {"type": "string", "description": "כתובת יעד"},
            },
            "required": ["origin", "destination"],
        },
    },
    {
        "name": "save_order",
        "description": (
            "שמירת ההזמנה בסיום ופתיחת מכרז לנהגים. קרא לזה רק אחרי שהלקוח אישר את הפרטים. "
            "אם הלקוח מבקש נהג ספציפי "
            "(רכב חדש, נהג מבוגר, בלי סמארטפון וכו'), מלא את tender_filters."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "origin": {"type": "string"},
                "destination": {"type": "string"},
                "passengers": {"type": "integer"},
                "pickup_time": {"type": "string", "description": "מועד הנסיעה כפי שנמסר"},
                "price": {"type": "number"},
                "notes": {"type": "string"},
                "vehicle_type": {
                    "type": "string",
                    "description": "סוג רכב מבוקש, למשל סיאנה או טסלה",
                },
                "luggage": {"type": "string", "description": "כמות מזוודות או מטען"},
                "special_requests": {"type": "string", "description": "בקשות מיוחדות נוספות"},
                "tender_area": {
                    "type": "string",
                    "description": "אזור לצינתוק. ברירת מחדל: מוצא הנסיעה.",
                },
                "tender_filters": {
                    "type": "object",
                    "description": (
                        "סינון נהגים: min_car_year, min_age, min_rating, min_seats, "
                        "smartphone (true/false), voice_offers (true/false), "
                        "vehicle_type (למשל סיאנה), tiers (['standard','pro','pro_plus','premium'])"
                    ),
                    "properties": {
                        "min_car_year": {"type": "integer"},
                        "min_seats": {"type": "integer"},
                        "min_age": {"type": "integer"},
                        "min_rating": {"type": "number"},
                        "smartphone": {"type": "boolean"},
                        "voice_offers": {"type": "boolean"},
                        "vehicle_type": {"type": "string"},
                        "tiers": {"type": "array", "items": {"type": "string"}},
                    },
                },
                "tender_window_seconds": {
                    "type": "integer",
                    "description": "כמה שניות להמתין להצעות. ברירת מחדל: 10.",
                },
            },
            "required": ["origin", "destination", "passengers"],
        },
    },
    {
        "name": "transfer_to_representative",
        "description": (
            "העבר את השיחה לנציג אנושי. השתמש בזה רק אם הלקוח מבקש במפורש נציג "
            "אחרי שניסית לעזור לו, ולא כפתרון ראשון."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "hangup_call",
        "description": "נתק את השיחה לאחר שסיכמת את ההזמנה והלקוח אישר. קרא לזה בסיום.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "redeem_order",
        "description": "מימוש קרדיטים לתשלום הנסיעה האחרונה שנשמרה בשיחה. לפני זה בדוק get_points.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "create_referral",
        "description": "שיוך מספר טלפון של חבר/ת למבצע שתפו וסעו. שולחת צינתוק לאישור.",
        "parameters": {
            "type": "object",
            "properties": {
                "invited_phone": {
                    "type": "string",
                    "description": "מספר הטלפון להזמנה",
                },
            },
            "required": ["invited_phone"],
        },
    },
]


def _open_tender(
    order_id: int,
    area: str | None,
    filters: dict[str, Any] | None,
    window_seconds: int | None,
) -> None:
    """Open the driver auction off the bot's call thread."""
    try:
        with db.session_scope() as session:
            order = session.get(db.Order, order_id)
            if order is None:
                log.warning("order %s not found for auto tender", order_id)
                return
            dispatch.open_tender(
                session,
                order,
                area=area,
                filters=filters,
                window_seconds=window_seconds,
                actor="bot",
            )
    except Exception:
        log.exception("auto tender failed for order %s", order_id)


class ToolContext:
    """Binds tool calls to one call: caller id, call id, and a per-call cache."""

    def __init__(self, call_id: str, caller: str) -> None:
        self.call_id = call_id
        self.caller = db.normalize_phone(caller)
        self._cache: dict[str, Any] = {}
        self.saved_order_id: int | None = None

    def run(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        handlers = {
            "get_customer": self._get_customer,
            "get_recent_call": self._get_recent_call,
            "get_points": self._get_points,
            "get_driver_reputation": self._get_driver_reputation,
            "get_passenger_ride_history": self._get_passenger_ride_history,
            "lookup_price": self._lookup_price,
            "save_order": self._save_order,
            "hangup_call": self._hangup_call,
            "transfer_to_representative": self._transfer_to_representative,
            "redeem_order": self._redeem_order,
            "create_referral": self._create_referral,
        }
        handler = handlers.get(name)
        if handler is None:
            return {"error": f"unknown tool {name}"}
        key = f"{name}:{sorted(args.items())}"
        if name != "save_order" and key in self._cache:
            return self._cache[key]
        try:
            result = handler(args)
        except Exception as exc:
            log.exception("[%s] tool %s failed", self.call_id, name)
            return {"error": f"{type(exc).__name__}: {exc}"}
        self._cache[key] = result
        log.info("[%s] tool %s(%s) -> %s", self.call_id, name, args, result)
        return result

    # -------------------------------------------------------------- handlers

    def _get_customer(self, args: dict[str, Any]) -> dict[str, Any]:
        phone = db.normalize_phone(args.get("phone") or self.caller)
        with db.session_scope() as session:
            row = session.scalars(select(db.Customer).where(db.Customer.phone == phone)).first()
            if row is None:
                return {"found": False, "phone": phone}
            return {
                "found": True,
                "phone": row.phone,
                "name": row.name,
                "default_pickup": row.default_pickup,
                "notes": row.notes,
            }

    def _get_recent_call(self, args: dict[str, Any]) -> dict[str, Any]:
        phone = db.normalize_phone(args.get("phone") or self.caller)
        with db.session_scope() as session:
            row = db.recent_call(session, phone, RECENT_CALL_MINUTES)
            if row is None:
                return {"found": False}
            last_order = session.scalars(
                select(db.Order)
                .where(db.Order.phone == phone)
                .order_by(db.Order.created_at.desc())
                .limit(1)
            ).first()
            return {
                "found": True,
                "minutes_ago": round((datetime.utcnow() - row.started_at).total_seconds() / 60, 1),
                "summary": row.summary,
                "transcript_tail": (row.transcript or "")[-1500:],
                "last_order": (
                    {
                        "origin": last_order.origin,
                        "destination": last_order.destination,
                        "passengers": last_order.passengers,
                        "pickup_time": last_order.pickup_time,
                        "price": last_order.price,
                        "vehicle_type": last_order.vehicle_type,
                        "luggage": last_order.luggage,
                        "special_requests": last_order.special_requests,
                    }
                    if last_order
                    else None
                ),
            }

    def _get_points(self, args: dict[str, Any]) -> dict[str, Any]:
        phone = db.normalize_phone(args.get("phone") or self.caller)
        with db.session_scope() as session:
            balance = loyalty.balance(session, phone)
        cost = db.setting_int("redeem_points")
        return {
            "balance": balance,
            "free_ride_cost": cost,
            "can_redeem": balance >= cost,
            "missing": max(0, cost - balance),
        }

    def _get_driver_reputation(self, args: dict[str, Any]) -> dict[str, Any]:
        phone = db.normalize_phone(args.get("phone") or self.caller)
        with db.session_scope() as session:
            driver = drivers.get_by_phone(session, phone)
            if driver is None:
                return {"found": False, "phone": phone}
            score = drivers.general_score(driver)
            _, tier_label = drivers.tier_of(driver)
            avg = drivers.average_rating(driver)
            return {
                "found": True,
                "phone": phone,
                "status": driver.status,
                "general_score": score,
                "tier": tier_label,
                "average_rating": avg,
                "rating_count": driver.rating_count,
                "rides_done": driver.rides_done,
                "car_model": driver.car_model,
                "car_year": driver.car_year,
                "seats": driver.seats,
            }

    def _get_passenger_ride_history(self, args: dict[str, Any]) -> dict[str, Any]:
        phone = db.normalize_phone(args.get("phone") or self.caller)
        limit = int(args.get("limit") or 5)
        with db.session_scope() as session:
            rows = session.scalars(
                select(db.Order)
                .where(db.Order.phone == phone)
                .order_by(db.Order.created_at.desc())
                .limit(limit)
            ).all()
            rides = [
                {
                    "origin": row.origin,
                    "destination": row.destination,
                    "price": row.price,
                    "status": row.status,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
                for row in rows
            ]
        return {"found": bool(rides), "phone": phone, "rides": rides}

    def _lookup_price(self, args: dict[str, Any]) -> dict[str, Any]:
        origin = (args.get("origin") or "").strip()
        destination = (args.get("destination") or "").strip()
        if not origin or not destination:
            return {"found": False, "error": "חסר מוצא או יעד"}

        # 1. Try the configured price knowledge.
        botconfig = db.get_botconfig()
        knowledge = (botconfig.get("knowledge") or "").lower()
        price = _extract_price_from_text(knowledge, origin, destination)
        if price is not None:
            return {"found": True, "origin": origin, "destination": destination, "price": price}

        # 2. Fall back to the average of recent completed orders.
        with db.session_scope() as session:
            since = datetime.utcnow() - timedelta(days=90)
            rows = session.scalars(
                select(db.Order)
                .where(
                    db.Order.origin.ilike(f"%{origin}%"),
                    db.Order.destination.ilike(f"%{destination}%"),
                    db.Order.status == "done",
                    db.Order.price != None,
                    db.Order.created_at >= since,
                )
                .order_by(db.Order.created_at.desc())
                .limit(20)
            ).all()
            prices = [float(row.price) for row in rows if row.price is not None]
            if prices:
                return {
                    "found": True,
                    "origin": origin,
                    "destination": destination,
                    "price": round(sum(prices) / len(prices), 0),
                    "based_on": len(prices),
                }

        return {"found": False, "origin": origin, "destination": destination}

    def _save_order(self, args: dict[str, Any]) -> dict[str, Any]:
        with db.session_scope() as session:
            order = db.Order(
                call_id=self.call_id,
                phone=self.caller,
                origin=args.get("origin", ""),
                destination=args.get("destination", ""),
                passengers=int(args.get("passengers") or 1),
                pickup_time=args.get("pickup_time"),
                price=args.get("price"),
                notes=args.get("notes"),
                vehicle_type=args.get("vehicle_type"),
                luggage=args.get("luggage"),
                special_requests=args.get("special_requests"),
                area=args.get("tender_area") or args.get("origin", ""),
            )
            session.add(order)
            session.flush()
            self.saved_order_id = order.id
            payload = {
                "order_id": order.id,
                "phone": order.phone,
                "origin": order.origin,
                "destination": order.destination,
                "passengers": order.passengers,
                "pickup_time": order.pickup_time,
                "price": order.price,
                "notes": order.notes,
                "vehicle_type": order.vehicle_type,
                "luggage": order.luggage,
                "special_requests": order.special_requests,
            }
        # The order is now committed; anything that talks to the PBX happens off
        # the bot's call thread so the caller never waits on a ring-out.
        if db.setting_int("auto_tender"):
            filters = args.get("tender_filters")
            if not isinstance(filters, dict):
                filters = None
            window = args.get("tender_window_seconds")
            if window:
                try:
                    window = int(window)
                except (TypeError, ValueError):
                    window = None
            threading.Thread(
                target=_open_tender,
                args=(
                    payload["order_id"],
                    args.get("tender_area"),
                    filters,
                    window,
                ),
                daemon=True,
            ).start()
        threading.Thread(target=notify.send_order, args=(payload,), daemon=True).start()
        return {"saved": True, "order_id": payload["order_id"]}

    def _hangup_call(self, _args: dict[str, Any]) -> dict[str, Any]:
        return {"hung_up": True}

    def _transfer_to_representative(self, _args: dict[str, Any]) -> dict[str, Any]:
        ext = db.get_botconfig().get("representative_phone") or db.get_setting(
            "representative_extension"
        )
        if not ext:
            return {
                "ok": False,
                "message": "לא מוגדר מספר נציג; נא להגדיר בעמוד ההגדרות.",
            }
        return {"ok": True, "transfer_to": ext}

    def _redeem_order(self, _args: dict[str, Any]) -> dict[str, Any]:
        order_id = self.saved_order_id
        if order_id is None:
            return {"ok": False, "error": "אין הזמנה פעילה לפני הנסיעה האחרונה"}
        try:
            with db.session_scope() as session:
                order = session.get(db.Order, order_id)
                if order is None:
                    return {"ok": False, "error": "ההזמנה לא נמצאה"}
                result = loyalty.redeem_ride(session, order, actor="bot")
                if result["ok"]:
                    return {
                        "ok": True,
                        "spent": result["spent"],
                        "remaining": result["remaining"],
                        "price": order.price,
                    }
                return {"ok": False, "error": result.get("error", "אין אפשרות למימוש")}
        except Exception:
            log.exception("redeem_order failed for %s", order_id)
            return {"ok": False, "error": "שגיאה במימוש הקרדיטים"}

    def _create_referral(self, args: dict[str, Any]) -> dict[str, Any]:
        invited = db.normalize_phone(args.get("invited_phone") or "")
        if not invited:
            return {"ok": False, "error": "מספר הטלפון לא תקין"}
        try:
            with db.session_scope() as session:
                result = referrals.assign(
                    session,
                    referrer_phone=self.caller,
                    invited_phone=invited,
                    actor="bot",
                )
                if result["ok"]:
                    pbx.flash_call(
                        session,
                        invited,
                        kind="referral",
                        cid=None,
                    )
                return result
        except Exception:
            log.exception("create_referral failed: %s -> %s", self.caller, invited)
            return {"ok": False, "error": "שגיאה ביצירת שיוך"}
