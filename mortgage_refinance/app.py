from datetime import UTC, datetime
from html import escape
import os
from pathlib import Path
import sqlite3

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response


BASELINE_AMOUNT = 400_000
BASELINE_YEARS = 20
BASELINE_SAVINGS = 150_000
SAVINGS_RATE = BASELINE_SAVINGS / BASELINE_AMOUNT / BASELINE_YEARS

app = FastAPI(
    title="Mortgage Refinance Opportunity Calculator",
    description="Standalone IVR calculator and lead capture service.",
    version="1.0.0",
)


def database_path() -> Path:
    path = Path(os.getenv("LEADS_DB_PATH", "data/leads.db"))
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def initialize_database() -> None:
    with sqlite3.connect(database_path()) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone_number TEXT,
                mortgage_amount INTEGER NOT NULL,
                remaining_years INTEGER NOT NULL,
                estimated_savings INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )


def estimate_savings(mortgage_amount: int, remaining_years: int) -> int:
    return round(mortgage_amount * remaining_years * SAVINGS_RATE)


def twiml(message: str, gather: str = "") -> Response:
    body = f'<?xml version="1.0" encoding="UTF-8"?><Response>{message}{gather}</Response>'
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
        <p>לבדיקת לידים: <a href="/leads">/leads</a></p>
      </body>
    </html>
    """


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/voice/mortgage")
async def mortgage_start(request: Request) -> Response:
    return twiml(
        "",
        gather(
            "/voice/mortgage/amount",
            "מהו סכום המשכנתא שלקחת? הקש את הסכום המלא במספרים, ולסיום הקש סולמית.",
        ),
    )


@app.post("/voice/mortgage/amount")
async def mortgage_amount(request: Request) -> Response:
    form = await request.form()
    amount = form.get("Digits", "")
    if not str(amount).isdigit() or int(amount) <= 0:
        return twiml(
            gather(
                "/voice/mortgage/amount",
                "הסכום לא נקלט. הקש את סכום המשכנתא במספרים, ולסיום הקש סולמית.",
            )
        )
    return twiml(
        "",
        gather(
            f"/voice/mortgage/years?amount={int(amount)}",
            "כמה שנים נותרו לך עד לסיום תשלום המשכנתא? הקש את מספר השנים.",
        ),
    )


@app.post("/voice/mortgage/years")
async def mortgage_years(request: Request, amount: int) -> Response:
    form = await request.form()
    years = form.get("Digits", "")
    if not str(years).isdigit() or not 1 <= int(years) <= 50:
        return twiml(
            gather(
                f"/voice/mortgage/years?amount={amount}",
                "מספר השנים לא נקלט. הקש מספר בין 1 ל-50.",
            )
        )
    savings = estimate_savings(amount, int(years))
    prompt = (
        f"וואו! ייתכן מאוד שאתה יכול לחסוך כ-{savings:,} שקלים "
        "ממחזור המשכנתא. לפרטים נוספים הקש 1, לסיום הקש 2."
    )
    return twiml(
        "",
        gather(
            f"/voice/mortgage/lead?amount={amount}&years={int(years)}"
            f"&savings={savings}",
            prompt,
            num_digits="1",
        ),
    )


@app.post("/voice/mortgage/lead")
async def mortgage_lead(
    request: Request, amount: int, years: int, savings: int
) -> Response:
    form = await request.form()
    if form.get("Digits") == "1":
        with sqlite3.connect(database_path()) as connection:
            connection.execute(
                """
                INSERT INTO leads
                    (phone_number, mortgage_amount, remaining_years,
                     estimated_savings, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    form.get("From", ""),
                    amount,
                    years,
                    savings,
                    datetime.now(UTC).isoformat(),
                ),
            )
        return twiml("", say("תודה, הפרטים נקלטו. נציג יחזור אליך בהקדם."))
    return twiml("", say("תודה שהתקשרת."))


@app.get("/leads")
def leads() -> JSONResponse:
    with sqlite3.connect(database_path()) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT id, phone_number, mortgage_amount, remaining_years,
                   estimated_savings, created_at
            FROM leads ORDER BY id DESC
            """
        ).fetchall()
    return JSONResponse([dict(row) for row in rows])
