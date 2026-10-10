"""SQL access for the inspection station and its history (steps 4 and 5, docs/konzept.md).

Rules:
- inspection and detection rows are written once and never changed.
- Status and final verdict are determined only by inspection_status() and
  final_verdict() below. They are registered as SQL functions, so queries use the
  same code; no query reads the column inspection.status.
- injected_error and gt_defect never leave this module.
"""

from __future__ import annotations

import functools
import random
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import date, datetime, timedelta

from app import config, db, rules
from app.detector import Detection, GroundTruth, MockDetector, PartInput


class StationError(Exception):
    status_code = 400


class NotFoundError(StationError):
    status_code = 404


class ConflictError(StationError):
    status_code = 409


# --- Status and final verdict: the only place where they are determined -----------

def inspection_status(ai_verdict: str, has_review: bool) -> str:
    """AUTO_DONE for automatic OK, REVIEWED once a review exists, else PENDING_REVIEW."""
    if ai_verdict == "OK":
        return "AUTO_DONE"
    return "REVIEWED" if has_review else "PENDING_REVIEW"


def final_verdict(ai_verdict: str, review_verdict: str | None) -> str | None:
    """Review verdict if present, else the AI verdict; None while a MANUAL part is open."""
    if review_verdict is not None:
        return review_verdict
    return ai_verdict if ai_verdict in ("OK", "NOK") else None


# SQL expressions using the functions above (registered per connection).
STATUS_SQL = "inspection_status(i.ai_verdict, r.id IS NOT NULL)"
FINAL_SQL = "final_verdict(i.ai_verdict, r.final_verdict)"


