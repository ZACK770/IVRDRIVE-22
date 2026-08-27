"""The one background loop: closing auctions, dialling ratings, expiring
referrals.

Everything it does is also reachable synchronously from the request path, so
the loop is an optimisation rather than a dependency — a tender whose window
expired is closed by the next driver who asks about it even if this worker is
dead. That keeps a single-instance timer from becoming a single point of
failure for the phone line.
"""

from __future__ import annotations

import asyncio
import logging
import os

from sqlalchemy import select

from app import db, dispatch, pbx, ratings, referrals

log = logging.getLogger("scheduler")

INTERVAL_S = float(os.getenv("SCHEDULER_INTERVAL_S", "2"))
CAMPAIGN_INTERVAL_S = float(os.getenv("CAMPAIGN_POLL_INTERVAL_S", "30"))
ENABLED = os.getenv("SCHEDULER_ENABLED", "1").lower() not in {"0", "false", "no"}

_task: asyncio.Task | None = None
_campaign_task: asyncio.Task | None = None


def tick() -> dict:
    with db.session_scope() as session:
        closed = dispatch.reap(session)
        called = ratings.run_due(session)
        expired = referrals.expire_stale(session)
    return {"tenders_closed": closed, "ratings_called": called, "referrals_expired": expired}


def monitor_voice_campaigns() -> int:
    """Stop paid voice campaigns once they have answered enough drivers."""
    threshold = db.setting_int("voice_campaign_stop_answered") or 30
    with db.session_scope() as session:
        open_tenders = session.scalars(
            select(db.Tender).where(
                db.Tender.status == dispatch.STATUS_OPEN,
                db.Tender.campaign_id.is_not(None),
            )
        ).all()
        stopped = 0
        for tender in open_tenders:
            try:
                report = pbx.campaign_report(str(tender.campaign_id))
                campaign = report.get("campaign", {})
                answered = int(campaign.get("answeredCalls", "0") or "0")
                status = campaign.get("status", "")
                if answered >= threshold:
                    pbx.stop_campaign(str(tender.campaign_id))
                    log.info(
                        "stopped campaign %s for tender %s after %s answered calls",
                        tender.campaign_id,
                        tender.id,
                        answered,
                    )
                    stopped += 1
                    tender.campaign_id = None
                    session.flush()
                elif status == "הסתיים":
                    log.info("campaign %s for tender %s has ended", tender.campaign_id, tender.id)
                    tender.campaign_id = None
                    session.flush()
            except Exception:
                log.exception("campaign monitor failed for tender %s", tender.id)
        return stopped


async def _loop() -> None:
    while True:
        try:
            result = await asyncio.to_thread(tick)
            if any(result.values()):
                log.info("scheduler %s", result)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("scheduler tick failed")
        await asyncio.sleep(INTERVAL_S)


async def _campaign_loop() -> None:
    while True:
        try:
            stopped = await asyncio.to_thread(monitor_voice_campaigns)
            if stopped:
                log.info("campaign monitor stopped %s campaign(s)", stopped)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("campaign monitor tick failed")
        await asyncio.sleep(CAMPAIGN_INTERVAL_S)


def start() -> None:
    global _task, _campaign_task
    if not ENABLED:
        return
    if _task is None:
        _task = asyncio.create_task(_loop())
        log.info("scheduler started, every %ss", INTERVAL_S)
    if _campaign_task is None:
        _campaign_task = asyncio.create_task(_campaign_loop())
        log.info("campaign monitor started, every %ss", CAMPAIGN_INTERVAL_S)


async def stop() -> None:
    global _task, _campaign_task
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None
    if _campaign_task is not None:
        _campaign_task.cancel()
        try:
            await _campaign_task
        except asyncio.CancelledError:
            pass
        _campaign_task = None
