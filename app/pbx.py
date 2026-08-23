"""Client for the Technoline Interaction API — our server calling the PBX.

Three surfaces, three trust models, and they are not interchangeable:

* ``ivrFilesApi.php?action=makeCall`` places the flash call ("צינתוק"): it rings
  the recipient with a caller ID we choose and hangs up the moment they answer.
  No audio, no cost to the driver, and the number stays in their missed calls.
  Authenticated by IP whitelist only — no apiKey.
* ``campaignApi.php`` broadcasts a recorded offer to a list of numbers. apiKey
  *and* IP whitelist. This is the paid path, used for drivers who bought spoken
  offers.
* ``ivrFilesApi.php`` (everything else) manages extensions and audio files with
  an apiKey.

The documented rate limit on ``makeCall`` is one call per number per two
minutes, and the docs are explicit that hitting it returns an error rather than
queueing, so the debounce lives here, in our own ledger, before the request is
made.
"""

from __future__ import annotations

import logging
import os
import random
from datetime import datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import db

log = logging.getLogger("pbx")

BASE_URL = os.getenv("PBX_BASE_URL", "https://app.ipsales.co.il").rstrip("/")
API_KEY = os.getenv("PBX_API_KEY", "")


def _flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes"}


#: Dialling is opt-in. `makeCall` and `campaignRun` need a whitelisted source IP.
#: `PBX_LIVE=1` or `PBX_API_KEY` set disables dry-run; both endpoints also send
#: the apiKey in the request body.
DRY_RUN = not (_flag("PBX_LIVE") or bool(os.getenv("PBX_API_KEY")))
TIMEOUT_S = float(os.getenv("PBX_TIMEOUT_S", "10"))
#: The PBX's own limit; we stay one second clear of it.
DEBOUNCE_SECONDS = int(os.getenv("PBX_FLASH_DEBOUNCE_S", "125"))


class PbxError(RuntimeError):
    pass


def _ok(payload: dict) -> bool:
    """Older endpoints answer `Ok`, newer ones `OK`; both mean success.
    Campaign actions use `errorCode: 0`, and the report endpoint returns
    `campaign` / `calls` without a `status` wrapper.
    """
    if not isinstance(payload, dict):
        return False
    if str(payload.get("status", "")).strip().lower() == "ok":
        return True
    if str(payload.get("errorCode", "")) == "0":
        return True
    if "campaign" in payload or "calls" in payload:
        return True
    return False


def _request(
    action: str,
    params: dict,
    *,
    endpoint: str = "ivrFilesApi.php",
    json_body: bool = False,
) -> dict:
    url = f"{BASE_URL}/{endpoint}"
    body = {"action": action, **{k: v for k, v in params.items() if v is not None}}
    # The Technoline endpoints require apiKey for authentication; makeCall is no
    # exception even though the docs once described it as IP-only.
    if API_KEY:
        body.setdefault("apiKey", API_KEY)
    if DRY_RUN:
        redacted = {k: ("***" if k == "apiKey" else v) for k, v in body.items()}
        log.info("pbx dry-run %s %s", url, redacted)
        return {"status": "OK", "dry_run": True}
    try:
        if json_body:
            response = httpx.post(url, json=body, timeout=TIMEOUT_S, follow_redirects=True)
        else:
            response = httpx.post(url, data=body, timeout=TIMEOUT_S, follow_redirects=True)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise PbxError(f"{action}: {exc}") from exc
    if not _ok(payload):
        raise PbxError(f"{action}: {payload.get('note') or payload}")
    return payload


def new_cid(seed: int | None = None) -> str:
    """The six digits the driver's phone displays. Derived from the tender id
    when there is one, so the missed call itself says which ride it was."""
    if seed is not None:
        return f"{seed % 1_000_000:06d}"
    return f"{random.randint(0, 999_999):06d}"


def recently_called(session: Session, phone: str, seconds: int = DEBOUNCE_SECONDS) -> bool:
    cutoff = datetime.utcnow() - timedelta(seconds=seconds)
    return bool(
        session.scalars(
            select(db.FlashCall.id).where(
                db.FlashCall.phone == db.normalize_phone(phone),
                db.FlashCall.created_at >= cutoff,
                db.FlashCall.status.in_(("sent", "dry_run")),
            )
        ).first()
    )


def flash_call(
    session: Session,
    phone: str,
    *,
    cid: str | None = None,
    driver_id: int | None = None,
    tender_id: int | None = None,
    kind: str = "tender",
) -> dict:
    """One ring on the recipient's phone showing `cid`, then silence.

    Returns the ledger row's outcome rather than raising, because a blast of
    fifty drivers must not stop at the first number that is in cooldown.
    """
    phone = db.normalize_phone(phone)
    cid = cid or new_cid(tender_id)
    if recently_called(session, phone):
        session.add(
            db.FlashCall(
                phone=phone,
                driver_id=driver_id,
                tender_id=tender_id,
                cid=cid,
                kind=kind,
                status="debounced",
                note="נקרא לאחרונה לפני פחות משתי דקות",
            )
        )
        return {"sent": False, "status": "debounced", "phone": phone}

    status, note = "sent", None
    try:
        payload = _request("makeCall", {"phone": phone, "cid": cid})
        if payload.get("dry_run"):
            status = "dry_run"
    except PbxError as exc:
        status, note = "failed", str(exc)
        log.warning("flash call to %s failed: %s", phone, exc)

    session.add(
        db.FlashCall(
            phone=phone,
            driver_id=driver_id,
            tender_id=tender_id,
            cid=cid,
            kind=kind,
            status=status,
            note=note,
        )
    )
    return {"sent": status in {"sent", "dry_run"}, "status": status, "cid": cid, "phone": phone}


