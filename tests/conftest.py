"""Test wiring.

The database URL is read at import time by ``app.db``, so it is set here
— before any application module is imported — rather than in a fixture.
``app.pbx`` defaults to dry-run when neither ``PBX_LIVE`` nor ``PBX_API_KEY``
are set, which is the desired test behaviour.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="ivrdrive-tests-"))
os.environ["BOT_DB_URL"] = f"sqlite:///{_TMP / 'test.db'}"
os.environ["SCHEDULER_ENABLED"] = "0"
os.environ["CAPTURE_DIR"] = str(_TMP / "captures")

from app import db  # noqa: E402  (must follow the env setup above)


@pytest.fixture(autouse=True)
def clean_db() -> Iterator[None]:
    db.Base.metadata.drop_all(db.engine)
    db.Base.metadata.create_all(db.engine)
    # Outbound campaigns hand the PBX a callback URL, so a deployment without
    # one cannot dial at all; tests run as a configured deployment does.
    db.set_setting("public_base_url", "https://tests.local")
    yield


@pytest.fixture()
def session() -> Iterator[db.Session]:
    with db.session_scope() as active:
        yield active
