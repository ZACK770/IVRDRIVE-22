"""Add a driver to the dispatch database from the command line.

Examples:
    python tools/add_driver.py --phone 0521111111 --name "ישראל" --areas "בני ברק,ירושלים" --status active
    BOT_DB_URL=postgresql://... python tools/add_driver.py --phone 0522222222 --areas "ירושלים" --voice-offers
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app import db, drivers


def main() -> None:
    parser = argparse.ArgumentParser(description="Add a driver to the dispatch database")
    parser.add_argument("--phone", required=True, help="Driver phone number")
    parser.add_argument("--name", default=None, help="Driver name")
    parser.add_argument(
        "--status",
        default="active",
        choices=["pending", "active", "paused", "removed"],
        help="Driver status (default: active)",
    )
    parser.add_argument("--areas", default="", help="Comma-separated preferred areas")
    parser.add_argument("--home-area", default=None, help="Home area")
    parser.add_argument("--car-year", type=int, default=None)
    parser.add_argument("--seats", type=int, default=4)
    parser.add_argument("--birth-year", type=int, default=None)
    parser.add_argument("--voice-offers", action="store_true", help="Use paid voice offers")
    parser.add_argument("--no-smartphone", action="store_true")
    args = parser.parse_args()

    db.init_db()
    with db.session_scope() as session:
        driver = drivers.register(
            session,
            args.phone,
            name=args.name,
            home_area=args.home_area,
            status=args.status,
            car_year=args.car_year,
            seats=args.seats,
            birth_year=args.birth_year,
            voice_offers=args.voice_offers,
            smartphone=not args.no_smartphone,
        )
        areas = [a.strip() for a in args.areas.split(",") if a.strip()]
        if areas:
            drivers.set_areas(session, driver, areas)
        print(f"added driver {driver.id}: {driver.phone}, areas={areas or 'any'}")


if __name__ == "__main__":
    main()
