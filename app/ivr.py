"""Module API handler — the PBX asking our server what to do next in a call.

The PBX drives one module at a time: it GETs this handler, we answer with a
single JSON object describing one action (play, menu, capture digits, route,
hang up), it runs that action and comes back with the result. There is no
session on the PBX side, so the few digits already collected live in
``ivr_sessions``, keyed by the call id.

Two rules from the PBX documentation shape everything here:

* an unrecognised ``type`` or non-JSON body silently sends the caller back to
  the previous menu, which is invisible from our side, so every path — including
  every error path — must end in a valid module. That is why the handler catches
  its own exceptions and answers with an apology message instead of a 500;
* the request is not authenticated: the URL is the secret. So the caller's
  identity is only ever the phone number the PBX reports, and nothing
  destructive happens without it matching a record we already hold.

Audio file names are settings (``audio_*``), because the recordings live in the
PBX account's library and the office re-records them without a deploy.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import db, dispatch, drivers, loyalty, pbx, ratings, referrals, tts

log = logging.getLogger("ivr")

router = APIRouter(prefix="/ivr", tags=["ivr"])

#: No phone menu step takes longer than this, so anything older belongs to a
#: call that has already ended.
STALE_CALL = timedelta(minutes=15)

#: Logical name -> default file in the PBX audio library. The office uploads
#: recordings under these names, or renames them through the settings table.
DEFAULT_AUDIO = {
    "driver_menu": "drivers_menu",
    "driver_offer": "drivers_offer",
    "driver_wait": "drivers_wait",
    "driver_taken": "drivers_taken",
    "driver_no_offer": "drivers_no_offer",
    "driver_connecting": "drivers_connecting",
    "driver_register": "drivers_register",
    "driver_pending": "drivers_pending",
    "driver_saved": "drivers_saved",
    "driver_area_prompt": "drivers_area_prompt",
    "driver_quiet_prompt": "drivers_quiet_prompt",
    "driver_location_prompt": "drivers_location_prompt",
    "driver_location_done": "drivers_location_done",
    "driver_finish_done": "drivers_finish_done",
    "passenger_menu": "club_menu",
    "passenger_balance": "club_balance",
    "passenger_redeem_ok": "club_redeem_ok",
    "passenger_redeem_no": "club_redeem_no",
    "passenger_refer_prompt": "club_refer_prompt",
    "passenger_refer_ok": "club_refer_ok",
    "passenger_refer_no": "club_refer_no",
    "passenger_prefs": "club_prefs",
    "rating_prompt": "rating_prompt",
    "rating_thanks": "rating_thanks",
    "error": "system_error",
}


def audio(key: str) -> str:
    return db.get_setting(f"audio_{key}") or DEFAULT_AUDIO.get(key, key)


# --------------------------------------------------------------- module JSON


def _split_text(text: str) -> list[dict]:
    """Split a long prompt into short `files` items.

    The Technoline docs recommend short, reusable `text` chunks because each
    string is TTS-synthesised once and cached.  One long unique block is slow
    and can be rejected if it hits the TTS service length limit.
    """
    if not text:
        return []
    parts = [p.strip() for p in re.split(r"(?<=\.)\s+", text) if p.strip()]
    if len(parts) == 1:
        return [{"text": parts[0]}]
    return [{"text": p if p.endswith(".") else f"{p}."} for p in parts]


def message(key: str, **extra: Any) -> dict:
    text = tts.AUDIO_TEXTS.get(key)
    if text:
        return {"type": "simpleMessage", "files": [{"text": text}], **extra}
    return {"type": "simpleMessage", "fileName": audio(key), **extra}


def menu(
    key: str,
    *,
    keys: str | None = None,
    tries: int = 3,
    timeout: int = 5,
    file_name: str | None = None,
    text: str | None = None,
) -> dict:
    """Return a simpleMenu module per the Technoline Module API spec.

    Required fields: ``name`` (the query-key the PBX uses for the pressed
    key), ``enabledKeys`` (comma-separated allowed keys), ``times`` (how many
    times to repeat the menu), ``timeout`` (seconds to wait), and ``files``
    (the audio to play).  See ``https://app.tlivr.com/apiModuleDocs.html``.
    """
    if file_name:
        files = [{"fileName": file_name}]
    elif text:
        files = _split_text(text)
    elif key in tts.AUDIO_TEXTS:
        files = _split_text(tts.AUDIO_TEXTS[key])
    else:
        files = [{"fileName": audio(key)}]
    return {
        "type": "simpleMenu",
        "name": "dtmf",
        "enabledKeys": keys or "",
        "times": tries,
        "timeout": timeout,
        "setMusic": "no",
        "files": files,
    }


def get_digits(*, min_digits: int = 1, max_digits: int = 10, timeout: int = 8) -> dict:
    """Return a getDTMF module per the Technoline Module API spec."""
    return {
        "type": "getDTMF",
        "name": "dtmf",
        "max": max_digits,
        "min": min_digits,
        "timeout": timeout,
    }


def record(
    name: str,
    *,
    max_seconds: int = 30,
    min_seconds: int = 2,
    text: str | None = None,
    confirm: str = "no",
) -> dict:
    """Return a record module per the Technoline Module API spec.

    The PBX returns the file name in ``<name>`` and the full path/URL in
    ``PATH_<name>`` after the recording is complete.
    """
    module: dict = {
        "type": "record",
        "name": name,
        "max": max_seconds,
        "min": min_seconds,
        "confirm": confirm,
    }
    if text:
        module["files"] = _split_text(text)
    return module


#: Step that plays a question and remembers where the digits belong. ``getDTMF``
#: carries no audio of its own, so a question is a module in its own right and
#: the capture follows on the next callback -- without this the caller hears
#: nothing but a beep.
ASK_STEP = "ask_digits"


def ask_digits(
    row: db.IvrSession,
    state: dict,
    text: str,
    *,
    next_step: str,
    min_digits: int = 1,
    max_digits: int = 10,
    timeout: int = 8,
) -> dict:
    """Play ``text``, then capture digits and hand them to ``next_step``."""
    state["ask"] = {
        "step": next_step,
        "min": min_digits,
        "max": max_digits,
        "timeout": timeout,
    }
    _save(row, ASK_STEP, state)
    return say(text)


def _collect_digits(row: db.IvrSession, state: dict, fallback: str) -> dict:
    ask = state.pop("ask", None)
    if not isinstance(ask, dict):
        _save(row, fallback, state)
        return message("error")
    _save(row, str(ask.get("step") or fallback), state)
    return get_digits(
        min_digits=int(ask.get("min") or 1),
        max_digits=int(ask.get("max") or 10),
        timeout=int(ask.get("timeout") or 8),
    )


def route(phone: str) -> dict:
    return {"type": "simpleRouting", "dialPhone": phone}


def hangup() -> dict:
    return {"type": "hangup"}


def say(text: str) -> dict:
    return {"type": "simpleMessage", "files": [{"text": text}]}


# ------------------------------------------------------------ call plumbing


def call_params(request: Request) -> dict[str, str]:
    """The PBX names its parameters differently between modules and versions,
    so every field is read through its known aliases."""
    raw = {k.lower(): v for k, v in request.query_params.items()}

    def pick(*names: str) -> str:
        for name in names:
            value = raw.get(name.lower())
            if value:
                return str(value)
        return ""

    def pick_prefix(prefix: str) -> str:
        for key, value in raw.items():
            if key.startswith(prefix.lower()):
                return str(value)
        return ""

    dtmf = pick("dtmf", "digits", "input", "value")
    # Some PBX builds append the captured digit as an unnamed query value
    # (e.g. ...&=3) when the module response does not name a DTMF variable.
    # We keep this fallback for calls that are already in flight.
    if not dtmf and raw.get(""):
        dtmf = str(raw[""])

    recording = pick("recording", "record", "recordurl", "recordingurl")
    if not recording:
        recording = pick_prefix("file_")
    recording_path = pick("path", "recording_path", "record_path")
    if not recording_path:
        recording_path = pick_prefix("path_")

    return {
        "call_id": pick("callId", "call_id", "uniqueid", "id", "pbxcallid"),
        "caller": pick("caller", "phone", "callerid", "did_caller", "from", "pbxphone"),
        "extension": pick("extension", "ext", "did", "pbxextensionid"),
        "dtmf": dtmf,
        "area": pick("area"),
        "tender": pick("tender"),
        "rating": pick("rating"),
        "recording": recording,
        "recording_path": recording_path,
    }


def _session_row(session: Session, call_id: str, caller: str) -> db.IvrSession:
    row = session.scalars(
        select(db.IvrSession).where(db.IvrSession.call_id == call_id)
    ).first()
    if row is None:
        row = db.IvrSession(call_id=call_id, phone=db.normalize_phone(caller), step="start")
        session.add(row)
        session.flush()
        return row
    # Some extensions report no call id, so the row is keyed by the caller and
    # outlives the call. A finished or stale row therefore means a new call,
    # not a caller stuck at the end of the previous one.
    if row.step == "done" or row.updated_at < datetime.utcnow() - STALE_CALL:
        row.step = "start"
        row.data = "{}"
    return row


def _state(row: db.IvrSession) -> dict:
    try:
        value = json.loads(row.data or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _save(row: db.IvrSession, step: str, state: dict) -> None:
    row.step = step
    row.data = json.dumps(state, ensure_ascii=False)
    row.updated_at = datetime.utcnow()


# ------------------------------------------------------------- driver line


@router.api_route("/driver", methods=["GET", "POST"])
async def driver_line(request: Request) -> JSONResponse:
    params = call_params(request)
    try:
        with db.session_scope() as session:
            body = _driver_step(session, params)
    except Exception:
        log.exception("driver IVR failed for %s", params)
        body = message("error")
    log.info("ivr/driver request=%s response=%s", params, json.dumps(body, ensure_ascii=False))
    return JSONResponse(body)


def _driver_step(session: Session, params: dict[str, str]) -> dict:
    caller = db.normalize_phone(params["caller"])
    row = _session_row(session, params["call_id"] or caller, caller)
    state = _state(row)
    dtmf = params["dtmf"]
    driver = drivers.get_by_phone(session, caller) if caller else None

    if row.step == "done":
        return hangup()

    # A driver who was rung about a ride gets the offer immediately; the
    # callback is the answer to the flash call, not a visit to the menu.
    if row.step == ASK_STEP:
        return _collect_digits(row, state, "menu")

    if row.step == "start":
        if driver is None:
            _save(row, "register_start", state)
            return menu("driver_register", keys="1")
        if driver.status != "active":
            return message("driver_pending")
        # A winner who was not left on hold -- a voice-campaign bidder, or one
        # whose line dropped -- is rung back, so the callback has to hand them
        # the ride they already won before anything else.
        won = dispatch.awarded_order_for_driver(session, driver)
        if won is not None:
            state["connect"] = won.phone
            _save(row, "connect", state)
            return message("driver_connecting")
        tender = None
        if params.get("tender"):
            tender = session.get(db.Tender, int(params["tender"]))
        if tender is None:
            tender = dispatch.latest_tender_for_driver(session, driver)
        if tender is not None and tender.status == dispatch.STATUS_OPEN:
            order = session.get(db.Order, tender.order_id)
            state.update({"tender": tender.id, "order": order.id if order else None})
            _save(row, "offer", state)
            offer = tts.offer_text(order) if order else tts.AUDIO_TEXTS.get("driver_offer", "")
            return menu("driver_offer", keys="1", text=offer)
        _save(row, "menu", state)
        return menu("driver_menu", keys="1,2,3,4,5,6")

    if row.step == "offer":
        tender = session.get(db.Tender, int(state.get("tender") or 0))
        if tender is None:
            _save(row, "menu", state)
            return menu("driver_menu", keys="1,2,3,4,5,6")
        if dtmf != "1":
            _save(row, "menu", state)
            return menu("driver_menu", keys="1,2,3,4,5,6")
        result = dispatch.place_bid(session, tender, driver)
        if not result.get("ok"):
            _save(row, "done", state)
            return message("driver_taken")
        # The hold message runs for the rest of the window; the PBX comes back
        # when it ends, which is when the auction is decided.
        _save(row, "await_result", state)
        return message("driver_wait")

    if row.step == "await_result":
        tender = session.get(db.Tender, int(state.get("tender") or 0))
        if tender is None:
            return message("driver_taken")
        outcome = dispatch.result_for_driver(session, tender, driver)
        if outcome.get("won") and outcome.get("passenger_phone"):
            state["connect"] = outcome["passenger_phone"]
            _save(row, "connect", state)
            return message("driver_connecting")
        _save(row, "done", state)
        return message("driver_taken")

    if row.step == "connect":
        phone = str(state.pop("connect", "") or "")
        _save(row, "done", state)
        return route(phone) if phone else message("driver_taken")

    if row.step == "menu":
        return _driver_menu_choice(session, row, state, driver, dtmf)

    if row.step == "register_start":
        if dtmf != "1":
            _save(row, "done", state)
            return hangup()
        return _ask_car_year(row, state)

    if row.step == "register_car_year":
        year = _year(dtmf, low=1980, high=datetime.utcnow().year + 1)
        if year is None:
            return _retry(row, state, "car_year", _ask_car_year)
        state["car_year"] = year
        return _ask_seats(row, state)

    if row.step == "register_seats":
        seats = _in_range(dtmf, low=1, high=9)
        if seats is None:
            return _retry(row, state, "seats", _ask_seats)
        state["seats"] = seats
        return _ask_birth_year(row, state)

    if row.step == "register_birth_year":
        year = _year(dtmf, low=1930, high=datetime.utcnow().year - 17)
        if year is None:
            return _retry(row, state, "birth_year", _ask_birth_year)
        state["birth_year"] = year
        names = [area.name for area in _active_areas(session)]
        if not names:
            return _finish_registration(session, row, state, caller, area=None)
        _save(row, "register_area", state)
        return menu(
            "driver_area_prompt",
            text=tts.area_menu_text(tts.AUDIO_TEXTS["driver_area_prompt"], names),
            keys=",".join(str(i) for i in range(1, len(names) + 1)),
        )

    if row.step == "register_area":
        area = _area_by_index(session, dtmf)
        if area is None:
            return _finish_registration(session, row, state, caller, area=None)
        return _finish_registration(session, row, state, caller, area=area.name)

    if row.step == "areas":
        if dtmf == "1":
            state["area_action"] = "add"
            _save(row, "area_select", state)
            return _area_pick_menu(session, tts.AUDIO_TEXTS["driver_area_add_prompt"])
        if dtmf == "2":
            state["area_action"] = "remove"
            _save(row, "area_select", state)
            return _area_pick_menu(session, tts.AUDIO_TEXTS["driver_area_remove_prompt"])
        if dtmf == "3":
            areas_text = drivers.areas_list_text(session, driver)
            menu_text = f"{areas_text} {tts.AUDIO_TEXTS['driver_areas_menu']}"
            _save(row, "areas", state)
            return menu("driver_areas_menu", text=menu_text, keys="1,2,3,4")
        if dtmf == "4" or dtmf == "":
            _save(row, "done", state)
            return message("driver_saved")
        _save(row, "menu", state)
        return menu("driver_menu", keys="1,2,3,4,5,6")

    if row.step == "area_select":
        area = _area_by_index(session, dtmf)
        action = state.get("area_action")
        if area is None or driver is None:
            _save(row, "areas", state)
            return menu(
                "driver_areas_menu",
                text=f"{tts.AUDIO_TEXTS['driver_invalid_input']} "
                f"{tts.AUDIO_TEXTS['driver_areas_menu']}",
                keys="1,2,3,4",
            )
        if action == "add":
            result = drivers.add_area(session, driver, area.name)
            prompt = "driver_area_added" if result["ok"] else "driver_area_already"
        elif action == "remove":
            result = drivers.remove_area(session, driver, area.name)
            prompt = "driver_area_removed" if result["ok"] else "driver_area_not_found"
        else:
            _save(row, "areas", state)
            return menu("driver_areas_menu", keys="1,2,3,4")
        confirm = tts.AUDIO_TEXTS.get(prompt, "")
        menu_text = f"{confirm} {tts.AUDIO_TEXTS['driver_areas_menu']}"
        _save(row, "areas", state)
        return menu("driver_areas_menu", text=menu_text, keys="1,2,3,4")

    if row.step == "quiet_from":
        if _hour(dtmf) is None:
            return ask_digits(
                row,
                state,
                f"{tts.AUDIO_TEXTS['driver_invalid_input']} "
                f"{tts.AUDIO_TEXTS['driver_quiet_prompt']}",
                next_step="quiet_from",
                min_digits=2,
                max_digits=2,
            )
        state["quiet_from"] = dtmf
        return ask_digits(
            row,
            state,
            tts.AUDIO_TEXTS["driver_ask_quiet_to"],
            next_step="quiet_to",
            min_digits=2,
            max_digits=2,
        )

    if row.step == "quiet_to":
        if driver is not None:
            driver.quiet_from = _hour(state.get("quiet_from"))
            driver.quiet_to = _hour(dtmf)
        _save(row, "done", state)
        return message("driver_saved")

    if row.step == "location_choice":
        area = _area_by_index(session, dtmf)
        if area is None or driver is None:
            _save(row, "done", state)
            return message("error")
        result = drivers.report_location(session, driver, area.name, source="declared")
        _save(row, "done", state)
        return message("driver_location_done" if result["ok"] else "error")

    _save(row, "menu", state)
    return menu("driver_menu", keys="1,2,3,4,5,6")


def _driver_menu_choice(
    session: Session, row: db.IvrSession, state: dict, driver: db.Driver | None, dtmf: str
) -> dict:
    if driver is None:
        return message("error")
    if dtmf == "1":  # current offer
        tender = dispatch.open_tender_for_area(session, driver.last_area or driver.home_area)
        if tender is None:
            _save(row, "done", state)
            return message("driver_no_offer")
        state["tender"] = tender.id
        _save(row, "offer", state)
        order = session.get(db.Order, tender.order_id)
        offer = tts.offer_text(order) if order else tts.AUDIO_TEXTS.get("driver_offer", "")
        return menu("driver_offer", keys="1", text=offer)
    if dtmf == "2":  # reputation
        _save(row, "done", state)
        return say(tts.reputation_text(driver))
    if dtmf == "3":  # preferred areas
        _save(row, "areas", state)
        return menu("driver_areas_menu", keys="1,2,3,4")
    if dtmf == "4":  # quiet hours
        return ask_digits(
            row,
            state,
            tts.AUDIO_TEXTS["driver_quiet_prompt"],
            next_step="quiet_from",
            min_digits=2,
            max_digits=2,
        )
    if dtmf == "5":  # location update
        _save(row, "location_choice", state)
        return _area_pick_menu(session, tts.AUDIO_TEXTS["driver_location_prompt"])
    if dtmf == "6":  # ride finished
        order = session.scalars(
            select(db.Order)
            .where(db.Order.driver_id == driver.id, db.Order.status.in_(("assigned", "on_route")))
            .order_by(db.Order.created_at.desc())
            .limit(1)
        ).first()
        if order is None:
            _save(row, "done", state)
            return message("error")
        dispatch.finish_ride(session, order)
        _save(row, "done", state)
        return message("driver_finish_done")
    return menu("driver_menu", keys="1,2,3,4,5,6")


def _hour(value: object) -> int | None:
    try:
        hour = int(str(value))
    except (TypeError, ValueError):
        return None
    return hour if 0 <= hour <= 23 else None


def _in_range(value: object, *, low: int, high: int) -> int | None:
    try:
        number = int(str(value))
    except (TypeError, ValueError):
        return None
    return number if low <= number <= high else None


def _year(value: object, *, low: int, high: int) -> int | None:
    text = str(value or "")
    return _in_range(text, low=low, high=high) if len(text) == 4 else None


def _ask_car_year(row: db.IvrSession, state: dict, prefix: str = "") -> dict:
    return ask_digits(
        row,
        state,
        f"{prefix}{tts.AUDIO_TEXTS['driver_ask_car_year']}",
        next_step="register_car_year",
        min_digits=4,
        max_digits=4,
    )


def _ask_seats(row: db.IvrSession, state: dict, prefix: str = "") -> dict:
    return ask_digits(
        row,
        state,
        f"{prefix}{tts.AUDIO_TEXTS['driver_ask_seats']}",
        next_step="register_seats",
        min_digits=1,
        max_digits=1,
    )


def _ask_birth_year(row: db.IvrSession, state: dict, prefix: str = "") -> dict:
    return ask_digits(
        row,
        state,
        f"{prefix}{tts.AUDIO_TEXTS['driver_ask_birth_year']}",
        next_step="register_birth_year",
        min_digits=4,
        max_digits=4,
    )


#: A caller who keeps mis-keying is not helped by a fourth attempt.
MAX_TRIES = 3


def _retry(row: db.IvrSession, state: dict, field: str, ask) -> dict:
    """Re-ask one question, saying so, and give up after a few attempts."""
    tries = int(state.get(f"tries_{field}") or 0) + 1
    state[f"tries_{field}"] = tries
    if tries >= MAX_TRIES:
        _save(row, "done", state)
        return message("error")
    return ask(row, state, f"{tts.AUDIO_TEXTS['driver_invalid_input']} ")


def _finish_registration(
    session: Session, row: db.IvrSession, state: dict, caller: str, *, area: str | None
) -> dict:
    """Save what the call collected. Only the supplied fields are written, so a
    driver who rings back to fix one detail keeps the rest — and an approved
    driver is never dropped back to `pending`."""
    car_year = _in_range(state.get("car_year"), low=1980, high=datetime.utcnow().year + 1)
    seats = _in_range(state.get("seats"), low=1, high=9)
    birth_year = _in_range(state.get("birth_year"), low=1930, high=datetime.utcnow().year - 17)
    driver = drivers.register(
        session,
        caller,
        car_year=car_year,
        seats=seats,
        birth_year=birth_year,
        home_area=area,
    )
    if area:
        drivers.add_area(session, driver, area)
    _save(row, "done", state)
    return say(tts.registration_text(car_year=car_year, seats=seats, area=area))


def _active_areas(session: Session) -> list[db.Area]:
    return list(
        session.scalars(select(db.Area).where(db.Area.active.is_(True)).order_by(db.Area.id)).all()
    )


def _area_pick_menu(session: Session, prompt: str) -> dict:
    """Speak the areas and their digits; the digits come from the table order."""
    names = [area.name for area in _active_areas(session)]
    if not names:
        return message("driver_no_areas")
    return menu(
        "driver_area_prompt",
        text=tts.area_menu_text(prompt, names),
        keys=",".join(str(index) for index in range(1, len(names) + 1)),
    )


def _area_by_index(session: Session, dtmf: str) -> db.Area | None:
    """Menu digits map onto the active areas in display order, so adding an
    area never means re-recording the menu's numbering by hand."""
    try:
        index = int(dtmf)
    except (TypeError, ValueError):
        return None
    rows = _active_areas(session)
    if 1 <= index <= len(rows):
        return rows[index - 1]
    return None