def register_functions(conn: sqlite3.Connection) -> sqlite3.Connection:
    conn.create_function("inspection_status", 2, lambda v, has: inspection_status(v, bool(has)), deterministic=True)
    conn.create_function("final_verdict", 2, final_verdict, deterministic=True)
    return conn


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    conn = register_functions(db.connect())
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def write_transaction(conn: sqlite3.Connection) -> Iterator[None]:
    """Exclusive write lock, so two requests can never inspect the same part."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def prefix_for(seed: int) -> str:
    return config.SERIAL_PREFIX_TEMPLATE.format(seed=seed)


# --- Parts by seed ------------------------------------------------------------------

def parts_with_prefix(conn: sqlite3.Connection, prefix: str) -> list[PartInput]:
    """Parts whose serial starts with prefix, with ground truth, ordered by id."""
    rows = conn.execute(
        "SELECT p.id AS part_id, p.serial, g.defect_type, g.x, g.y, g.w, g.h, g.length_px "
        "FROM part p LEFT JOIN gt_defect g ON g.part_id = p.id "
        "WHERE p.serial LIKE ? || '%' ORDER BY p.id, g.id",
        (prefix,),
    ).fetchall()
    parts: dict[int, dict] = {}
    for row in rows:
        entry = parts.setdefault(row["part_id"], {"serial": row["serial"], "truths": []})
        if row["defect_type"] is not None:
            entry["truths"].append(
                GroundTruth(row["defect_type"], row["x"], row["y"], row["w"], row["h"], row["length_px"])
            )
    return [PartInput(e["serial"], tuple(e["truths"]), part_id) for part_id, e in parts.items()]


def demo_prefix() -> str:
    return prefix_for(config.DEMO_SEED)


def demo_parts(conn: sqlite3.Connection) -> list[PartInput]:
    return parts_with_prefix(conn, demo_prefix())


def filler_parts(conn: sqlite3.Connection) -> list[PartInput]:
    return parts_with_prefix(conn, prefix_for(config.FILLER_SEED))


def demo_detector(demo: Sequence[PartInput]) -> MockDetector:
    """Mock whose required scenarios are placed among the demo parts."""
    return _cached_detector(tuple(demo))


@functools.lru_cache(maxsize=8)
def _cached_detector(demo: tuple[PartInput, ...]) -> MockDetector:
    # The infeed runs every STATION_INFEED_INTERVAL_S; the choice of required parts
    # only depends on the demo parts, so it is computed once per set of demo parts.
    return MockDetector(parts=list(demo))


# --- Station stream -----------------------------------------------------------------

def demo_slots(total: int, demo_count: int) -> list[int]:
    """Stream positions of the demo parts, spread evenly over the stream."""
    return [int((k + 0.5) * total / demo_count) for k in range(demo_count)]


def build_stream(demo: Sequence[PartInput], fillers: Sequence[PartInput], required: Sequence[str]) -> list[PartInput]:
    """Deterministic order: demo parts evenly between fillers. The required mock parts
    take random demo slots inside the pre-inspected section, the other demo parts follow
    in a seeded random order."""
    total = len(demo) + len(fillers)
    if not demo:
        return list(fillers)
    slots = demo_slots(total, len(demo))
    early = [k for k, pos in enumerate(slots) if pos < config.DEMO_PREINSPECTED]
    rng = random.Random(f"{config.DEMO_SEED}:stream")

    by_serial = {p.serial: p for p in demo}
    required_parts = [by_serial[s] for s in sorted(required) if s in by_serial]
    others = [p for p in demo if p.serial not in required]
    rng.shuffle(others)
    demo_order: list[PartInput | None] = [None] * len(demo)
    if len(early) >= len(required_parts):
        for k, part in zip(sorted(rng.sample(early, len(required_parts))), required_parts):
            demo_order[k] = part
    else:  # stream too short for the pre-inspected section; keep required parts first
        for k, part in enumerate(required_parts):
            demo_order[k] = part
    rest = iter(others)
    demo_order = [p if p is not None else next(rest) for p in demo_order]

    stream: list[PartInput] = []
    filler_iter = iter(fillers)
    demo_at = dict(zip(slots, demo_order))
    for position in range(total):
        stream.append(demo_at[position] if position in demo_at else next(filler_iter))
    return stream


def station_stream(conn: sqlite3.Connection) -> tuple[list[PartInput], MockDetector]:
    demo = demo_parts(conn)
    detector = demo_detector(demo)
    stream = build_stream(demo, filler_parts(conn), list(detector.required))
    return stream[: config.STATION_MAX_PARTS], detector


def stream_part_ids(conn: sqlite3.Connection) -> list[int]:
    return [p.part_id for p in station_stream(conn)[0]]


# --- Storing inspections ------------------------------------------------------------

def store_inspection(
    conn: sqlite3.Connection,
    part_id: int,
    detections: Sequence[Detection],
    detector_version: str,
    inspected_at: str,
) -> int:
    """Insert one inspection with its detections; verdict and severities from rules.py."""
    verdict, reason = rules.evaluate(detections)
    severities = rules.severities(detections)
    # The status column is filled once for completeness; it is never read.
    stored_status = "AUTO_DONE" if verdict == "OK" else "PENDING_REVIEW"
    cursor = conn.execute(
        "INSERT INTO inspection (part_id, inspected_at, detector_version, ai_verdict, ai_reason, status) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (part_id, inspected_at, detector_version, verdict, reason, stored_status),
    )
    inspection_id = cursor.lastrowid
    conn.executemany(
        "INSERT INTO detection (inspection_id, defect_type, x, y, w, h, length_px, confidence, severity, injected_error) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (inspection_id, d.defect_type, d.x, d.y, d.w, d.h, d.length_px, d.confidence, sev, d.injected_error)
            for d, sev in zip(detections, severities)
        ],
    )
    return inspection_id


def _is_inspected(conn: sqlite3.Connection, part_id: int) -> bool:
    return conn.execute("SELECT 1 FROM inspection WHERE part_id = ?", (part_id,)).fetchone() is not None


def _inspect(conn: sqlite3.Connection, part: PartInput, detector: MockDetector, inspected_at: str | None = None) -> int:
    """Run the mock on one part and store the result. Caller holds the lock."""
    if _is_inspected(conn, part.part_id):
        raise ConflictError("Dieses Teil wurde bereits geprüft. Doppelprüfungen sind nicht möglich.")
    return store_inspection(conn, part.part_id, detector.analyze(part), detector.version, inspected_at or now())


def infeed_part(conn: sqlite3.Connection, part_id: int) -> int:
    with write_transaction(conn):
        row = conn.execute("SELECT serial FROM part WHERE id = ?", (part_id,)).fetchone()
        if row is None:
            raise NotFoundError("Teil nicht gefunden.")
        part = next(p for p in parts_with_prefix(conn, row["serial"]) if p.part_id == part_id)
        return _inspect(conn, part, demo_detector(demo_parts(conn)))


def infeed_next(conn: sqlite3.Connection) -> int | None:
    """Inspect the next part of the station stream; None after STATION_MAX_PARTS."""
    with write_transaction(conn):
        stream, detector = station_stream(conn)
        inspected = {row["part_id"] for row in conn.execute("SELECT part_id FROM inspection")}
        part = next((p for p in stream if p.part_id not in inspected), None)
        return None if part is None else _inspect(conn, part, detector)


# --- Demo reset ---------------------------------------------------------------------

def _create_fillers(conn: sqlite3.Connection, count: int, produced_on: str) -> None:
    """Defect-free filler parts: own serial, line and batch; image reused from the
    defect-free demo images."""
    clean_images = [
        row["image_path"]
        for row in conn.execute(
            "SELECT p.image_path FROM part p WHERE p.serial LIKE ? || '%' "
            "AND NOT EXISTS (SELECT 1 FROM gt_defect g WHERE g.part_id = p.id) ORDER BY p.id",
            (demo_prefix(),),
        )
    ]
    if not clean_images or count <= 0:
        return
    rng = random.Random(f"{config.FILLER_SEED}:fillers")
    lines = list(config.GENERATOR_LINE_SHARES)
    shares = list(config.GENERATOR_LINE_SHARES.values())
    line_ids = {row["code"]: row["id"] for row in conn.execute("SELECT id, code FROM production_line")}
    per_line: dict[str, int] = {}
    batch_ids: dict[str, int] = {}
    created_at = now()
    for index in range(1, count + 1):
        line = rng.choices(lines, weights=shares)[0]
        position = per_line.get(line, 0)
        per_line[line] = position + 1
        number = position // config.GENERATOR_PARTS_PER_BATCH + 1
        batch = config.BATCH_TEMPLATE.format(seed=config.FILLER_SEED, line=line, number=number)
        if batch not in batch_ids:
            batch_ids[batch] = conn.execute(
                "INSERT INTO batch (code, line_id, produced_on) VALUES (?, ?, ?)",
                (batch, line_ids[line], produced_on),
            ).lastrowid
        conn.execute(
            "INSERT INTO part (serial, batch_id, image_path, created_at) VALUES (?, ?, ?, ?)",
            (
                config.SERIAL_TEMPLATE.format(seed=config.FILLER_SEED, index=index),
                batch_ids[batch],
                clean_images[(index - 1) % len(clean_images)],
                created_at,
            ),
        )


def reset_demo(conn: sqlite3.Connection, now_dt: datetime | None = None) -> dict:
    """Reset the station stream for the next participant.

    Deletes inspections and reviews of demo and filler parts only (history stays),
    recreates the filler parts, then the mock inspects the first DEMO_PREINSPECTED
    stream parts with timestamps spread over the last DEMO_PREINSPECTED_SPAN_MIN minutes.
    """
    now_dt = (now_dt or datetime.now()).replace(microsecond=0)
    filler_prefix = prefix_for(config.FILLER_SEED)
    batch_prefix = config.BATCH_PREFIX_TEMPLATE.format(seed=config.FILLER_SEED)
    with write_transaction(conn):
        deleted = conn.execute(
            "DELETE FROM inspection WHERE part_id IN "
            "(SELECT id FROM part WHERE serial LIKE ? || '%' OR serial LIKE ? || '%')",
            (demo_prefix(), filler_prefix),
        ).rowcount
        conn.execute("DELETE FROM part WHERE serial LIKE ? || '%'", (filler_prefix,))
        conn.execute("DELETE FROM batch WHERE code LIKE ? || '%'", (batch_prefix,))
        # The demo parts are produced "today" like the fillers (coverage on the dashboard).
        conn.execute(
            "UPDATE batch SET produced_on = ? WHERE code LIKE ? || '%'",
            (now_dt.date().isoformat(), config.BATCH_PREFIX_TEMPLATE.format(seed=config.DEMO_SEED)),
        )

        demo = demo_parts(conn)
        _create_fillers(conn, config.STATION_MAX_PARTS - len(demo), now_dt.date().isoformat())
        stream, detector = station_stream(conn)
        pre = stream[: config.DEMO_PREINSPECTED]
        span = timedelta(minutes=config.DEMO_PREINSPECTED_SPAN_MIN)
        step = span / max(len(pre), 1)
        for index, part in enumerate(pre):
            inspected_at = (now_dt - span + step * (index + 1)).isoformat(timespec="seconds")
            store_inspection(conn, part.part_id, detector.analyze(part), detector.version, inspected_at)

    position = {p.serial: i + 1 for i, p in enumerate(stream)}
    return {
        "deleted": deleted,
        "stream": len(stream),
        "demo_parts": len(demo),
        "fillers": len(stream) - sum(1 for p in stream if p.serial.startswith(demo_prefix())),
        "preinspected": len(pre),
        "required_positions": {s: position[s] for s in sorted(detector.required)},
        "demo_positions": [i + 1 for i, p in enumerate(stream) if p.serial.startswith(demo_prefix())],
    }


# --- Reading ------------------------------------------------------------------------

BASE_FROM = (
    "FROM inspection i JOIN part p ON p.id = i.part_id "
    "JOIN batch b ON b.id = p.batch_id JOIN production_line l ON l.id = b.line_id "
    "LEFT JOIN review r ON r.inspection_id = i.id "
)


def inspection_summary(conn: sqlite3.Connection, inspection_id: int) -> dict:
    register_functions(conn)
    row = conn.execute(
        f"SELECT i.id, p.serial, i.ai_verdict, {STATUS_SQL} AS status, {FINAL_SQL} AS final_verdict "
        f"{BASE_FROM} WHERE i.id = ?",
        (inspection_id,),
    ).fetchone()
    if row is None:
        raise NotFoundError("Prüfung nicht gefunden.")
    return dict(row)


def station_state(conn: sqlite3.Connection, today: date | None = None) -> dict:
    """Header counters (today), queue of open parts and whether the stream is done."""
    register_functions(conn)
    start = datetime.combine(today or date.today(), datetime.min.time()).isoformat(timespec="seconds")
    counts = dict(
        conn.execute(
            f"SELECT COUNT(*) AS today, COALESCE(SUM({STATUS_SQL} = 'AUTO_DONE'), 0) AS today_auto_ok "
            f"{BASE_FROM} WHERE i.inspected_at >= ?",
            (start,),
        ).fetchone()
    )
    queue = [
        dict(row)
        for row in conn.execute(
            f"SELECT i.id, p.serial, i.ai_verdict, i.ai_reason {BASE_FROM} "
            f"WHERE {STATUS_SQL} = 'PENDING_REVIEW' ORDER BY i.inspected_at, i.id"
        )
    ]
    counts["open"] = len(queue)
    stream_ids = stream_part_ids(conn)
    inspected = {row["part_id"] for row in conn.execute("SELECT part_id FROM inspection")}
    remaining = sum(1 for part_id in stream_ids if part_id not in inspected)
    return {
        "counts": counts,
        "all_done": bool(stream_ids) and remaining == 0,
        "queue": queue,
    }


def _detection_view(d: sqlite3.Row) -> dict:
    return {
        "id": d["id"],
        "defect_type": d["defect_type"],
        "type_name": config.DEFECT_TYPES[d["defect_type"]],
        "severity": d["severity"],
        "severity_name": config.SEVERITIES[d["severity"]],
        "x": d["x"], "y": d["y"], "w": d["w"], "h": d["h"],
        "length_mm": None if d["length_px"] is None else round(d["length_px"] * config.MM_PER_PX, 1),
        "confidence": d["confidence"],
        "certain": d["confidence"] >= config.CONFIDENCE_MIN,
        "is_defect": d["defect_type"] not in config.NON_DEFECT_TYPES,
    }


def inspection_detail(conn: sqlite3.Connection, inspection_id: int) -> dict:
    register_functions(conn)
    row = conn.execute(
        f"SELECT i.id, i.inspected_at, i.ai_verdict, i.ai_reason, {STATUS_SQL} AS status, "
        f"{FINAL_SQL} AS final_verdict, p.serial, b.code AS batch, l.code AS line, l.name AS line_name "
        f"{BASE_FROM} WHERE i.id = ?",
        (inspection_id,),
    ).fetchone()
    if row is None:
        raise NotFoundError("Prüfung nicht gefunden.")
    detections = conn.execute(
        "SELECT id, defect_type, x, y, w, h, length_px, confidence, severity "
        "FROM detection WHERE inspection_id = ? ORDER BY id",
        (inspection_id,),
    ).fetchall()
    detail = dict(row)
    detail["detections"] = [_detection_view(d) for d in detections]
    detail["review"] = review_of(conn, inspection_id)
    return detail


def review_of(conn: sqlite3.Connection, inspection_id: int) -> dict | None:
    row = conn.execute(
        "SELECT id, decision, final_verdict, comment, reviewed_at FROM review WHERE inspection_id = ?",
        (inspection_id,),
    ).fetchone()
    if row is None:
        return None
    review = dict(row)
    changes = conn.execute(
        "SELECT detection_id, change, new_defect_type FROM review_change WHERE review_id = ? ORDER BY id",
        (row["id"],),
    ).fetchall()
    review["changes"] = [dict(c) for c in changes]
    return review


def image_path(conn: sqlite3.Connection, inspection_id: int):
    row = conn.execute(
        "SELECT p.image_path FROM inspection i JOIN part p ON p.id = i.part_id WHERE i.id = ?",
        (inspection_id,),
    ).fetchone()
    if row is None:
        raise NotFoundError("Prüfung nicht gefunden.")
    path = config.DATA_DIR / row["image_path"]
    if not path.is_file():
        raise NotFoundError("Bild nicht gefunden.")
    return path


# --- History ------------------------------------------------------------------------

HISTORY_SCOPES = {"flagged": "Nur auffällige", "all": "Alle Prüfungen"}
HISTORY_STATUSES = {
    "AUTO_DONE": "Automatisch freigegeben",
    "PENDING_REVIEW": "Zu prüfen",
    "REVIEWED": "Bearbeitet",
}
HISTORY_FINALS = {"OK": "OK", "NOK": "NOK", "OPEN": "offen"}


def history(
    conn: sqlite3.Connection,
    scope: str = "flagged",
    status: str | None = None,
    final: str | None = None,
    corrected: bool = False,
    page: int = 1,
) -> dict:
    """Filtered, paged list of inspections, newest first."""
    register_functions(conn)
    where, params = [], []
    if scope != "all":
        where.append(f"{STATUS_SQL} <> 'AUTO_DONE'")
    if status in HISTORY_STATUSES:
        where.append(f"{STATUS_SQL} = ?")
        params.append(status)
    if final == "OPEN":
        where.append(f"{FINAL_SQL} IS NULL")
    elif final in ("OK", "NOK"):
        where.append(f"{FINAL_SQL} = ?")
        params.append(final)
    if corrected:
        where.append("r.decision = 'CORRECTED'")
    clause = ("WHERE " + " AND ".join(where)) if where else ""

    total = conn.execute(f"SELECT COUNT(*) {BASE_FROM} {clause}", params).fetchone()[0]
    pages = max(1, -(-total // config.HISTORY_PAGE_SIZE))
    page = min(max(page, 1), pages)
    rows = conn.execute(
        f"SELECT i.id, i.inspected_at, p.serial, l.code AS line, b.code AS batch, i.ai_verdict, "
        f"{FINAL_SQL} AS final_verdict, {STATUS_SQL} AS status, r.decision "
        f"{BASE_FROM} {clause} ORDER BY i.inspected_at DESC, i.id DESC LIMIT ? OFFSET ?",
        [*params, config.HISTORY_PAGE_SIZE, (page - 1) * config.HISTORY_PAGE_SIZE],
    ).fetchall()
    return {"rows": [dict(r) for r in rows], "total": total, "page": page, "pages": pages}


# --- Dashboard ----------------------------------------------------------------------
# Closed = AUTO_DONE or REVIEWED (status function). Open inspections never enter a rate.

CLOSED_SQL = f"{STATUS_SQL} IN ('AUTO_DONE', 'REVIEWED')"


def resolve_period(period: str, date_from: str | None, date_to: str | None, today: date) -> tuple[str, date, date]:
    """(period key, first day, last day). Unknown or invalid input falls back to the default."""
    if period == "custom":
        try:
            start, end = date.fromisoformat(date_from or ""), date.fromisoformat(date_to or "")
        except ValueError:
            period = config.DASHBOARD_DEFAULT_PERIOD
        else:
            return "custom", min(start, end), max(start, end)
    if period not in config.DASHBOARD_PERIODS or config.DASHBOARD_PERIODS[period][1] is None:
        period = config.DASHBOARD_DEFAULT_PERIOD
    days = config.DASHBOARD_PERIODS[period][1]
    return period, today - timedelta(days=days - 1), today


def _scope(start: date, end: date, line: str | None) -> tuple[str, list]:
    """WHERE clause for inspections in [start, end] and optionally one line."""
    clause = "date(i.inspected_at) BETWEEN ? AND ?"
    params: list = [start.isoformat(), end.isoformat()]
    if line:
        clause += " AND l.code = ?"
        params.append(line)
    return clause, params


def _rate(part: int, whole: int) -> float | None:
    return part / whole if whole else None


def _stream_filter() -> tuple[str, list]:
    """Parts of the station stream (demo and filler parts)."""
    return "(p.serial LIKE ? || '%' OR p.serial LIKE ? || '%')", [demo_prefix(), prefix_for(config.FILLER_SEED)]


# Defect rate: final verdict NOK / inspections with a final verdict. For open inspections
# final_verdict() is the AI verdict (NOK); open MANUAL cases have none and are left out.
# The value is provisional while open inspections lie in the scope.
RATED_SQL = f"{FINAL_SQL} IS NOT NULL"
NOK_SQL = f"{FINAL_SQL} = 'NOK'"
OPEN_SQL = f"{STATUS_SQL} = 'PENDING_REVIEW'"


def dashboard_kpis(conn: sqlite3.Connection, start: date, end: date, line: str | None = None) -> dict:
    register_functions(conn)
    clause, params = _scope(start, end, line)
    row = dict(
        conn.execute(
            "SELECT COUNT(*) AS inspected, "
            f" COALESCE(SUM({CLOSED_SQL}), 0) AS closed, "
            f" COALESCE(SUM({RATED_SQL}), 0) AS rated, "
            f" COALESCE(SUM({NOK_SQL}), 0) AS nok, "
            f" COALESCE(SUM({OPEN_SQL}), 0) AS open, "
            f" COALESCE(SUM({OPEN_SQL} AND i.ai_verdict = 'NOK'), 0) AS open_nok, "
            f" COALESCE(SUM({OPEN_SQL} AND i.ai_verdict = 'MANUAL'), 0) AS open_manual, "
            f" COALESCE(SUM(i.ai_verdict = 'NOK' AND {STATUS_SQL} = 'REVIEWED'), 0) AS ai_nok_reviewed, "
            f" COALESCE(SUM(i.ai_verdict = 'NOK' AND {STATUS_SQL} = 'REVIEWED' AND {FINAL_SQL} = 'OK'), 0) AS ai_nok_corrected "
            f"{BASE_FROM} WHERE {clause}",
            params,
        ).fetchone()
    )
    # Produced = parts of the period that entered the line. Stream parts count only once
    # they have run into the station; parts created by the reset but not yet arrived do not.
    stream, stream_params = _stream_filter()
    line_clause = " AND l.code = ?" if line else ""
    produced = dict(
        conn.execute(
            "SELECT COUNT(*) AS produced, COALESCE(SUM(inspected), 0) AS produced_inspected FROM ("
            " SELECT EXISTS (SELECT 1 FROM inspection i WHERE i.part_id = p.id) AS inspected, "
            f" {stream} AS in_stream "
            " FROM part p JOIN batch b ON b.id = p.batch_id JOIN production_line l ON l.id = b.line_id "
            f" WHERE b.produced_on BETWEEN ? AND ?{line_clause}"
            ") WHERE inspected OR NOT in_stream",
            [*stream_params, start.isoformat(), end.isoformat(), *([line] if line else [])],
        ).fetchone()
    )
    row.update(produced)
    row["coverage"] = _rate(row["produced_inspected"], row["produced"])
    row["defect_rate"] = _rate(row["nok"], row["rated"])
    row["provisional"] = row["open"] > 0
    row["correction_rate"] = _rate(row["ai_nok_corrected"], row["ai_nok_reviewed"])
    return row


def dashboard_trend(
    conn: sqlite3.Connection, start: date, end: date, line: str | None = None, by_line: bool = False
) -> dict:
    """Defect rate per day (same definition as the tile); optionally one series per line.
    For every value a flag says whether it is provisional (open inspections that day)."""
    register_functions(conn)
    clause, params = _scope(start, end, line)
    rows = conn.execute(
        f"SELECT date(i.inspected_at) AS day, l.code AS line, COALESCE(SUM({NOK_SQL}), 0) AS nok, "
        f" COALESCE(SUM({RATED_SQL}), 0) AS rated, COALESCE(SUM({OPEN_SQL}), 0) AS open "
        f"{BASE_FROM} WHERE {clause} GROUP BY day, l.code",
        params,
    ).fetchall()
    days = [(start + timedelta(days=n)).isoformat() for n in range((end - start).days + 1)]
    totals: dict[str, list[int]] = {}
    per_line: dict[str, dict[str, list[int]]] = {}
    for r in rows:
        total = totals.setdefault(r["day"], [0, 0, 0])
        for index, key in enumerate(("nok", "rated", "open")):
            total[index] += r[key]
        per_line.setdefault(r["line"], {})[r["day"]] = [r["nok"], r["rated"], r["open"]]

    def series(values: dict[str, list[int]]) -> dict:
        return {
            "rate": [_rate(*values[d][:2]) if d in values else None for d in days],
            "provisional": [bool(values[d][2]) if d in values else False for d in days],
        }

    result = {"days": days, "total": series(totals), "lines": {}}
    if by_line and not line:
        codes = [code for code, _ in config.PRODUCTION_LINES]
        result["lines"] = {code: series(per_line.get(code, {})) for code in codes}
    return result


def dashboard_defect_types(conn: sqlite3.Connection, start: date, end: date, line: str | None = None) -> dict:
    """Parts per defect type after the reviewer's corrections (closed inspections only).

    REJECTED removes a finding, TYPE_CHANGED replaces its type, MISSED adds one.
    Reflections are counted separately as filtered, not as defects.
    """
    register_functions(conn)
    clause, params = _scope(start, end, line)
    closed_ids = f"SELECT i.id {BASE_FROM} WHERE {clause} AND {CLOSED_SQL}"
    findings: dict[int, dict[int | str, str]] = {}
    for d in conn.execute(
        f"SELECT d.id, d.inspection_id, d.defect_type FROM detection d WHERE d.inspection_id IN ({closed_ids})", params
    ):
        findings.setdefault(d["inspection_id"], {})[d["id"]] = d["defect_type"]
    for c in conn.execute(
        "SELECT c.id, r.inspection_id, c.detection_id, c.change, c.new_defect_type "
        f"FROM review_change c JOIN review r ON r.id = c.review_id WHERE r.inspection_id IN ({closed_ids}) ORDER BY c.id",
        params,
    ):
        per_part = findings.setdefault(c["inspection_id"], {})
        if c["change"] == "REJECTED":
            per_part.pop(c["detection_id"], None)
        elif c["change"] == "TYPE_CHANGED":
            per_part[c["detection_id"]] = c["new_defect_type"]
        elif c["change"] == "MISSED":
            per_part[f"missed-{c['id']}"] = c["new_defect_type"]

    counts = {code: 0 for code in config.DEFECT_TYPES}
    for per_part in findings.values():
        for defect_type in set(per_part.values()):
            counts[defect_type] += 1
    defects = sorted(
        ((code, counts[code]) for code in config.DEFECT_TYPES if code not in config.NON_DEFECT_TYPES),
        key=lambda item: -item[1],
    )
    filtered = [(code, counts[code]) for code in config.DEFECT_TYPES if code in config.NON_DEFECT_TYPES]
    return {"defects": defects, "filtered": filtered}


def dashboard(
    conn: sqlite3.Connection,
    period: str = config.DASHBOARD_DEFAULT_PERIOD,
    line: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    by_line: bool = False,
    today: date | None = None,
) -> dict:
    today = today or date.today()
    line = line if line in {code for code, _ in config.PRODUCTION_LINES} else None
    period, start, end = resolve_period(period, date_from, date_to, today)
    return {
        "period": period,
        "start": start,
        "end": end,
        "line": line,
        "by_line": bool(by_line and not line),
        "kpis": dashboard_kpis(conn, start, end, line),
        "trend": dashboard_trend(conn, start, end, line, by_line),
        "defect_types": dashboard_defect_types(conn, start, end, line),
    }


# --- Review -------------------------------------------------------------------------

def _validate_changes(detection_types: dict[int, str], changes: list[dict]) -> None:
    seen: set[int] = set()
    for change in changes:
        kind = change.get("change")
        detection_id = change.get("detection_id")
        new_type = change.get("new_defect_type")
        if kind == "MISSED":
            if detection_id is not None:
                raise StationError("Ein übersehener Fehler gehört zu keiner Erkennung.")
            if new_type not in config.DEFECT_TYPES or new_type in config.NON_DEFECT_TYPES:
                raise StationError("Bitte für den übersehenen Fehler einen Fehlertyp wählen.")
            continue
        if kind not in ("REJECTED", "TYPE_CHANGED"):
            raise StationError("Unbekannte Korrekturart.")
        if detection_id not in detection_types:
            raise StationError("Die Erkennung gehört nicht zu dieser Prüfung.")
        if detection_id in seen:
            raise StationError("Jede Erkennung kann nur einmal korrigiert werden.")
        seen.add(detection_id)
        if kind == "REJECTED" and new_type is not None:
            raise StationError("Bei „falsch erkannt“ wird kein neuer Fehlertyp angegeben.")
        if kind == "TYPE_CHANGED":
            if new_type not in config.DEFECT_TYPES:
                raise StationError("Bitte einen gültigen Fehlertyp wählen.")
            if new_type == detection_types[detection_id]:
                raise StationError("Der neue Fehlertyp ist derselbe wie der erkannte.")


def save_review(
    conn: sqlite3.Connection,
    inspection_id: int,
    decision: str,
    final: str | None,
    comment: str | None,
    changes: list[dict],
    reviewed_at: str | None = None,
) -> int:
    with write_transaction(conn):
        return _save_review(conn, inspection_id, decision, final, comment, changes, reviewed_at)


def _save_review(conn, inspection_id, decision, final, comment, changes, reviewed_at=None) -> int:
    """Validate and insert a review. Caller holds the lock."""
    register_functions(conn)
    inspection = conn.execute(
        f"SELECT i.ai_verdict, {STATUS_SQL} AS status FROM inspection i "
        "LEFT JOIN review r ON r.inspection_id = i.id WHERE i.id = ?",
        (inspection_id,),
    ).fetchone()
    if inspection is None:
        raise NotFoundError("Prüfung nicht gefunden.")
    if inspection["status"] == "AUTO_DONE":
        raise ConflictError("Automatisch als OK eingestufte Teile werden nicht nachgeprüft.")
    if inspection["status"] == "REVIEWED":
        raise ConflictError("Für diese Prüfung liegt bereits eine Bewertung vor.")

    ai_verdict = inspection["ai_verdict"]
    if decision == "CONFIRMED":
        if changes:
            raise StationError("Beim Bestätigen sind keine Korrekturen möglich.")
        if ai_verdict == "NOK":
            if final not in (None, "NOK"):
                raise StationError("Bestätigen übernimmt das KI-Urteil NOK.")
            final = "NOK"
        elif final not in ("OK", "NOK"):
            raise StationError("Bei manueller Prüfung bitte OK oder NOK wählen.")
    elif decision == "CORRECTED":
        if final not in ("OK", "NOK"):
            raise StationError("Bitte ein Endurteil OK oder NOK wählen.")
        detection_types = {
            row["id"]: row["defect_type"]
            for row in conn.execute("SELECT id, defect_type FROM detection WHERE inspection_id = ?", (inspection_id,))
        }
        _validate_changes(detection_types, changes)
        if not changes and final == ai_verdict:
            raise StationError("Keine Änderung gegenüber dem KI-Ergebnis. Bitte „Bestätigen“ verwenden.")
    else:
        raise StationError("Unbekannte Entscheidung.")

    comment = (comment or "").strip() or None
    try:
        cursor = conn.execute(
            "INSERT INTO review (inspection_id, decision, final_verdict, comment, reviewed_at) VALUES (?, ?, ?, ?, ?)",
            (inspection_id, decision, final, comment, reviewed_at or now()),
        )
    except sqlite3.IntegrityError as exc:  # UNIQUE(inspection_id), e.g. two clicks at once
        raise ConflictError("Für diese Prüfung liegt bereits eine Bewertung vor.") from exc
    review_id = cursor.lastrowid
    conn.executemany(
        "INSERT INTO review_change (review_id, detection_id, change, new_defect_type) VALUES (?, ?, ?, ?)",
        [(review_id, c.get("detection_id"), c["change"], c.get("new_defect_type")) for c in changes],
    )
    return review_id
