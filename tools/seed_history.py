"""History data for the last HISTORY_DAYS days (step 5, docs/konzept.md).

Usage: python -m tools.seed_history

- Own image pool (HISTORY_POOL_SEED) in data/history_pool/, separate from the demo parts.
- Per day and line HISTORY_PARTS_PER_DAY parts; exactly round(HISTORY_DEFECT_RATE * n)
  parts per line are defective (ground truth NOK), spread over the days by seed.
- Every part is inspected by the mock (natural errors, no required scenarios) and
  stored. NOK and MANUAL parts get a plausible review: the final verdict follows the
  ground truth, and deliberate mock errors are corrected (review_change).
- Timestamps relative to today; today itself is left to the live station.
- A rerun replaces the history data (serials and batches with HISTORY_SEED).
Deterministic for a given day.
"""

from __future__ import annotations

import random
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from app import config, db, repository
from app.detector import MockDetector, PartInput, part_from_label, target_verdict
from tools import generate_images as gen

COMMENTS = {
    "FALSE_POSITIVE": "Spiegelung, kein Kratzer",
    "TYPE_CONFUSION": "Fehlertyp korrigiert",
    "MISSED": "Übersehenen Fehler nachgetragen",
}


def pool_dir(data_dir: Path | None = None) -> Path:
    return (data_dir or config.DATA_DIR) / config.HISTORY_POOL_DIRNAME


def build_pool(render: bool = True, data_dir: Path | None = None) -> list[dict]:
    """Pool labels with image path; images are rendered once (deterministic)."""
    directory = pool_dir(data_dir)
    if render:
        directory.mkdir(parents=True, exist_ok=True)
    pool = []
    for plan, shapes, label in gen.build_parts(config.HISTORY_POOL_SIZE, config.HISTORY_POOL_SEED):
        target = directory / label["image"]
        if render and not target.exists():
            gen.render(shapes, random.Random(f"{config.HISTORY_POOL_SEED}:{plan['index']}:render")).save(target)
        label["image_path"] = f"{config.HISTORY_POOL_DIRNAME}/{label['image']}"
        label["target"] = target_verdict(part_from_label(label))
        pool.append(label)
    return pool


def review_for(conn: sqlite3.Connection, inspection_id: int, ai_verdict: str, target: str, missed) -> dict:
    """Plausible review: final verdict = ground truth; deliberate mock errors corrected."""
    changes, reasons = [], []
    for row in conn.execute(
        "SELECT id, defect_type, injected_error FROM detection WHERE inspection_id = ? ORDER BY id",
        (inspection_id,),
    ):
        if row["injected_error"] == "FALSE_POSITIVE":
            changes.append({"change": "TYPE_CHANGED", "detection_id": row["id"], "new_defect_type": "SPIEGELUNG"})
            reasons.append("FALSE_POSITIVE")
        elif row["injected_error"] == "TYPE_CONFUSION":
            original = config.MOCK_TYPE_CONFUSION[row["defect_type"]]
            changes.append({"change": "TYPE_CHANGED", "detection_id": row["id"], "new_defect_type": original})
            reasons.append("TYPE_CONFUSION")
    for truth in missed:
        changes.append({"change": "MISSED", "detection_id": None, "new_defect_type": truth.defect_type})
        reasons.append("MISSED")
    corrected = bool(changes) or (ai_verdict == "NOK" and target != "NOK")
    comment = "; ".join(dict.fromkeys(COMMENTS[r] for r in reasons)) or None
    return {
        "decision": "CORRECTED" if corrected else "CONFIRMED",
        "final": target,
        "comment": comment,
        "changes": changes,
    }


