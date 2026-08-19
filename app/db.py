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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    exported: Mapped[bool] = mapped_column(Boolean, default=False)
    #: Lifecycle spine: loyalty points, driver payouts and the rating call all
    #: hang off the order actually reaching `done`.
    status: Mapped[str] = mapped_column(String(24), default="new", index=True)
    driver_name: Mapped[str | None] = mapped_column(String(120))
    driver_phone: Mapped[str | None] = mapped_column(String(32))


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


def find_price(session: Session, origin: str, destination: str) -> Price | None:
    """Exact match on normalised names, then the reverse direction."""
    a, b = normalize_place(origin), normalize_place(destination)
    for first, second in ((a, b), (b, a)):
        stmt = select(Price).where(
            func.lower(Price.origin) == first, func.lower(Price.destination) == second
        )
        if (hit := session.scalars(stmt).first()) is not None:
            return hit
    return None


DEFAULT_PROMPT = (
    "אתה נציג טלפוני של מוקד ההסעות 'דרייברים'. דבר עברית בלבד.\n"
    "חוק הברזל: כל תשובה שלך היא משפט אחד קצר, עד כ-12 מילים, ובו שאלה אחת "
    "בלבד. אל תפתח בהקדמה, אל תסביר מה אתה כן ולא יכול לעשות, ואל תחזור על "
    "כל הפרטים שנאספו — אישור קצר של הפרט האחרון בלבד. סכם את כל ההזמנה רק "
    "פעם אחת, לפני האישור הסופי.\n"
    "אם הלקוח שואל משהו שאינו קשור להסעות, ענה במשפט אחד קצר וחזור מיד "
    "לשאלה הבאה שחסרה לך.\n"
    "עליך לאסוף: כתובת מוצא, כתובת יעד, מספר נוסעים ומועד הנסיעה.\n"
    "אל תמציא מחיר לעולם — השתמש בכלי lookup_price. אם אין מחיר במערכת, אמור "
    "שנציג יחזור עם הצעת מחיר.\n"
    "אם הלקוח מתקשר שוב זמן קצר אחרי שיחה קודמת, השתמש ב-get_recent_call כדי "
    "להמשיך מאיפה שהפסקתם במקום להתחיל מחדש.\n"
    "בסיום, קרא ל-save_order כדי לשמור את ההזמנה, ואז סכם ללקוח בקצרה."
)


def get_prompt(name: str = "system") -> str:
    with session_scope() as session:
        row = session.scalars(select(Prompt).where(Prompt.name == name)).first()
        return row.content if row else DEFAULT_PROMPT


def set_prompt(name: str, content: str) -> None:
    with session_scope() as session:
        row = session.scalars(select(Prompt).where(Prompt.name == name)).first()
        if row is None:
            session.add(Prompt(name=name, content=content))
        else:
            row.content = content


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


def init_db() -> None:
    Base.metadata.create_all(engine)
    _add_missing_columns()
    with session_scope() as session:
        if session.scalars(select(Prompt).where(Prompt.name == "system")).first() is None:
            session.add(Prompt(name="system", content=DEFAULT_PROMPT))