# ---------------------------------------------------------- passenger line


@router.api_route("/passenger", methods=["GET", "POST"])
async def passenger_line(request: Request) -> JSONResponse:
    params = call_params(request)
    try:
        with db.session_scope() as session:
            body = _passenger_step(session, params)
    except Exception:
        log.exception("passenger IVR failed for %s", params)
        body = message("error")
    log.info("ivr/passenger request=%s response=%s", params, json.dumps(body, ensure_ascii=False))
    return JSONResponse(body)


def _passenger_step(session: Session, params: dict[str, str]) -> dict:
    caller = db.normalize_phone(params["caller"]) if params["caller"] else ""
    row = _session_row(session, params["call_id"] or caller, caller)
    state = _state(row)
    dtmf = params["dtmf"]

    if row.step == "done":
        return hangup()

    if row.step == "start":
        # Ringing in is how an invited number confirms its referral, so this
        # happens before any menu and regardless of what the caller wanted.
        referrals.confirm_by_call(session, caller)
        _save(row, "menu", state)
        return menu("passenger_menu", keys="1,2,3,4")

    if row.step == "menu":
        if dtmf == "1":
            _save(row, "done", state)
            balance = loyalty.balance(session, caller)
            return say(tts.AUDIO_TEXTS["passenger_balance"].format(balance=balance))
        if dtmf == "2":
            order = session.scalars(
                select(db.Order)
                .where(db.Order.phone == caller, db.Order.status.in_(("new", "assigned")))
                .order_by(db.Order.created_at.desc())
                .limit(1)
            ).first()
            if order is None or not loyalty.can_redeem(session, caller):
                _save(row, "done", state)
                return message("passenger_redeem_no")
            result = loyalty.redeem_ride(session, order, actor=f"ivr:{caller}")
            _save(row, "done", state)
            return message("passenger_redeem_ok" if result["redeemed"] else "passenger_redeem_no")
        if dtmf == "3":
            return ask_digits(
                row,
                state,
                tts.AUDIO_TEXTS["passenger_refer_prompt"],
                next_step="refer_number",
                min_digits=9,
                max_digits=10,
                timeout=12,
            )
        if dtmf == "4":
            _save(row, "done", state)
            return message("passenger_prefs")
        return menu("passenger_menu", keys="1,2,3,4")

    if row.step == ASK_STEP:
        return _collect_digits(row, state, "menu")

    if row.step == "refer_number":
        result = referrals.assign(session, caller, dtmf, actor=f"ivr:{caller}")
        _save(row, "done", state)
        if not result.get("ok"):
            return message("passenger_refer_no")
        # A single ring on the invited phone leaves our number in their missed
        # calls, which is all the confirmation call needs.
        pbx.flash_call(session, dtmf, kind="referral")
        return message("passenger_refer_ok")

    _save(row, "menu", state)
    return menu("passenger_menu", keys="1,2,3,4")


