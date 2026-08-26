"""Mortgage refinance IVR flow and lead capture."""

# ruff: noqa: E501, I001

import os
import csv
import io
import sqlite3
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
import psycopg
from psycopg.rows import dict_row


BASELINE_AMOUNT = 400_000
BASELINE_YEARS = 20
BASELINE_SAVINGS = 100_000
SAVINGS_RATE = BASELINE_SAVINGS / BASELINE_AMOUNT / BASELINE_YEARS
ROUTE_PREFIX = "/mortgage"

app = FastAPI(
    title="Mortgage Refinance Opportunity Calculator",
    description="Standalone Hebrew IVR calculator and lead capture service.",
    version="1.0.0",
)


def database_path() -> Path:
    path = Path(os.getenv("LEADS_DB_PATH", "data/leads.db"))
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def database_url() -> str:
    return os.getenv("BOT_DB_URL") or os.getenv("DATABASE_URL") or ""


def initialize_database() -> None:
    if database_url():
        with psycopg.connect(database_url()) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS mortgage_leads (
                    id BIGSERIAL PRIMARY KEY,
                    phone_number TEXT NOT NULL DEFAULT '',
                    mortgage_amount BIGINT NOT NULL CHECK (mortgage_amount > 0),
                    years_since_origination INTEGER NOT NULL,
                    original_term_years INTEGER NOT NULL,
                    remaining_years INTEGER NOT NULL,
                    estimated_savings BIGINT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
        return
    with sqlite3.connect(database_path()) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone_number TEXT,
                mortgage_amount INTEGER NOT NULL,
                years_since_origination INTEGER NOT NULL,
                original_term_years INTEGER NOT NULL,
                remaining_years INTEGER NOT NULL,
                estimated_savings INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS ivr_sessions (
                call_id TEXT PRIMARY KEY,
                caller TEXT NOT NULL,
                step TEXT NOT NULL,
                amount INTEGER,
                elapsed INTEGER,
                term INTEGER
            )
            """
        )


def estimate_savings(mortgage_amount: int, remaining_years: int) -> int:
    return round(mortgage_amount * remaining_years * SAVINGS_RATE)


def insert_lead(
    caller: str, amount: int, elapsed: int, term: int, remaining: int, savings: int
) -> None:
    values = (caller, amount, elapsed, term, remaining, savings)
    if database_url():
        with psycopg.connect(database_url()) as connection:
            connection.execute(
                """
                INSERT INTO mortgage_leads (
                    phone_number, mortgage_amount, years_since_origination,
                    original_term_years, remaining_years, estimated_savings
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """,
                values,
            )
        return
    with sqlite3.connect(database_path()) as connection:
        connection.execute(
            """
            INSERT INTO leads (
                phone_number, mortgage_amount, years_since_origination,
                original_term_years, remaining_years, estimated_savings, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (*values, datetime.now(UTC).isoformat()),
        )


def twiml(message: str, gather_prompt: str = "") -> Response:
    body = (
        '<?xml version="1.0" encoding="UTF-8"?><Response>'
        f"{message}{gather_prompt}</Response>"
    )
    return Response(content=body, media_type="application/xml")


def say(text: str) -> str:
    return f"<Say language=\"he-IL\">{escape(text)}</Say>"


def gather(action: str, prompt: str, num_digits: str = "") -> str:
    digits = f' numDigits="{num_digits}"' if num_digits else ""
    return (
        f'<Gather input="dtmf" action="{action}" method="POST"{digits}'
        ' timeout="8" finishOnKey="#">'
        f"{say(prompt)}</Gather>"
    )


def action_path(path: str) -> str:
    return f"{ROUTE_PREFIX}{path}"


def module_message(text: str) -> dict[str, Any]:
    return {"type": "simpleMessage", "files": [{"text": text}]}


def module_digits(
    prompt: str, *, name: str, min_digits: int = 1, max_digits: int = 10
) -> dict[str, Any]:
    return {
        "type": "getDTMF",
        "name": name,
        "min": min_digits,
        "max": max_digits,
        "timeout": 8,
        "files": [{"text": prompt}],
    }


def module_menu(files: list[dict[str, str]], *, name: str = "confirm") -> dict[str, Any]:
    return {
        "type": "simpleMenu",
        "name": name,
        "enabledKeys": "1,2",
        "times": 2,
        "timeout": 8,
        "files": files,
    }


def module_params(request: Request) -> tuple[str, str, str]:
    values = {key.lower(): value for key, value in request.query_params.items()}
    dtmf = next(
        (
            values[key]
            for key in ("dtmf", "digits", "input", "value", "")
            if values.get(key)
        ),
        "",
    )
    call_id = next(
        (
            values[key]
            for key in ("pbxcallid", "callid", "call_id", "uniqueid", "id")
            if values.get(key)
        ),
        "",
    )
    caller = next(
        (
            values[key]
            for key in ("pbxphone", "caller", "phone", "callerid", "from")
            if values.get(key)
        ),
        "",
    )
    return call_id or caller, caller, dtmf


def module_session(call_id: str, caller: str) -> tuple[str, int | None, int | None, int | None]:
    with sqlite3.connect(database_path()) as connection:
        row = connection.execute(
            "SELECT step, amount, elapsed, term FROM ivr_sessions WHERE call_id = ?",
            (call_id,),
        ).fetchone()
    return row if row else ("start", None, None, None)


def save_module_session(
    call_id: str,
    caller: str,
    step: str,
    amount: int | None = None,
    elapsed: int | None = None,
    term: int | None = None,
) -> None:
    with sqlite3.connect(database_path()) as connection:
        connection.execute(
            """
            INSERT INTO ivr_sessions (call_id, caller, step, amount, elapsed, term)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(call_id) DO UPDATE SET
                caller=excluded.caller, step=excluded.step,
                amount=excluded.amount, elapsed=excluded.elapsed, term=excluded.term
            """,
            (call_id, caller, step, amount, elapsed, term),
        )


AMOUNT_PROMPT = (
    "\u05de\u05d4\u05d5 \u05e1\u05db\u05d5\u05dd \u05d4\u05de\u05e9\u05db\u05e0\u05ea\u05d0 "
    "\u05e9\u05dc\u05e7\u05d7\u05ea? \u05d4\u05e7\u05e9 \u05d0\u05ea \u05d4\u05e1\u05db\u05d5\u05dd "
    "\u05d4\u05de\u05dc\u05d0 \u05d1\u05de\u05e1\u05e4\u05e8\u05d9\u05dd, "
    "\u05d5\u05dc\u05e1\u05d9\u05d5\u05dd \u05d4\u05e7\u05e9 \u05e1\u05d5\u05dc\u05de\u05d9\u05ea."
)
INVALID_AMOUNT_PROMPT = (
    "\u05d4\u05e1\u05db\u05d5\u05dd \u05dc\u05d0 \u05e0\u05e7\u05dc\u05d8. "
    "\u05d4\u05e7\u05e9 \u05d0\u05ea \u05e1\u05db\u05d5\u05dd "
    "\u05d4\u05de\u05e9\u05db\u05e0\u05ea\u05d0 \u05d1\u05de\u05e1\u05e4\u05e8\u05d9\u05dd."
)
ELAPSED_PROMPT = (
    "\u05e0\u05d0 \u05d4\u05e7\u05e9 \u05dc\u05e4\u05e0\u05d9 \u05db\u05de\u05d4 \u05e9\u05e0\u05d9\u05dd "
    "\u05d4\u05d5\u05e6\u05d0\u05ea \u05d0\u05ea \u05d4\u05de\u05e9\u05db\u05e0\u05ea\u05d0."
)
INVALID_ELAPSED_PROMPT = (
    "\u05de\u05e1\u05e4\u05e8 \u05d4\u05e9\u05e0\u05d9\u05dd \u05dc\u05d0 \u05e0\u05e7\u05dc\u05d8. "
    "\u05e0\u05d0 \u05d4\u05e7\u05e9 \u05dc\u05e4\u05e0\u05d9 \u05db\u05de\u05d4 \u05e9\u05e0\u05d9\u05dd "
    "\u05d4\u05d5\u05e6\u05d0\u05ea \u05d0\u05ea \u05d4\u05de\u05e9\u05db\u05e0\u05ea\u05d0."
)
TERM_PROMPT = (
    "\u05e0\u05d0 \u05d4\u05e7\u05e9 \u05dc\u05db\u05de\u05d4 \u05e9\u05e0\u05d9\u05dd "
    "\u05d4\u05d9\u05ea\u05d4 \u05d4\u05de\u05e9\u05db\u05e0\u05ea\u05d0 "
    "\u05e9\u05dc\u05e7\u05d7\u05ea."
)
INVALID_TERM_PROMPT = (
    "\u05d4\u05e0\u05ea\u05d5\u05e0\u05d9\u05dd \u05dc\u05d0 \u05e0\u05e7\u05dc\u05d8\u05d5. "
    "\u05ea\u05e7\u05d5\u05e4\u05ea \u05d4\u05de\u05e9\u05db\u05e0\u05ea\u05d0 \u05d7\u05d9\u05d9\u05d1\u05ea \u05dc\u05d4\u05d9\u05d5\u05ea "
    "\u05d2\u05d3\u05d5\u05dc\u05d4 \u05de\u05de\u05e1\u05e4\u05e8 \u05d4\u05e9\u05e0\u05d9\u05dd "
    "\u05e9\u05e2\u05d1\u05e8\u05d5. \u05e0\u05d0 \u05d4\u05e7\u05e9 \u05dc\u05db\u05de\u05d4 "
    "\u05e9\u05e0\u05d9\u05dd \u05d4\u05d9\u05ea\u05d4 \u05d4\u05de\u05e9\u05db\u05e0\u05ea\u05d0."
)
SAVINGS_PROMPT = (
    "\u05d5\u05d5\u05d0\u05d5! \u05d9\u05d9\u05ea\u05db\u05df \u05de\u05d0\u05d5\u05d3 \u05e9\u05d0\u05ea\u05d4 "
    "\u05d9\u05db\u05d5\u05dc \u05dc\u05d7\u05e1\u05d5\u05da \u05db-{savings:,} \u05e9\u05e7\u05dc\u05d9\u05dd "
    "\u05de\u05de\u05d7\u05d6\u05d5\u05e8 \u05d4\u05de\u05e9\u05db\u05e0\u05ea\u05d0. "
    "\u05dc\u05e4\u05e8\u05d8\u05d9\u05dd \u05e0\u05d5\u05e1\u05e4\u05d9\u05dd "
    "\u05d4\u05e7\u05e9 1, "
    "\u05dc\u05e1\u05d9\u05d5\u05dd \u05d4\u05e7\u05e9 2."
)
SAVINGS_INTRO = (
    "\u05d5\u05d5\u05d0\u05d5! \u05d9\u05d9\u05ea\u05db\u05df \u05de\u05d0\u05d5\u05d3 "
    "\u05e9\u05d0\u05ea\u05d4 \u05d9\u05db\u05d5\u05dc \u05dc\u05d7\u05e1\u05d5\u05da \u05db-"
)
SAVINGS_OUTRO = (
    " \u05e9\u05e7\u05dc\u05d9\u05dd \u05de\u05de\u05d7\u05d6\u05d5\u05e8 "
    "\u05d4\u05de\u05e9\u05db\u05e0\u05ea\u05d0. \u05dc\u05e4\u05e8\u05d8\u05d9\u05dd \u05e0\u05d5\u05e1\u05e4\u05d9\u05dd "
    "\u05d4\u05e7\u05e9 1, \u05dc\u05e1\u05d9\u05d5\u05dd \u05d4\u05e7\u05e9 2."
)


@app.on_event("startup")
def startup() -> None:
    initialize_database()


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    return """
    <!doctype html>
    <html lang="he" dir="rtl">
      <head><meta charset="utf-8"><title>בדיקת כדאיות מחזור משכנתא</title></head>
      <body style="font-family:Arial;max-width:700px;margin:40px auto;line-height:1.6">
        <h1>בדיקת כדאיות מחזור משכנתא</h1>
        <p>שירות IVR עצמאי לחישוב חיסכון משוער ולשמירת לידים.</p>
        <p>הגדר את כתובת ה-POST של ספק הטלפוניה לכתובת
        <strong>/voice/mortgage</strong>.</p>
        <p>לצפייה בכל הנתונים המדויקים שהלקוחות הזינו:
        <a href="/leads">/leads</a></p>
      </body>
    </html>
    """


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.api_route("/voice/mortgage", methods=["GET", "POST"])
async def mortgage_start(request: Request) -> JSONResponse:
    _, caller, _ = module_params(request)
    values = {key.lower(): value for key, value in request.query_params.items()}
    if values.get("confirm") == "1":
        amount = int(values["amount"])
        elapsed = int(values["elapsed"])
        term = int(values["term"])
        remaining = term - elapsed
        savings = estimate_savings(amount, remaining)
        insert_lead(caller, amount, elapsed, term, remaining, savings)
        return JSONResponse(
            module_message(
                "\u05ea\u05d5\u05d3\u05d4, \u05d4\u05e4\u05e8\u05d8\u05d9\u05dd "
                "\u05e0\u05e7\u05dc\u05d8\u05d5. \u05e0\u05e6\u05d9\u05d2 \u05d9\u05d7\u05d6\u05d5\u05e8 "
                "\u05d0\u05dc\u05d9\u05da \u05d1\u05d4\u05e7\u05d3\u05dd."
            )
        )
    if {"amount", "elapsed", "term"} <= values.keys():
        amount = int(values["amount"])
        elapsed = int(values["elapsed"])
        term = int(values["term"])
        if amount <= 0 or not 0 <= elapsed <= 50 or not 1 <= term <= 50:
            return JSONResponse(module_message(INVALID_TERM_PROMPT))
        if term <= elapsed:
            return JSONResponse(module_message(INVALID_TERM_PROMPT))
        remaining = term - elapsed
        savings = estimate_savings(amount, remaining)
        return JSONResponse(
            module_menu(
                [
                    {"text": SAVINGS_INTRO},
                    {"number": str(savings)},
                    {"text": SAVINGS_OUTRO},
                ]
            )
        )
    return JSONResponse(
        [
            module_digits(AMOUNT_PROMPT, name="amount", max_digits=10),
            module_digits(ELAPSED_PROMPT, name="elapsed", max_digits=2),
            module_digits(TERM_PROMPT, name="term", max_digits=2),
        ]
    )


@app.post("/voice/mortgage/amount")
async def mortgage_amount(request: Request) -> Response:
    form = await request.form()
    amount = form.get("Digits", "")
    if not str(amount).isdigit() or int(amount) <= 0:
        return twiml(
            "",
            gather(
                action_path("/voice/mortgage/amount"),
                INVALID_AMOUNT_PROMPT,
            ),
        )
    return twiml(
        "",
        gather(
            action_path(f"/voice/mortgage/elapsed?amount={int(amount)}"),
            ELAPSED_PROMPT,
        ),
    )


@app.post("/voice/mortgage/elapsed")
async def mortgage_elapsed(request: Request, amount: int) -> Response:
    form = await request.form()
    elapsed = form.get("Digits", "")
    if not str(elapsed).isdigit() or not 0 <= int(elapsed) <= 50:
        return twiml(
            "",
            gather(
                action_path(f"/voice/mortgage/elapsed?amount={amount}"),
                INVALID_ELAPSED_PROMPT,
            ),
        )
    return twiml(
        "",
        gather(
            action_path(
                f"/voice/mortgage/term?amount={amount}&elapsed={int(elapsed)}"
            ),
            TERM_PROMPT,
        ),
    )


@app.post("/voice/mortgage/term")
async def mortgage_term(request: Request, amount: int, elapsed: int) -> Response:
    form = await request.form()
    term = form.get("Digits", "")
    if (
        not str(term).isdigit()
        or not 1 <= int(term) <= 50
        or int(elapsed) >= int(term)
    ):
        return twiml(
            "",
            gather(
                action_path(f"/voice/mortgage/term?amount={amount}&elapsed={elapsed}"),
                INVALID_TERM_PROMPT,
            ),
        )
    remaining = int(term) - elapsed
    savings = estimate_savings(amount, remaining)
    prompt = (
        SAVINGS_PROMPT.format(savings=savings)
    )
    return twiml(
        "",
        gather(
            action_path(
                f"/voice/mortgage/lead?amount={amount}&elapsed={elapsed}"
                f"&term={int(term)}&remaining={remaining}&savings={savings}"
            ),
            prompt,
            num_digits="1",
        ),
    )


@app.post("/voice/mortgage/lead")
async def mortgage_lead(
    request: Request,
    amount: int,
    elapsed: int,
    term: int,
    remaining: int,
    savings: int,
) -> Response:
    form = await request.form()
    if form.get("Digits") == "1":
        with sqlite3.connect(database_path()) as connection:
            connection.execute(
                """
                INSERT INTO leads (
                    phone_number, mortgage_amount, years_since_origination,
                    original_term_years, remaining_years, estimated_savings,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    form.get("From", ""),
                    amount,
                    elapsed,
                    term,
                    remaining,
                    savings,
                    datetime.now(UTC).isoformat(),
                ),
            )
        return twiml(
            "",
            say(
                "\u05ea\u05d5\u05d3\u05d4, \u05d4\u05e4\u05e8\u05d8\u05d9\u05dd \u05e0\u05e7\u05dc\u05d8\u05d5. "
                "\u05e0\u05e6\u05d9\u05d2 \u05d9\u05d7\u05d6\u05d5\u05e8 \u05d0\u05dc\u05d9\u05da \u05d1\u05d4\u05e7\u05d3\u05dd."
            )
    )
    return twiml("", say("\u05ea\u05d5\u05d3\u05d4 \u05e9\u05d4\u05ea\u05e7\u05e9\u05e8\u05ea."))


def lead_rows() -> list[dict[str, Any]]:
    if database_url():
        with psycopg.connect(database_url(), row_factory=dict_row) as connection:
            rows = connection.execute(
                """
                SELECT id, phone_number, mortgage_amount,
                       years_since_origination, original_term_years,
                       remaining_years, estimated_savings, created_at
                FROM mortgage_leads ORDER BY id DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]
    with sqlite3.connect(database_path()) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT id, phone_number, mortgage_amount,
                   years_since_origination, original_term_years,
                   remaining_years, estimated_savings, created_at
            FROM leads ORDER BY id DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


@app.get("/leads", response_class=HTMLResponse)
def leads() -> str:
    rows = lead_rows()
    headers = [
        ("id", "מס׳"),
        ("phone_number", "טלפון"),
        ("mortgage_amount", "סכום משכנתא"),
        ("years_since_origination", "שנים שעברו"),
        ("original_term_years", "תקופה מקורית"),
        ("remaining_years", "שנים שנותרו"),
        ("estimated_savings", "חיסכון משוער"),
        ("created_at", "מועד"),
    ]
    table_rows = "".join(
        "<tr>"
        + "".join(f"<td>{escape(str(row[key]))}</td>" for key, _ in headers)
        + "</tr>"
        for row in rows
    )
    return f"""<!doctype html>
<html lang="he" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>לידים - מחזור משכנתא</title>
<style>
body {{ margin: 0; background: #f5f7fb; color: #172033; font-family: Arial, sans-serif; }}
main {{ max-width: 1180px; margin: 36px auto; padding: 0 18px; }}
.top {{ align-items: center; display: flex; gap: 12px; justify-content: space-between; margin-bottom: 18px; }}
h1 {{ font-size: 26px; margin: 0; }}
.actions {{ display: flex; flex-wrap: wrap; gap: 8px; }}
a {{ background: #1769aa; border-radius: 8px; color: white; padding: 10px 14px; text-decoration: none; }}
a.secondary {{ background: #e7edf5; color: #1769aa; }}
.card {{ background: white; border: 1px solid #e1e7ef; border-radius: 12px; box-shadow: 0 5px 18px #1d355710; overflow-x: auto; }}
table {{ border-collapse: collapse; min-width: 900px; width: 100%; }}
th, td {{ border-bottom: 1px solid #edf0f4; padding: 12px 14px; text-align: right; white-space: nowrap; }}
th {{ background: #f0f5fa; color: #42526b; font-size: 13px; }}
tr:last-child td {{ border-bottom: 0; }}
.empty {{ color: #68758a; padding: 28px; text-align: center; }}
</style>
</head>
<body><main>
<div class="top"><h1>לידים — מחזור משכנתא</h1>
<div class="actions"><a href="/mortgage/leads.csv">הורדת CSV</a>
<a href="/mortgage/leads.xls">פתיחה ב־Excel</a>
<a class="secondary" href="/mortgage/leads">רענון</a></div></div>
<div class="card">{"<table><thead><tr>" + "".join(f"<th>{label}</th>" for _, label in headers) + "</tr></thead><tbody>" + table_rows + "</tbody></table>" if rows else '<div class="empty">עדיין לא נקלטו לידים</div>'}</div>
</main></body></html>"""


@app.get("/leads.csv")
def leads_csv() -> Response:
    rows = lead_rows()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["מס׳", "טלפון", "סכום משכנתא", "שנים שעברו", "תקופה מקורית",
                     "שנים שנותרו", "חיסכון משוער", "מועד"])
    for row in rows:
        writer.writerow(
            [
                row["id"],
                row["phone_number"],
                row["mortgage_amount"],
                row["years_since_origination"],
                row["original_term_years"],
                row["remaining_years"],
                row["estimated_savings"],
                row["created_at"],
            ]
        )
    return Response(
        content="\ufeff" + output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="mortgage-leads.csv"'},
    )


@app.get("/leads.xls")
def leads_xls() -> Response:
    rows = lead_rows()
    labels = ["מס׳", "טלפון", "סכום משכנתא", "שנים שעברו", "תקופה מקורית",
              "שנים שנותרו", "חיסכון משוער", "מועד"]
    body = "".join(
        "<tr>" + "".join(f"<td>{escape(str(value))}</td>" for value in [
            row["id"], row["phone_number"], row["mortgage_amount"],
            row["years_since_origination"], row["original_term_years"],
            row["remaining_years"], row["estimated_savings"], row["created_at"],
        ]) + "</tr>"
        for row in rows
    )
    content = (
        '<html xmlns:o="urn:schemas-microsoft-com:office:office" '
        'xmlns:x="urn:schemas-microsoft-com:office:excel"><meta charset="utf-8">'
        "<table><tr>" + "".join(f"<th>{label}</th>" for label in labels)
        + "</tr>" + body + "</table></html>"
    )
    return Response(
        content="\ufeff" + content,
        media_type="application/vnd.ms-excel",
        headers={"Content-Disposition": 'attachment; filename="mortgage-leads.xls"'},
    )


@app.get("/leads.json")
def leads_json() -> JSONResponse:
    return JSONResponse(lead_rows())
