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

from app import drivers

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
        pickup = order.pickup_time.strip()
        if pickup.lower() in {"now", "asap", "immediately"}:
            pickup = "עכשיו"
        parts.append(f"מועד איסוף: {pickup}.")
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


def reputation_text(driver: drivers.db.Driver) -> str:
    """Read the driver the real general score and its components.

    The general score is what decides the tier and what the driver hears when
    they ask for their reputation, so this is the canonical voice summary."""
    score = drivers.general_score(driver)
    _, label = drivers.tier_of(driver)
    parts = [f"המוניטין שלך הוא {label} עם ציון {score:.0f} ממאה."]
    rating = drivers.average_rating(driver)
    if rating is not None and driver.rating_count:
        parts.append(f"דירוג ממוצע {rating:.1f} מתוך {driver.rating_count} דירוגים.")
    else:
        parts.append("עדיין אין דירוגים מנוסעים.")
    parts.append(f"ביצעת {driver.rides_done or 0} נסיעות.")
    if driver.car_year and driver.car_model:
        parts.append(f"רכב {driver.car_model} משנת {driver.car_year} עם {driver.seats or 0} מקומות.")
    elif driver.car_year:
        parts.append(f"שנת רכב {driver.car_year} עם {driver.seats or 0} מקומות.")
    return " ".join(parts)


#: Prompts for the static PBX audio library. The key is the logical name used
#: in the code; the file written to disk is the PBX audio-library name, which
#: defaults to the value in ``app/ivr.DEFAULT_AUDIO``.
AUDIO_TEXTS: dict[str, str] = {
    "driver_menu": (
        "לתפריט הראשי. "
        "להצעת נסיעה נוכחית באזורכם הקישו 1. "
        "לשמיעת ציון המוניטין שלכם הקישו 2. "
        "לניהול ובחירת אזורים מועדפים הקישו 3. "
        "להגדרת שעות שקט הקישו 4. "
        "לעדכון מיקום נוכחי הקישו 5. "
        "לסימון נסיעה כבוצעה הקישו 6."
    ),
    "driver_register": "שלום, אינך רשום במערכת. להרשמת נהג חדש הקישו 1.",
    "driver_ask_car_year": "אנא הקישו את שנת ייצור הרכב בארבע ספרות.",
    "driver_ask_seats": "אנא הקישו את מספר מקומות הישיבה ברכב.",
    "driver_ask_birth_year": "אנא הקישו את שנת הלידה שלכם בארבע ספרות.",
    "driver_ask_quiet_to": "הקש את שעת הסיום של שעות השקט, בשתי ספרות.",
    "driver_invalid_input": "הקשה לא תקינה. ננסה שוב.",
    "driver_saved": "הפרטים נשמרו. תודה.",
    "driver_pending": "פרטיך נקלטו במערכת וממתינים לאישור מנהל. תודה ולהתראות.",
    "driver_offer": "נפתחה קריאה חדשה באזורך. לקבלת פרטי הנסיעה והגשת הצעה הקישו 1.",
    "driver_wait": "ההצעה נשמרה. אנא הַמְתֵּן על הקו לעדכון על תוצאת המכרז.",
    "driver_wait_more": "אנא הַמְתֵּן.",
    "driver_won_menu": (
        "זכית בנסיעה! למעבר לנוסע כעת הקש 1. לשמיעת מספר הטלפון של הנוסע הקש 2."
    ),
    "driver_won_callback": (
        "זכית בנסיעה! נתק כעת. בעוד רגע תקבל שיחה מהמערכת — ענה והקש 1 כדי להתחבר לנוסע."
    ),
    "driver_connect_offer": "זכית בנסיעה. לחיבור לנוסע הקש 1.",
    "driver_taken": "מצטערים, הנסיעה כבר נתפסה על ידי נהג אחר.",
    "driver_no_offer": "כרגע אין הצעה פתוחה בשטח. נסה שוב מאוחר יותר.",
    "driver_connecting": "הנכם מועברים כעת לשיחה עם הנוסע.",
    "driver_areas_menu": (
        "להוספת אזור הקש 1. להסרת אזור הקש 2. "
        "לשמיעת האזורים שלך הקש 3. ליציאה הקש 4."
    ),
    "driver_area_prompt": "אנא בחרו את אזור הפעילות הראשי שלכם מתוך הרשימה.",
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
    "driver_finish_done": "הנסיעה סומנה כבוצעה. קרדיטים, דירוג ועמלה יעודכנו אוטומטית.",
    "passenger_menu": (
        "שלום וברוכים הבאים. "
        "לבדיקת יתרת הקרדיטים שצברתם הקישו 1. "
        "למימוש נסיעה חינם באמצעות קרדיטים הקישו 2. "
        "לתוכנית שתפו וסעו והזמנת חברים הקישו 3. "
        "לעדכון העדפות נסיעה הקישו 4."
    ),
    "passenger_balance": "יתרת הקרדיטים שלך היא {balance}. תודה.",
    "passenger_redeem_ok": "יש לכם מספיק קרדיטים. הנסיעה הזאת תחוייב באפס שקלים.",
    "passenger_redeem_no": "אין מספיק קרדיטים לנסיעה חינם, או שאין הזמנה פתוחה.",
    "passenger_refer_confirmed": "השיוך שלכם אומת בהצלחה.",
    "passenger_refer_prompt": "הקש את מספר הטלפון שברצונך לשייך למבצע שתפו וסעו.",
    "passenger_refer_ok": "המספר נשמר. צינתוק אישור נשלח אליו. החיוג חייב להתבצע תוך 24 שעות.",
    "passenger_refer_no": "לא ניתן לשייך את המספר. ייתכן שהוא כבר משויך או לא תקין.",
    "passenger_prefs": "ההעדפות נשמרו. תודה.",
    "rating_prompt": "נודה לקבלת דירוג עבור הנסיעה האחרונה. אנא הקישו ציון בין 1 ל-5, כאשר 5 הוא הגבוה ביותר.",
    "rating_thanks": "תודה רבה, הדירוג נשמר בהצלחה. נסיעה טובה ולהתראות.",
    "error": "אירעה תקלה. נסה שוב מאוחר יותר.",
}