# -------------------------------------------------------------- rating line


@router.api_route("/rating", methods=["GET", "POST"])
async def rating_line(request: Request) -> JSONResponse:
    params = call_params(request)
    try:
        with db.session_scope() as session:
            body = _rating_step(session, params)
    except Exception:
        log.exception("rating IVR failed for %s", params)
        body = message("error")
    log.info("ivr/rating request=%s response=%s", params, json.dumps(body, ensure_ascii=False))
    return JSONResponse(body)


def _rating_step(session: Session, params: dict[str, str]) -> dict:
    caller = db.normalize_phone(params["caller"]) if params["caller"] else ""
    row = _session_row(session, params["call_id"] or caller, caller)
    state = _state(row)
    dtmf = params["dtmf"]

    request_id = params["rating"] or state.get("rating")
    if request_id:
        state["rating"] = str(request_id)

    if row.step == "done":
        return hangup()

    if row.step == "start" or (row.step == "score" and not dtmf):
        _save(row, "score", state)
        return menu("rating_prompt", keys="1,2,3,4,5")

    if row.step == "score" and dtmf:
        rating = (
            session.get(db.RatingRequest, int(state["rating"]))
            if str(state.get("rating") or "").isdigit()
            else None
        )
        if rating is None:
            rating = session.scalars(
                select(db.RatingRequest)
                .where(
                    db.RatingRequest.phone == caller,
                    db.RatingRequest.status.in_((ratings.STATUS_CALLING, ratings.STATUS_SCHEDULED)),
                )
                .order_by(db.RatingRequest.due_at.desc())
                .limit(1)
            ).first()
        if rating is None:
            _save(row, "done", state)
            return message("rating_thanks")

        try:
            score = int(dtmf[:1])
        except ValueError:
            return menu("rating_prompt", keys="1,2,3,4,5")
        if score < 1 or score > 5:
            return menu("rating_prompt", keys="1,2,3,4,5")

        if rating is not None:
            state["rating"] = str(rating.id)
            state["score"] = str(score)
        if score < 4:
            _save(row, "record_feedback", state)
            return record(
                "feedback",
                max_seconds=30,
                min_seconds=2,
                confirm="no",
                text="אם תוכל לשתף יותר פרטים, נא הקלט כעת",
            )

        if rating is not None:
            ratings.record_score(session, rating, score)
        _save(row, "done", state)
        return message("rating_thanks")

    if row.step == "record_feedback":
        rating = (
            session.get(db.RatingRequest, int(state["rating"]))
            if str(state.get("rating") or "").isdigit()
            else None
        )
        score = int(state["score"]) if state.get("score") and state["score"].isdigit() else None
        recording_path = params["recording_path"] or params["recording"]
        if rating is not None and score is not None:
            ratings.record_score(session, rating, score, feedback_url=recording_path or None)
        _save(row, "done", state)
        return message("rating_thanks")

    _save(row, "done", state)
    return message("rating_thanks")
