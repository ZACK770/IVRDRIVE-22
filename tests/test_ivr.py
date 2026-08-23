"""The Module API contract.

The PBX drops back to the previous menu on any JSON it cannot parse, so the
one thing every branch — including the failure branches — must guarantee is a
module of a documented type.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import db, dispatch, drivers, loyalty, ratings, referrals, tts
from app.main import app

#: The subset of the documented module list this service emits.
KNOWN_TYPES = {"simpleMessage", "simpleMenu", "getDTMF", "simpleRouting", "hangup", "record"}

DRIVER = "0521111111"
PASSENGER = "0529999999"


@pytest.fixture()
def client() -> TestClient:
    with TestClient(app) as active:
        yield active


def call(client: TestClient, path: str, **params) -> dict:
    response = client.get(path, params=params)
    assert response.status_code == 200
    body = response.json()
    assert body["type"] in KNOWN_TYPES, body
    return body


def test_an_unknown_driver_is_offered_registration(client):
    body = call(client, "/ivr/driver", callId="c1", caller=DRIVER)
    assert body["files"][0]["text"] == tts.AUDIO_TEXTS["driver_register"]


def step(client: TestClient, **params) -> dict:
    return call(client, "/ivr/driver", callId="c1", caller=DRIVER, **params)


def test_every_registration_question_is_spoken_before_the_digits(client):
    """getDTMF carries no audio, so a question the caller can hear has to be a
    module of its own — without it the line is silent but for the beep."""
    with db.session_scope() as session:
        session.add(db.Area(name="ירושלים", callback_number="0765673575"))

    step(client)
    # Each question is one module and the digit capture the next, so the
    # sequence alternates: ask, capture, answer.
    assert step(client, dtmf="1")["files"][0]["text"] == tts.AUDIO_TEXTS["driver_ask_car_year"]
    assert step(client)["type"] == "getDTMF"
    assert step(client, dtmf="2021")["files"][0]["text"] == tts.AUDIO_TEXTS["driver_ask_seats"]
    assert step(client)["type"] == "getDTMF"
    assert step(client, dtmf="4")["files"][0]["text"] == tts.AUDIO_TEXTS["driver_ask_birth_year"]
    assert step(client)["type"] == "getDTMF"

    area_menu = step(client, dtmf="1980")
    assert any("ירושלים" in f["text"] for f in area_menu["files"])
    done = step(client, dtmf="1")
    assert tts.AUDIO_TEXTS["driver_pending"] in done["files"][0]["text"]

    with db.session_scope() as session:
        driver = drivers.get_by_phone(session, DRIVER)
        assert (driver.car_year, driver.seats, driver.birth_year) == (2021, 4, 1980)
        assert (driver.home_area, driver.status) == ("ירושלים", "pending")
        assert drivers.areas_of(session, driver) == ["ירושלים"]


def test_a_mis_keyed_answer_is_asked_again(client):
    step(client)
    step(client, dtmf="1")  # the car-year question
    step(client, dtmf="2021")  # the capture module
    again = step(client, dtmf="1")  # one digit where four are needed
    assert tts.AUDIO_TEXTS["driver_invalid_input"] in again["files"][0]["text"]
    assert tts.AUDIO_TEXTS["driver_ask_car_year"] in again["files"][0]["text"]


def test_a_second_call_on_a_reused_id_starts_from_the_top(client):
    """Extensions that report no call id key the row by the caller, so the row
    survives the call and must not strand them mid-menu."""
    call(client, "/ivr/driver", callId="", caller=DRIVER)
    call(client, "/ivr/driver", callId="", caller=DRIVER, dtmf="9")

    again = call(client, "/ivr/driver", callId="", caller=DRIVER)

    assert again["files"][0]["text"] == tts.AUDIO_TEXTS["driver_register"]


def test_registration_never_demotes_a_driver_the_office_approved(client):
    with db.session_scope() as session:
        drivers.register(session, DRIVER, status="active")

    with db.session_scope() as session:
        drivers.register(session, DRIVER, car_year=2021)
        assert drivers.get_by_phone(session, DRIVER).status == "active"


def test_a_pending_driver_hears_that_and_nothing_else(client):
    with db.session_scope() as session:
        drivers.register(session, DRIVER)
    body = call(client, "/ivr/driver", callId="c2", caller=DRIVER)
    assert body["files"][0]["text"] == tts.AUDIO_TEXTS["driver_pending"]


def test_the_ride_offer_holds_the_driver_until_the_window_closes(client):
    with db.session_scope() as session:
        drivers.register(session, DRIVER, status="active")
        order = db.Order(
            call_id="x", phone=PASSENGER, origin="ירושלים", destination="בני ברק", price=80.0
        )
        session.add(order)
        session.flush()
        dispatch.open_tender(session, order, area="ירושלים")

    offer = call(client, "/ivr/driver", callId="c3", caller=DRIVER)
    full_offer = " ".join(f["text"] for f in offer["files"]).strip()
    assert full_offer == tts.offer_text(order)

    waiting = call(client, "/ivr/driver", callId="c3", caller=DRIVER, dtmf="1")
    assert waiting["files"][0]["text"] == tts.AUDIO_TEXTS["driver_wait"]

    with db.session_scope() as session:
        tender = session.scalars(db.select(db.Tender)).first()
        tender.closes_at = tender.opened_at

    won = call(client, "/ivr/driver", callId="c3", caller=DRIVER)
    assert won["files"][0]["text"] == tts.AUDIO_TEXTS["driver_won_menu"]

    bridged = call(client, "/ivr/driver", callId="c3", caller=DRIVER, dtmf="1")
    assert bridged["type"] == "simpleRouting"
    assert bridged["dialPhone"] == PASSENGER

    # The PBX reports a failed bridge by calling back with dtmf=ERROR; the
    # driver is then rung back by a connect campaign instead.
    fallback = call(client, "/ivr/driver", callId="c3", caller=DRIVER, dtmf="ERROR")
    assert fallback["files"][0]["text"] == tts.AUDIO_TEXTS["driver_won_callback"]

    with db.session_scope() as session:
        connects = session.scalars(
            db.select(db.FlashCall).where(db.FlashCall.kind == "connect")
        ).all()
        assert [row.phone for row in connects] == [DRIVER]


def test_a_winner_who_left_the_line_is_rung_and_connected_on_callback(client):
    """The winner is rung by a connect campaign that bridges them to the
    passenger; a callback within the award window re-triggers the ring."""
    with db.session_scope() as session:
        driver = drivers.register(session, DRIVER, status="active")
        order = db.Order(call_id="x", phone=PASSENGER, origin="ירושלים", destination="בני ברק")
        session.add(order)
        session.flush()
        result = dispatch.open_tender(session, order, area="ירושלים")
        tender = session.get(db.Tender, result["tender_id"])
        dispatch.place_bid(session, tender, driver)
        tender.closes_at = tender.opened_at
        dispatch.close_tender(session, tender)

        connects = session.scalars(
            db.select(db.FlashCall).where(db.FlashCall.kind == "connect")
        ).all()
        assert [row.phone for row in connects] == [driver.phone]

    won = call(client, "/ivr/driver", callId="c9", caller=DRIVER)
    assert won["files"][0]["text"] == tts.AUDIO_TEXTS["driver_won_callback"]


def test_ringing_in_confirms_a_pending_referral(client):
    with db.session_scope() as session:
        referrals.assign(session, "0523333333", PASSENGER)

    call(client, "/ivr/passenger", callId="c4", caller=PASSENGER)

    with db.session_scope() as session:
        row = session.scalars(db.select(db.Referral)).first()
        assert row.status == referrals.STATUS_CONFIRMED


def test_redeeming_without_points_says_so(client):
    with db.session_scope() as session:
        session.add(
            db.Order(call_id="x", phone=PASSENGER, origin="a", destination="b", price=90.0)
        )
    body = call(client, "/ivr/passenger", callId="c5", caller=PASSENGER)
    assert body.get("name") == "dtmf"
    assert body.get("enabledKeys") == "1,2,3,4"
    body = call(client, "/ivr/passenger", callId="c5", caller=PASSENGER, dtmf="2")
    assert body["files"][0]["text"] == tts.AUDIO_TEXTS["passenger_redeem_no"]


def test_pbx_sends_dtmf_with_empty_param_name(client):
    """Some PBX builds append the captured digit as an unnamed query value."""
    with db.session_scope() as session:
        session.add(
            db.Order(call_id="x", phone=PASSENGER, origin="a", destination="b", price=90.0)
        )
    call(client, "/ivr/passenger", callId="c10", caller=PASSENGER)
    response = client.get(
        "/ivr/passenger",
        params={"callId": "c10", "caller": PASSENGER, "": "2"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "simpleMessage"
    assert body["files"][0]["text"] == tts.AUDIO_TEXTS["passenger_redeem_no"]


def test_redeeming_with_points_zeroes_the_fare(client):
    with db.session_scope() as session:
        session.add(
            db.Order(call_id="x", phone=PASSENGER, origin="a", destination="b", price=90.0)
        )
        loyalty.grant(session, phone=PASSENGER, delta=500, reason="manual")
    call(client, "/ivr/passenger", callId="c6", caller=PASSENGER)
    body = call(client, "/ivr/passenger", callId="c6", caller=PASSENGER, dtmf="2")
    assert body["files"][0]["text"] == tts.AUDIO_TEXTS["passenger_redeem_ok"]
    with db.session_scope() as session:
        order = session.scalars(db.select(db.Order)).first()
        assert (order.price, order.points_spent) == (0.0, 500)


def test_the_rating_call_records_one_score(client):
    with db.session_scope() as session:
        driver = drivers.register(session, DRIVER, status="active")
        order = db.Order(
            call_id="x",
            phone=PASSENGER,
            origin="a",
            destination="b",
            price=50.0,
            status="done",
            driver_id=driver.id,
        )
        session.add(order)
        session.flush()
        ratings.schedule_for_order(session, order)
        rating_id = session.scalars(db.select(db.RatingRequest)).first().id

    call(client, "/ivr/rating", callId="c7", caller=PASSENGER, rating=rating_id)
    body = call(client, "/ivr/rating", callId="c7", caller=PASSENGER, rating=rating_id, dtmf="5")
    assert body["files"][0]["text"] == tts.AUDIO_TEXTS["rating_thanks"]

    with db.session_scope() as session:
        request = session.get(db.RatingRequest, rating_id)
        assert (request.score, request.status) == (5, ratings.STATUS_DONE)
        assert drivers.get_by_phone(session, DRIVER).rating_count == 1


def test_a_low_rating_asks_for_a_voice_feedback(client):
    with db.session_scope() as session:
        driver = drivers.register(session, DRIVER, status="active")
        order = db.Order(
            call_id="x",
            phone=PASSENGER,
            origin="a",
            destination="b",
            price=50.0,
            status="done",
            driver_id=driver.id,
        )
        session.add(order)
        session.flush()
        ratings.schedule_for_order(session, order)
        rating_id = session.scalars(db.select(db.RatingRequest)).first().id

    call(client, "/ivr/rating", callId="c8", caller=PASSENGER, rating=rating_id)
    body = call(client, "/ivr/rating", callId="c8", caller=PASSENGER, rating=rating_id, dtmf="2")
    assert body["type"] == "record"
    assert body["name"] == "feedback"
    assert "תוכל לשתף" in body["files"][0]["text"]

    body = call(
        client,
        "/ivr/rating",
        callId="c8",
        caller=PASSENGER,
        rating=rating_id,
        PATH_feedback="https://example.com/rec.mp3",
    )
    assert body["files"][0]["text"] == tts.AUDIO_TEXTS["rating_thanks"]
    with db.session_scope() as session:
        request = session.get(db.RatingRequest, rating_id)
        assert request.score == 2
        assert request.feedback_recording_url == "https://example.com/rec.mp3"


def test_module_payloads_use_tlivr_field_names(client):
    body = call(client, "/ivr/driver", callId="c9", caller=DRIVER)
    assert body["type"] == "simpleMenu"
    assert body["name"] == "dtmf"
    assert "enabledKeys" in body
    assert "times" in body
    assert "tries" not in body
    assert "min_digits" not in body
    assert "max_digits" not in body

    call(client, "/ivr/driver", callId="c9", caller=DRIVER, dtmf="1")
    body = call(client, "/ivr/driver", callId="c9", caller=DRIVER)
    assert body["type"] == "getDTMF"
    assert body["max"] == 4
    assert body["min"] == 4
    assert "max_digits" not in body
    assert "min_digits" not in body


def test_a_broken_step_still_returns_a_module(client):
    # No caller at all: the PBX must still get valid JSON rather than a 500.
    body = call(client, "/ivr/driver", callId="c10")
    assert body["type"] in KNOWN_TYPES