def seed_history(conn: sqlite3.Connection, today: date | None = None, render: bool = True, data_dir: Path | None = None) -> dict:
    today = today or date.today()
    pool = build_pool(render, data_dir)
    nok_pool = [label for label in pool if label["target"] == "NOK"]
    ok_pool = [label for label in pool if label["target"] == "OK"]
    rng = random.Random(f"{config.HISTORY_SEED}:{today.isoformat()}")
    detector = MockDetector(seed=config.MOCK_SEED)
    line_ids = {row["code"]: row["id"] for row in conn.execute("SELECT id, code FROM production_line")}

    defective: dict[str, set[int]] = {}
    for line, per_day in config.HISTORY_PARTS_PER_DAY.items():
        total = per_day * config.HISTORY_DAYS
        defective[line] = set(rng.sample(range(total), round(config.HISTORY_DEFECT_RATE[line] * total)))

    serial_prefix = config.SERIAL_PREFIX_TEMPLATE.format(seed=config.HISTORY_SEED)
    batch_prefix = config.BATCH_PREFIX_TEMPLATE.format(seed=config.HISTORY_SEED)
    shift_start, shift_end = config.HISTORY_SHIFT_HOURS
    shift = timedelta(hours=shift_end - shift_start)
    counter = 0
    with repository.write_transaction(conn):
        conn.execute("DELETE FROM part WHERE serial LIKE ? || '%'", (serial_prefix,))
        conn.execute("DELETE FROM batch WHERE code LIKE ? || '%'", (batch_prefix,))
        for day_number, days_ago in enumerate(range(config.HISTORY_DAYS, 0, -1), start=1):
            day = today - timedelta(days=days_ago)
            day_start = datetime.combine(day, datetime.min.time()) + timedelta(hours=shift_start)
            for line, per_day in config.HISTORY_PARTS_PER_DAY.items():
                batch_id = conn.execute(
                    "INSERT INTO batch (code, line_id, produced_on) VALUES (?, ?, ?)",
                    (
                        config.BATCH_TEMPLATE.format(seed=config.HISTORY_SEED, line=line, number=day_number),
                        line_ids[line],
                        day.isoformat(),
                    ),
                ).lastrowid
                for k in range(per_day):
                    counter += 1
                    line_index = (day_number - 1) * per_day + k
                    label = rng.choice(nok_pool if line_index in defective[line] else ok_pool)
                    inspected = day_start + shift * ((k + rng.random()) / per_day)
                    inspected_at = inspected.isoformat(timespec="seconds")
                    serial = config.HISTORY_SERIAL_TEMPLATE.format(seed=config.HISTORY_SEED, index=counter)
                    part_id = conn.execute(
                        "INSERT INTO part (serial, batch_id, image_path, created_at) VALUES (?, ?, ?, ?)",
                        (serial, batch_id, label["image_path"], inspected_at),
                    ).lastrowid
                    conn.executemany(
                        "INSERT INTO gt_defect (part_id, defect_type, x, y, w, h, length_px) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        [(part_id, d["defect_type"], d["x"], d["y"], d["w"], d["h"], d["length_px"]) for d in label["defects"]],
                    )
                    part = PartInput(serial, part_from_label(label).ground_truth, part_id)
                    outcome = detector.simulate(part)
                    inspection_id = repository.store_inspection(
                        conn, part_id, outcome.detections, detector.version, inspected_at
                    )
                    ai_verdict = conn.execute("SELECT ai_verdict FROM inspection WHERE id = ?", (inspection_id,)).fetchone()[0]
                    if ai_verdict == "OK":
                        continue
                    review = review_for(conn, inspection_id, ai_verdict, label["target"], outcome.missed)
                    delay = timedelta(minutes=rng.uniform(*config.HISTORY_REVIEW_DELAY_MIN))
                    repository._save_review(
                        conn,
                        inspection_id,
                        review["decision"],
                        review["final"],
                        review["comment"],
                        review["changes"],
                        (inspected + delay).isoformat(timespec="seconds"),
                    )
    return history_stats(conn)


def history_last_day(conn: sqlite3.Connection) -> date | None:
    """Last day with history inspections, None without history data."""
    prefix = config.SERIAL_PREFIX_TEMPLATE.format(seed=config.HISTORY_SEED)
    last = conn.execute(
        "SELECT MAX(substr(i.inspected_at, 1, 10)) FROM inspection i JOIN part p ON p.id = i.part_id "
        "WHERE p.serial LIKE ? || '%'",
        (prefix,),
    ).fetchone()[0]
    return date.fromisoformat(last) if last else None


def history_is_current(conn: sqlite3.Connection, today: date | None = None) -> bool:
    """True if the history reaches exactly up to yesterday."""
    return history_last_day(conn) == (today or date.today()) - timedelta(days=1)


def history_stats(conn: sqlite3.Connection) -> dict:
    """Inspections, flagged, final NOK and corrected per line for the history parts."""
    repository.register_functions(conn)
    prefix = config.SERIAL_PREFIX_TEMPLATE.format(seed=config.HISTORY_SEED)
    rows = conn.execute(
        f"SELECT l.code AS line, COUNT(*) AS inspections, "
        f" SUM(i.ai_verdict <> 'OK') AS flagged, "
        f" SUM({repository.FINAL_SQL} = 'NOK') AS final_nok, "
        f" SUM(r.decision = 'CORRECTED') AS corrected, "
        f" COUNT(DISTINCT date(i.inspected_at)) AS days, "
        f" MIN(date(i.inspected_at)) AS first_day, MAX(date(i.inspected_at)) AS last_day "
        f"{repository.BASE_FROM} WHERE p.serial LIKE ? || '%' GROUP BY l.code ORDER BY l.code",
        (prefix,),
    ).fetchall()
    return {row["line"]: dict(row) for row in rows}


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    started = time.perf_counter()
    conn = db.connect()
    try:
        db.init_db(conn)
        stats = seed_history(conn)
    finally:
        conn.close()
    total = sum(s["inspections"] for s in stats.values())
    print(f"Historie erzeugt in {time.perf_counter() - started:.1f} s: {total} Prüfungen, "
          f"{config.HISTORY_DAYS} Tage, {len(stats)} Linien")
    print(f"{'Linie':<6} {'Prüfungen':>9} {'KI auffällig':>12} {'Endurteil NOK':>13} {'Quote':>7} {'Soll':>6} {'korrigiert':>10}")
    for line, s in stats.items():
        rate = s["final_nok"] / s["inspections"]
        quote = f"{rate * 100:.1f} %".replace(".", ",")
        target = f"{config.HISTORY_DEFECT_RATE[line] * 100:.1f} %".replace(".", ",")
        print(
            f"{line:<6} {s['inspections']:>9} {s['flagged']:>12} {s['final_nok']:>13} "
            f"{quote:>7} {target:>6} {s['corrected']:>10}"
        )
    first = min(s["first_day"] for s in stats.values())
    last = max(s["last_day"] for s in stats.values())
    print(f"Zeitraum: {first} bis {last}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