def connect_call(
    session: Session,
    driver_phone: str,
    passenger_phone: str,
    *,
    text: str,
    driver_id: int | None = None,
    tender_id: int | None = None,
) -> dict:
    """Ring the winning driver; when they answer they hear ``text`` and press
    1 to be bridged to the passenger (the documented CRM "agent connect"
    pattern: ``campaignRun`` with a ``keysAction-1: routing-<phone>``).
    """
    driver_phone = db.normalize_phone(driver_phone)
    passenger_phone = db.normalize_phone(passenger_phone)

    cutoff = datetime.utcnow() - timedelta(seconds=60)
    already = session.scalars(
        select(db.FlashCall.id).where(
            db.FlashCall.phone == driver_phone,
            db.FlashCall.kind == "connect",
            db.FlashCall.tender_id == tender_id,
            db.FlashCall.created_at >= cutoff,
            db.FlashCall.status.in_(("sent", "dry_run")),
        )
    ).first()
    if already:
        return {"sent": False, "status": "debounced", "phone": driver_phone}

    status, note, campaign_id = "sent", None, None
    try:
        payload = _request(
            "campaignRun",
            {
                "audioText": text,
                "phones": [driver_phone],
                "title": "חיבור נהג לנוסע",
                "callLength": 25,
                "dialRetries": 2,
                "betweenRetries": 5,
                "keysAction-1": f"routing-{passenger_phone}",
            },
            endpoint="campaignApi.php",
            json_body=True,
        )
        campaign_id = payload.get("campaignId")
        if payload.get("dry_run"):
            status = "dry_run"
    except PbxError as exc:
        status, note = "failed", str(exc)
        log.warning("connect call to %s failed: %s", driver_phone, exc)

    session.add(
        db.FlashCall(
            phone=driver_phone,
            driver_id=driver_id,
            tender_id=tender_id,
            cid=None,
            kind="connect",
            status=status,
            note=note,
        )
    )
    return {
        "sent": status in {"sent", "dry_run"},
        "status": status,
        "campaign_id": campaign_id,
        "phone": driver_phone,
    }


def voice_broadcast(
    phones: list[str], *, name: str, module_url: str | None = None
) -> dict:
    """Start a paid outbound campaign that can receive return calls.

    Unlike ``makeCall``, this is a normal campaign call: the PBX presents its
    configured campaign caller ID and the driver can call that number back.
    """
    if not phones:
        return {"started": False, "note": "אין נמענים"}
    params: dict[str, object] = {
        "campaignName": name,
        "phones": [db.normalize_phone(p) for p in phones],
        "messagesType": "apiUrl",
        "callLength": 25,
        "dialRetries": 1,
        "betweenRetries": 20,
    }
    if not module_url:
        raise PbxError(
            "אין כתובת ציבורית למודול ה-IVR: הגדירו public_base_url כדי לשגר קמפיין"
        )
    params["apiUrl"] = module_url
    payload = _request("campaignRun", params, endpoint="campaignApi.php", json_body=True)
    return {"started": True, "campaign_id": payload.get("campaignId"), "response": payload}


def campaign_broadcast(
    session: Session,
    targets: list[tuple[int, str]],
    *,
    tender_id: int | None,
    name: str,
    module_url: str,
) -> dict:
    """Broadcast one tender campaign and record each recipient in the ledger."""
    pending = [
        (driver_id, db.normalize_phone(phone))
        for driver_id, phone in targets
        if not recently_called(session, phone)
    ]
    if not pending:
        return {"started": False, "sent": 0, "status": "debounced"}

    try:
        result = voice_broadcast(
            [phone for _, phone in pending],
            name=name,
            module_url=module_url,
        )
    except PbxError as exc:
        for driver_id, phone in pending:
            session.add(
                db.FlashCall(
                    phone=phone,
                    driver_id=driver_id,
                    tender_id=tender_id,
                    kind="campaign",
                    status="failed",
                    note=str(exc),
                )
            )
        raise

    campaign_id = result.get("campaign_id")
    status = "dry_run" if result.get("response", {}).get("dry_run") else "sent"
    for driver_id, phone in pending:
        session.add(
            db.FlashCall(
                phone=phone,
                driver_id=driver_id,
                tender_id=tender_id,
                kind="campaign",
                status=status,
                note=f"campaign {campaign_id}" if campaign_id else None,
            )
        )
    return {
        "started": True,
        "sent": len(pending),
        "campaign_id": campaign_id,
        "status": status,
    }


def campaign_report(campaign_id: str) -> dict:
    return _request(
        "campaignReport", {"campaignId": campaign_id}, endpoint="campaignApi.php"
    )


def stop_campaign(campaign_id: str) -> dict:
    return _request("campaignStop", {"campaignId": campaign_id}, endpoint="campaignApi.php")


def upload_file(file_name: str, data: bytes, *, mime: str = "audio/mpeg") -> dict:
    """Upload an audio file to the PBX audio library so it can be referenced by
    name from a Module API response. Dry-run just logs the byte size.
    """
    if DRY_RUN:
        log.info("pbx upload dry-run: %s (%s bytes)", file_name, len(data))
        return {"status": "OK", "dry_run": True, "fileName": file_name}
    if not API_KEY:
        raise PbxError("upload_file: PBX_API_KEY is required")
    try:
        response = httpx.post(
            f"{BASE_URL}/ivrFilesApi.php",
            data={"action": "uploadFile", "apiKey": API_KEY, "fileName": file_name},
            files={"file": (f"{file_name}.mp3", data, mime)},
            timeout=TIMEOUT_S,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise PbxError(f"upload_file: {exc}") from exc
    if not _ok(payload):
        raise PbxError(f"upload_file: {payload.get('note') or payload}")
    return payload
