"""Minimal operator console: edit the prompt, manage prices, export orders.

Deliberately server-rendered HTML with no build step — the dispatcher needs to
change the bot's wording without a deploy, nothing more.
"""

from __future__ import annotations

import io
from datetime import datetime
from html import escape

from fastapi import APIRouter, Form, Response
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from openpyxl import Workbook
from sqlalchemy import select

from app import db

router = APIRouter(prefix="/admin", tags=["admin"])

_PAGE = """<!doctype html>
<html lang="he" dir="rtl"><head><meta charset="utf-8">
<title>מוקד דרייברים — ניהול</title>
<style>
 body{{font-family:system-ui,Arial;margin:2rem auto;max-width:900px;line-height:1.6}}
 textarea{{width:100%;height:16rem;font-family:inherit;font-size:1rem}}
 table{{border-collapse:collapse;width:100%;margin-bottom:1rem}}
 td,th{{border:1px solid #ddd;padding:.4rem .6rem;text-align:right}}
 nav a{{margin-left:1rem}} button{{padding:.4rem 1rem;font-size:1rem}}
</style></head><body>
<nav><a href="/admin">פרומפט</a><a href="/admin/prices">מחירים</a>
<a href="/admin/orders">הזמנות</a><a href="/">דיבאג</a></nav>
<h1>{title}</h1>
{body}
</body></html>"""


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(_PAGE.format(title=escape(title), body=body))


@router.get("", response_class=HTMLResponse)
def prompt_form() -> HTMLResponse:
    content = escape(db.get_prompt("system"))
    return _page(
        "עריכת פרומפט",
        f"""<form method="post" action="/admin/prompt">
        <textarea name="content">{content}</textarea>
        <p><button type="submit">שמור</button>
        השינוי נכנס לתוקף בשיחה הבאה.</p></form>""",
    )


@router.post("/prompt")
def prompt_save(content: str = Form(...)) -> RedirectResponse:
    db.set_prompt("system", content)
    return RedirectResponse("/admin", status_code=303)


@router.get("/prices", response_class=HTMLResponse)
def prices_page() -> HTMLResponse:
    with db.session_scope() as session:
        rows = session.scalars(select(db.Price).order_by(db.Price.origin)).all()
        listed = "".join(
            f"<tr><td>{escape(r.origin)}</td><td>{escape(r.destination)}</td>"
            f"<td>{r.price:.0f} ₪</td>"
            f'<td><form method="post" action="/admin/prices/{r.id}/delete">'
            f'<button type="submit">מחק</button></form></td></tr>'
            for r in rows
        )
    return _page(
        "מחירון",
        f"""<table><tr><th>מוצא</th><th>יעד</th><th>מחיר</th><th></th></tr>
        {listed}</table>
        <form method="post" action="/admin/prices">
        <input name="origin" placeholder="מוצא" required>
        <input name="destination" placeholder="יעד" required>
        <input name="price" type="number" step="1" placeholder="מחיר" required>
        <button type="submit">הוסף</button></form>""",
    )


@router.post("/prices")
def prices_add(
    origin: str = Form(...), destination: str = Form(...), price: float = Form(...)
) -> RedirectResponse:
    with db.session_scope() as session:
        session.add(
            db.Price(
                origin=db.normalize_place(origin),
                destination=db.normalize_place(destination),
                price=price,
            )
        )
    return RedirectResponse("/admin/prices", status_code=303)


@router.post("/prices/{price_id}/delete")
def prices_delete(price_id: int) -> RedirectResponse:
    with db.session_scope() as session:
        if (row := session.get(db.Price, price_id)) is not None:
            session.delete(row)
    return RedirectResponse("/admin/prices", status_code=303)


@router.get("/orders", response_class=HTMLResponse)
def orders_page() -> HTMLResponse:
    with db.session_scope() as session:
        rows = session.scalars(
            select(db.Order).order_by(db.Order.created_at.desc()).limit(200)
        ).all()
        listed = "".join(
            f"<tr><td>{r.created_at:%d/%m %H:%M}</td><td>{escape(r.phone)}</td>"
            f"<td>{escape(r.origin)}</td><td>{escape(r.destination)}</td>"
            f"<td>{r.passengers}</td><td>{escape(r.pickup_time or '')}</td>"
            f"<td>{'' if r.price is None else f'{r.price:.0f}'}</td></tr>"
            for r in rows
        )
    return _page(
        "הזמנות",
        f"""<p><a href="/admin/orders.xlsx">הורדה כאקסל</a></p>
        <table><tr><th>מועד</th><th>טלפון</th><th>מוצא</th><th>יעד</th>
        <th>נוסעים</th><th>לאיסוף</th><th>מחיר</th></tr>{listed}</table>""",
    )


@router.get("/orders.xlsx")
def orders_export() -> Response:
    book = Workbook()
    sheet = book.active
    sheet.title = "orders"
    sheet.append(
        ["מועד יצירה", "טלפון", "מוצא", "יעד", "נוסעים", "מועד איסוף", "מחיר", "הערות"]
    )
    with db.session_scope() as session:
        for r in session.scalars(select(db.Order).order_by(db.Order.created_at)).all():
            sheet.append(
                [
                    r.created_at.strftime("%d/%m/%Y %H:%M"),
                    r.phone,
                    r.origin,
                    r.destination,
                    r.passengers,
                    r.pickup_time or "",
                    r.price,
                    r.notes or "",
                ]
            )
    buffer = io.BytesIO()
    book.save(buffer)
    buffer.seek(0)
    name = f"orders-{datetime.utcnow():%Y%m%d-%H%M}.xlsx"
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
