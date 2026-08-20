"""Persistence for the Drivers dispatch bot.

SQLAlchemy over Postgres in production and SQLite for local runs. Render's
instance filesystem is ephemeral, so a file database loses every order on
deploy; point ``BOT_DB_URL`` at the managed Postgres and the models are the
same either way.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    create_engine,
    func,
    inspect,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from app import prompt


def _engine_url(raw: str) -> str:
    """Render hands out `postgres://…`, which SQLAlchemy 2 rejects, and we ship
    psycopg 3 rather than the psycopg2 the default dialect expects."""
    if raw.startswith("postgres://"):
        raw = "postgresql://" + raw[len("postgres://") :]
    if raw.startswith("postgresql://"):
        raw = "postgresql+psycopg://" + raw[len("postgresql://") :]
    return raw


DB_URL = _engine_url(os.getenv("BOT_DB_URL", "sqlite:///./bot.db"))

#: `pool_pre_ping` costs a round trip per checkout but survives Postgres closing
#: idle connections between calls, which on a quiet dispatch line is the norm.
engine = create_engine(DB_URL, future=True, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


class Base(DeclarativeBase):
    pass


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    phone: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(120))
    default_pickup: Mapped[str | None] = mapped_column(String(200))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    #: Personal area, set by the caller over the phone or by the dispatcher.
    preferred_driver_phone: Mapped[str | None] = mapped_column(String(32))
    blocked_driver_phone: Mapped[str | None] = mapped_column(String(32))
    #: A caller who opted out is never dialled for a rating and never appears
    #: in a campaign recipient list.
    no_marketing: Mapped[bool] = mapped_column(Boolean, default=False)
    club_joined_at: Mapped[datetime | None] = mapped_column(DateTime)


class Price(Base):
    __tablename__ = "prices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    origin: Mapped[str] = mapped_column(String(120), index=True)
    destination: Mapped[str] = mapped_column(String(120), index=True)
    price: Mapped[float] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    call_id: Mapped[str] = mapped_column(String(64), index=True)
    phone: Mapped[str] = mapped_column(String(32), index=True)
    origin: Mapped[str] = mapped_column(String(200))
    destination: Mapped[str] = mapped_column(String(200))
    passengers: Mapped[int] = mapped_column(Integer, default=1)
    pickup_time: Mapped[str | None] = mapped_column(String(120))
    price: Mapped[float | None] = mapped_column(Float)
    notes: Mapped[str | None] = mapped_column(Text)
    vehicle_type: Mapped[str | None] = mapped_column(String(120))
    luggage: Mapped[str | None] = mapped_column(Text)
    special_requests: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    exported: Mapped[bool] = mapped_column(Boolean, default=False)
    #: Lifecycle spine: loyalty points, driver payouts and the rating call all
    #: hang off the order actually reaching `done`.
    status: Mapped[str] = mapped_column(String(24), default="new", index=True)
    driver_name: Mapped[str | None] = mapped_column(String(120))
    driver_phone: Mapped[str | None] = mapped_column(String(32))
    driver_id: Mapped[int | None] = mapped_column(Integer, index=True)
    area: Mapped[str | None] = mapped_column(String(120), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    #: Points spent to get this ride for free, and the commission the driver
    #: owes on it — both frozen at completion so later rule changes cannot
    #: rewrite past money.
    points_spent: Mapped[int] = mapped_column(Integer, default=0)
    commission: Mapped[float | None] = mapped_column(Float)


class CallLog(Base):
    __tablename__ = "calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    call_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(32), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime)
    transcript: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    #: Raw bridge stats (turns, reply_latency_ms, tool_calls) as JSON.
    stats_json: Mapped[str | None] = mapped_column(Text)


class Driver(Base):
    __tablename__ = "drivers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    phone: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(120))
    #: Home area; the areas the driver actually wants offers from live in
    #: `driver_areas`, because most drivers work more than one.
    home_area: Mapped[str | None] = mapped_column(String(120))
    car_model: Mapped[str | None] = mapped_column(String(120))
    car_year: Mapped[int | None] = mapped_column(Integer)
    seats: Mapped[int] = mapped_column(Integer, default=4)
    birth_year: Mapped[int | None] = mapped_column(Integer)
    joined_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    #: A driver without a smartphone can only be reached by the phone line, so
    #: the dispatcher filters on it when a ride needs an app-based hand-off.
    smartphone: Mapped[bool] = mapped_column(Boolean, default=True)
    #: Paying drivers hear a spoken offer and press 1; the rest get the free
    #: flash call and have to dial the area number back.
    voice_offers: Mapped[bool] = mapped_column(Boolean, default=False)
    quiet_from: Mapped[int | None] = mapped_column(Integer)
    quiet_to: Mapped[int | None] = mapped_column(Integer)
    rating_sum: Mapped[float] = mapped_column(Float, default=0.0)
    rating_count: Mapped[int] = mapped_column(Integer, default=0)
    rides_done: Mapped[int] = mapped_column(Integer, default=0)
    #: Where the driver last reported being, and when. Freshness is what makes
    #: this useful, so the timestamp matters as much as the area.
    last_area: Mapped[str | None] = mapped_column(String(120))
    last_area_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    notes: Mapped[str | None] = mapped_column(Text)


class DriverArea(Base):
    """An area a driver wants offers from. Absence of rows means 'all areas'."""

    __tablename__ = "driver_areas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    driver_id: Mapped[int] = mapped_column(Integer, index=True)
    area: Mapped[str] = mapped_column(String(120), index=True)


class Area(Base):
    """A dispatch area and the phone number its flash call tells drivers to
    ring back — a separate number per area is what makes the callback itself
    carry the routing information."""

    __tablename__ = "areas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    callback_number: Mapped[str | None] = mapped_column(String(32))
    #: Caller ID the flash call presents, so the driver's phone shows which
    #: area is calling before anything is answered.
    flash_cid: Mapped[str | None] = mapped_column(String(16))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class LocationUpdate(Base):
    __tablename__ = "location_updates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    driver_id: Mapped[int] = mapped_column(Integer, index=True)
    area: Mapped[str] = mapped_column(String(120), index=True)
    #: `ride_finished` is free and trustworthy; `declared` is the once-a-day
    #: self report, which is why the two are told apart when scoring.
    source: Mapped[str] = mapped_column(String(24), default="declared")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class Tender(Base):
    """One ride offered to the drivers of an area, with a short window during
    which everyone who wants it can bid before the algorithm picks."""

    __tablename__ = "tenders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(Integer, index=True)
    area: Mapped[str | None] = mapped_column(String(120), index=True)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    closes_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    awarded_driver_id: Mapped[int | None] = mapped_column(Integer)
    awarded_at: Mapped[datetime | None] = mapped_column(DateTime)
    #: The dispatcher's selection filters, kept so the same tender can be
    #: replayed or re-blasted without re-entering them.
    filters_json: Mapped[str | None] = mapped_column(Text)
    #: Name of the uploaded TTS file in the PBX audio library.
    offer_audio: Mapped[str | None] = mapped_column(String(120))
    #: PBX campaign id when paid drivers are rung via the voice campaign API.
    campaign_id: Mapped[str | None] = mapped_column(String(32))
    notified: Mapped[int] = mapped_column(Integer, default=0)


class TenderBid(Base):
    __tablename__ = "tender_bids"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tender_id: Mapped[int] = mapped_column(Integer, index=True)
    driver_id: Mapped[int] = mapped_column(Integer, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    won: Mapped[bool] = mapped_column(Boolean, default=False)


class FlashCall(Base):
    """Log of every outbound flash call, and the debounce ledger: the PBX
    rejects two calls to the same number within two minutes."""

    __tablename__ = "flash_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    phone: Mapped[str] = mapped_column(String(32), index=True)
    driver_id: Mapped[int | None] = mapped_column(Integer, index=True)
    tender_id: Mapped[int | None] = mapped_column(Integer, index=True)
    cid: Mapped[str | None] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(24), default="tender")
    status: Mapped[str] = mapped_column(String(16), default="sent")
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class PointsEntry(Base):
    """Append-only loyalty ledger. The balance is the sum of the rows, so a
    correction is another row and history is never rewritten."""

    __tablename__ = "points_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    phone: Mapped[str] = mapped_column(String(32), index=True)
    delta: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(32), index=True)
    order_id: Mapped[int | None] = mapped_column(Integer, index=True)
    referral_id: Mapped[int | None] = mapped_column(Integer, index=True)
    actor: Mapped[str] = mapped_column(String(64), default="system")
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class Referral(Base):
    """'Share and ride': a caller names a number, that number confirms by
    ringing in within 24 hours, and rides it makes for the next 30 days earn
    the referrer points."""

    __tablename__ = "referrals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    referrer_phone: Mapped[str] = mapped_column(String(32), index=True)
    invited_phone: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)
    credit_until: Mapped[datetime | None] = mapped_column(DateTime)
    rewarded_orders: Mapped[int] = mapped_column(Integer, default=0)


class RatingRequest(Base):
    """One rating call per finished ride, tracked so it can never go out twice."""

    __tablename__ = "rating_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    driver_id: Mapped[int | None] = mapped_column(Integer, index=True)
    phone: Mapped[str] = mapped_column(String(32), index=True)
    due_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    status: Mapped[str] = mapped_column(String(16), default="scheduled", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    score: Mapped[int | None] = mapped_column(Integer)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Expense(Base):
    __tablename__ = "expenses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    spent_on: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    category: Mapped[str] = mapped_column(String(64), index=True)
    amount: Mapped[float] = mapped_column(Float)
    note: Mapped[str | None] = mapped_column(Text)


class ActionLog(Base):
    __tablename__ = "action_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(64), default="system", index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    entity: Mapped[str | None] = mapped_column(String(64))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[str | None] = mapped_column(Text)


class Setting(Base):
    """Business rules the operator changes without a deploy: gift size, points
    per shekel, redemption cost, commission."""

    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    value: Mapped[str] = mapped_column(String(200))


class IvrSession(Base):
    """Module API is stateless per request, so the few digits a driver already
    entered live here, keyed by the PBX call id."""

    __tablename__ = "ivr_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    call_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(32), index=True)
    step: Mapped[str] = mapped_column(String(64), default="start")
    data: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, index=True
    )


class Prompt(Base):
    __tablename__ = "prompts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    content: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


@contextmanager
def session_scope() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def normalize_phone(phone: str) -> str:
    """`+972-52-718-0504`, `0527180504` and `972527180504` are one customer."""
    digits = re.sub(r"\D", "", phone or "")
    if digits.startswith("972"):
        digits = "0" + digits[3:]
    return digits


def normalize_place(place: str) -> str:
    text = (place or "").strip().lower()
    text = re.sub(r"^(ה|ל|מ|ב)?(עיר|רחוב)\s+", "", text)
    return re.sub(r"\s+", " ", text)


def recent_call(session: Session, phone: str, minutes: int) -> CallLog | None:
    """The short-term memory window: a caller who redials is still mid-errand."""
    cutoff = datetime.utcnow() - timedelta(minutes=minutes)
    stmt = (
        select(CallLog)
        .where(CallLog.phone == normalize_phone(phone), CallLog.started_at >= cutoff)
        .order_by(CallLog.started_at.desc())
        .limit(1)
    )
    return session.scalars(stmt).first()


def _place_match(session: Session, first: str, second: str) -> Price | None:
    """Try exact, then contained (for 'ירושלים' inside 'רחוב יפו 10 ירושלים')."""
    for first_col, second_col in (
        (Price.origin, Price.destination),
        (Price.destination, Price.origin),
    ):
        exact = session.scalars(
            select(Price).where(
                func.lower(first_col) == first,
                func.lower(second_col) == second,
            )
        ).first()
        if exact is not None:
            return exact
    # Containment match: pick the longest stored origin to avoid short substrings.
    hits: list[Price] = []
    for row in session.scalars(select(Price)).all():
        ro, rd = row.origin or "", row.destination or ""
        lo, ld = ro.lower(), rd.lower()
        if (first in lo or lo in first) and (second in ld or ld in second):
            hits.append(row)
        elif (first in ld or ld in first) and (second in lo or lo in second):
            hits.append(row)
    return max(hits, key=lambda r: len(r.origin or "") + len(r.destination or "")) if hits else None


def find_price(session: Session, origin: str, destination: str) -> Price | None:
    """Exact match on normalised names, then reverse direction, then loose match."""
    a, b = normalize_place(origin), normalize_place(destination)
    if a and b:
        hit = _place_match(session, a, b)
        if hit is not None:
            return hit
    for first, second in ((a, b), (b, a)):
        if not first or not second:
            continue
        for row in session.scalars(select(Price)).all():
            lo, ld = (row.origin or "").lower(), (row.destination or "").lower()
            if (first in lo or lo in first) and (second in ld or ld in second):
                return row
            if (first in ld or ld in first) and (second in lo or lo in second):
                return row
    return None


def prompt_is_edited(row: Prompt | None) -> bool:
    """The operator has typed their own prompt; do not overwrite it on deploy."""
    if row is None:
        return False
    if row.content == prompt.SYSTEM_PROMPT:
        return False
    return row.content not in prompt.LEGACY_PROMPTS


def get_prompt(name: str = "system") -> str:
    with session_scope() as session:
        row = session.scalars(select(Prompt).where(Prompt.name == name)).first()
        return row.content if row else prompt.SYSTEM_PROMPT


def set_prompt(name: str, content: str) -> None:
    with session_scope() as session:
        row = session.scalars(select(Prompt).where(Prompt.name == name)).first()
        if row is None:
            session.add(Prompt(name=name, content=content))
        else:
            row.content = content


def reset_prompt(name: str = "system") -> None:
    """Discard the DB override and reload the file default."""
    with session_scope() as session:
        row = session.scalars(select(Prompt).where(Prompt.name == name)).first()
        if row is not None:
            row.content = prompt.SYSTEM_PROMPT


def prompt_meta(name: str = "system") -> dict:
    """Tell the console whether the stored prompt has drifted from the default."""
    with session_scope() as session:
        row = session.scalars(select(Prompt).where(Prompt.name == name)).first()
        return {
            "content": row.content if row else prompt.SYSTEM_PROMPT,
            "edited": prompt_is_edited(row),
            "default": prompt.SYSTEM_PROMPT,
        }


def _add_missing_columns() -> None:
    """Poor man's migration: this schema only ever grows, and both backends take
    ADD COLUMN cheaply, so it does not yet warrant Alembic."""
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            existing = {col["name"] for col in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                spec = f"{column.name} {column.type.compile(engine.dialect)}"
                default = column.default
                if default is not None and default.is_scalar:
                    spec += f" DEFAULT {default.arg!r}"
                elif not column.nullable:
                    continue
                conn.exec_driver_sql(f"ALTER TABLE {table.name} ADD COLUMN {spec}")


#: Business rules the operator owns. Stored as strings so one table holds
#: them all; every reader goes through `setting_int` / `setting_float`.
#: `representative_extension` is the parallel-channel human target.
DEFAULT_SETTINGS: dict[str, str] = {
    #: Points per shekel of an order that actually completed.
    "points_per_shekel": "1",
    #: Welcome gift, granted once per phone on its first completed ride.
    "first_ride_gift": "50",
    #: What a free ride costs the passenger.
    "redeem_points": "500",
    #: Paid to the referrer for each completed ride of a number they brought.
    "referral_points": "30",
    #: Hours the invited number has to ring in and confirm the referral.
    "referral_confirm_hours": "24",
    #: How long after confirmation the referrer keeps earning on those rides.
    "referral_credit_days": "30",
    #: Bidding window, in seconds, before the algorithm picks a winner.
    "tender_window_seconds": "10",
    #: A location report older than this stops counting as "in the area".
    "location_fresh_hours": "10",
    #: Delay between a finished ride and the rating call.
    "rating_delay_minutes": "90",
    #: The cut the office takes, used by the driver statements.
    "commission_rate": "0.15",
    #: Where the PBX reaches us. Module API URLs are built from it, so a wrong
    #: value means calls that reach a menu and go nowhere.
    "public_base_url": os.getenv("PUBLIC_BASE_URL", ""),
    #: Open the bidding automatically when the bot saves an order, instead of
    #: waiting for a dispatcher to press the button.
    "auto_tender": "1",
    #: Stop a paid voice campaign once this many calls have been answered.
    "voice_campaign_stop_answered": "30",
    "representative_extension": "0527180504",
}


def get_setting(key: str, default: str | None = None) -> str:
    with session_scope() as session:
        row = session.scalars(select(Setting).where(Setting.key == key)).first()
        if row is not None:
            return row.value
    return DEFAULT_SETTINGS.get(key, default if default is not None else "")


def set_setting(key: str, value: str) -> None:
    with session_scope() as session:
        row = session.scalars(select(Setting).where(Setting.key == key)).first()
        if row is None:
            session.add(Setting(key=key, value=value))
        else:
            row.value = value


def setting_int(key: str) -> int:
    try:
        return int(float(get_setting(key)))
    except ValueError:
        return int(float(DEFAULT_SETTINGS.get(key, "0")))


def setting_float(key: str) -> float:
    try:
        return float(get_setting(key))
    except ValueError:
        return float(DEFAULT_SETTINGS.get(key, "0"))


def log_action(
    session: Session,
    action: str,
    *,
    actor: str = "system",
    entity: str | None = None,
    entity_id: str | int | None = None,
    detail: str | None = None,
) -> None:
    """Every points movement, tender award and driver change leaves a row —
    the club is money, so 'who changed this' has to be answerable."""
    session.add(
        ActionLog(
            actor=actor,
            action=action,
            entity=entity,
            entity_id=str(entity_id) if entity_id is not None else None,
            detail=detail,
        )
    )


def ensure_default_settings() -> None:
    """Seed missing business settings so a fresh or upgraded database has the
    intended defaults without overwriting values an operator already changed."""
    with session_scope() as session:
        for key, value in DEFAULT_SETTINGS.items():
            if value is None:
                continue
            row = session.scalars(select(Setting).where(Setting.key == key)).first()
            if row is None:
                session.add(Setting(key=key, value=value))


def init_db() -> None:
    Base.metadata.create_all(engine)
    _add_missing_columns()
    ensure_default_settings()
    with session_scope() as session:
        row = session.scalars(select(Prompt).where(Prompt.name == "system")).first()
        if row is None:
            session.add(Prompt(name="system", content=prompt.SYSTEM_PROMPT))
        elif not prompt_is_edited(row):
            row.content = prompt.SYSTEM_PROMPT
