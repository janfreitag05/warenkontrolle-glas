"""Reset the station for the next test participant.

Usage: python -m tools.reset_demo

Deletes inspections and reviews of the demo parts (seed DEMO_SEED) and the filler
parts only; history data stays. Recreates the defect-free filler parts so that the
stream holds STATION_MAX_PARTS parts, then the mock inspects the first
DEMO_PREINSPECTED stream parts at once, with timestamps spread over the last
DEMO_PREINSPECTED_SPAN_MIN minutes. All required mock parts lie in this set.
The rest runs in live at the station. Deterministic: every reset gives the same results.

Day change: if the history data does not reach exactly up to yesterday, it is
recreated with tools.seed_history first.
"""

from __future__ import annotations

import sys
import time
from datetime import date

from app import config, db, repository
from tools import seed_history


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    started = time.perf_counter()
    conn = db.connect()
    try:
        db.init_db(conn)
        if not repository.demo_parts(conn):
            print(f"Keine Demo-Teile (Seed {config.DEMO_SEED}) in der Datenbank.")
            print(f"Zuerst: python -m tools.generate_images --count 60 --seed {config.DEMO_SEED}")
            return 1
        today = date.today()
        if seed_history.history_is_current(conn, today):
            print(f"Vergangenheitsdaten aktuell (bis {seed_history.history_last_day(conn):%d.%m.%Y}).")
        else:
            last = seed_history.history_last_day(conn)
            reason = f"reichen nur bis {last:%d.%m.%Y}" if last else "fehlen"
            seeding = time.perf_counter()
            stats = seed_history.seed_history(conn, today=today)
            total = sum(s["inspections"] for s in stats.values())
            print(f"Vergangenheitsdaten {reason}: neu erzeugt mit seed_history "
                  f"({total} Prüfungen bis {seed_history.history_last_day(conn):%d.%m.%Y}, "
                  f"{time.perf_counter() - seeding:.1f} s).")
        reset_started = time.perf_counter()
        result = repository.reset_demo(conn)
        reset_seconds = time.perf_counter() - reset_started
    finally:
        conn.close()

    required = result["required_positions"]
    outside = [s for s, pos in required.items() if pos > result["preinspected"]]
    print(f"Demo zurückgesetzt in {reset_seconds:.2f} s (gesamt {time.perf_counter() - started:.2f} s)")
    print(f"  gelöschte Prüfungen (Demo- und Füllteile): {result['deleted']}")
    print(f"  Teile im Strom: {result['stream']} ({result['demo_parts']} Demo-Teile, {result['fillers']} Füllteile)")
    print(f"  sofort geprüft: {result['preinspected']}, live einzulaufen: {result['stream'] - result['preinspected']}")
    print("  Pflichtteile (Position im Strom): " + ", ".join(f"{s} ({pos})" for s, pos in required.items()))
    if outside:
        print(f"WARNUNG: Pflichtteile außerhalb der sofort geprüften Teile: {', '.join(outside)}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
