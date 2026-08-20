"""Text-to-speech helper for Hebrew prompts.

Uses Microsoft Edge TTS (edge-tts). All generated audio is returned as
in-memory MP3 bytes so the server can either write files to disk or upload
them straight to the PBX audio library.
"""

from __future__ import annotations

import asyncio
import logging
import os

import edge_tts

log = logging.getLogger("tts")

VOICE = os.getenv("TTS_VOICE", "he-IL-AvriNeural")


def synthesize(text: str) -> bytes:
    """Return an MP3 byte string for the given Hebrew text."""
    communicate = edge_tts.Communicate(text, voice=VOICE)
    data = bytearray()

    async def _collect() -> None:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                data.extend(chunk["data"])

    asyncio.run(_collect())
    return bytes(data)


def offer_text(order) -> str:
    """The announcement a driver hears when bidding on a ride."""
    parts = [
        "התקבלה הצעת נסיעה.",
        f"מוצא: {order.origin or 'לא ידוע'}.",
        f"יעד: {order.destination or 'לא ידוע'}.",
    ]
    if order.vehicle_type:
        parts.append(f"סוג רכב: {order.vehicle_type}.")
    if order.price:
        parts.append(f"מחיר: {order.price:.0f} שקלים.")
    if order.pickup_time:
        parts.append(f"מועד איסוף: {order.pickup_time}.")
    if order.luggage:
        parts.append(f"מטען: {order.luggage}.")
    if order.special_requests:
        parts.append(f"בקשות: {order.special_requests}.")
    parts.append("אם אתה מעוניין בנסיעה, הקש 1.")
    return " ".join(parts)


def area_menu_text(prompt: str, names: list[str]) -> str:
    """Read the areas out with their menu digits. The numbering comes from the
    areas table, so the caller has to hear it — a recorded list would go stale
    the first time the office adds an area."""
    if not names:
        return AUDIO_TEXTS["driver_no_areas"]
    options = " ".join(f"לאזור {name} הקש {index}." for index, name in enumerate(names, 1))
    return f"{prompt} {options}"


def registration_text(*, car_year: int | None, seats: int | None, area: str | None) -> str:
    """Read the collected details back before the pending-approval notice, so a
    wrong digit is caught on the call rather than by the office."""
    parts = ["הפרטים שנרשמו:"]
    if car_year:
        parts.append(f"שנת רכב {car_year}.")
    if seats:
        parts.append(f"{seats} מקומות.")
    if area:
        parts.append(f"אזור {area}.")
    parts.append(AUDIO_TEXTS["driver_pending"])
    return " ".join(parts)


#: Prompts for the static PBX audio library. The key is the logical name used
#: in the code; the file written to disk is the PBX audio-library name, which
#: defaults to the value in ``app/ivr.DEFAULT_AUDIO``.
AUDIO_TEXTS: dict[str, str] = {
    "driver_menu": (
        "שלום למערכת דרייברים. "
        "להצעת נסיעה חדשה הקש 1. "
        "למוניטין שלך הקש 2. "
        "לעדכון אזורים הקש 3. "
        "לשעות שקט הקש 4. "
        "לעדכון מיקום הקש 5. "
        "לסיום נסיעה הקש 6."
    ),
    "driver_register": "כדי להירשם כנהג, הקש 1 ולאחר מכן תועבר להשלמת פרטים.",
    "driver_ask_car_year": "הקש את שנת הרכב בארבע ספרות. לדוגמה, אלפיים עשרים ואחת.",
    "driver_ask_seats": "הקש את מספר המקומות ברכב, ספרה אחת, לא כולל הנהג.",
    "driver_ask_birth_year": "הקש את שנת הלידה שלך בארבע ספרות.",
    "driver_ask_quiet_to": "הקש את שעת הסיום של שעות השקט, בשתי ספרות.",
    "driver_invalid_input": "הקשה לא תקינה. ננסה שוב.",
    "driver_saved": "הפרטים נשמרו. תודה.",
    "driver_pending": "הרישום שלך ממתין לאישור המשרד. תוכל לחזור מאוחר יותר.",
    "driver_offer": "התקבלה הצעת נסיעה באזור שלך. אם אתה מעוניין, הקש 1.",
    "driver_wait": "ההצעה נשמרה. המערכת בודקת את כל ההצעות. אם תזכה, תחובר מיד לנוסע.",
    "driver_taken": "מצטערים, הנסיעה כבר נתפסה על ידי נהג אחר.",
    "driver_no_offer": "כרגע אין הצעה פתוחה בשטח. נסה שוב מאוחר יותר.",
    "driver_connecting": "מזל טוב, זכית בנסיעה. מחבר אותך עכשיו לנוסע.",
    "driver_reputation": (
        "המוניטין שלך נבנה מדירוגי נוסעים, ותק, גיל, רכב וכמות נסיעות. "
        "לפרטים פרטיים תקבל הודעת טקסט."
    ),
    "driver_areas_menu": (
        "להוספת אזור הקש 1. להסרת אזור הקש 2. "
        "לשמיעת האזורים שלך הקש 3. ליציאה הקש 4."
    ),
    "driver_area_prompt": "הקש את מספר האזור שבו תרצה לקבל הצעות.",
    "driver_area_add_prompt": "הקש את מספר האזור שברצונך להוסיף.",
    "driver_area_remove_prompt": "הקש את מספר האזור שברצונך להסיר.",
    "driver_area_added": "האזור נוסף בהצלחה.",
    "driver_area_removed": "האזור הוסר בהצלחה.",
    "driver_area_already": "האזור כבר ברשימה.",
    "driver_area_not_found": "האזור אינו ברשימה.",
    "driver_quiet_prompt": "הגדר שעות שקט. הקש את שעת ההתחלה בשתי ספרות.",
    "driver_location_prompt": "הקש את מספר האזור שבו אתה נמצא כרגע.",
    "driver_no_areas": "אין אזורים מוגדרים במערכת. פנה למשרד.",
    "driver_location_done": "המיקום נשמר. תודה.",
    "driver_finish_done": "הנסיעה סומנה כבוצעה. ניקוד, דירוג ועמלה יעודכנו אוטומטית.",
    "passenger_menu": (
        "שלום למוקד דרייברים. "
        "לבדיקת יתרת נקודות הקש 1. "
        "למימוש נסיעה חינם הקש 2. "
        "למבצע שתפו וסעו הקש 3. "
        "לעדכון העדפות הקש 4."
    ),
    "passenger_balance": "יתרת הנקודות שלך נבדקת ותשלח אליך בהודעת טקסט. תודה.",
    "passenger_redeem_ok": "יש לך מספיק נקודות. הנסיעה הזאת תחוייב באפס שקלים.",
    "passenger_redeem_no": "אין מספיק נקודות לנסיעה חינם, או שאין הזמנה פתוחה.",
    "passenger_refer_prompt": "הקש את מספר הטלפון שברצונך לשייך למבצע שתפו וסעו.",
    "passenger_refer_ok": "המספר נשמר. צינתוק אישור נשלח אליו. החיוג חייב להתבצע תוך 24 שעות.",
    "passenger_refer_no": "לא ניתן לשייך את המספר. ייתכן שהוא כבר משויך או לא תקין.",
    "passenger_prefs": "ההעדפות נשמרו. תודה.",
    "rating_prompt": "שלום, אנו מעריכים את דעתך. אנא דרג את הנהג בין 1 ל-5, כאשר 5 הוא מעולה.",
    "rating_thanks": "תודה על הדירוג. יום נעים.",
    "error": "אירעה תקלה. נסה שוב מאוחר יותר.",
}
