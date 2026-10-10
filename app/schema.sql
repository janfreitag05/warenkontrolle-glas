-- Schema for the prototype (see docs/konzept.md, section 1).

CREATE TABLE IF NOT EXISTS production_line (
    id   INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS batch (
    id          INTEGER PRIMARY KEY,
    code        TEXT NOT NULL UNIQUE,
    line_id     INTEGER NOT NULL REFERENCES production_line(id),
    produced_on TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS part (
    id         INTEGER PRIMARY KEY,
    serial     TEXT NOT NULL UNIQUE,
    batch_id   INTEGER NOT NULL REFERENCES batch(id),
    image_path TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS gt_defect (
    id          INTEGER PRIMARY KEY,
    part_id     INTEGER NOT NULL REFERENCES part(id) ON DELETE CASCADE,
    defect_type TEXT NOT NULL
        CHECK (defect_type IN ('RISS', 'KRATZER', 'DRUCKSTELLE', 'BLAESCHEN', 'SPIEGELUNG')),
    x           INTEGER NOT NULL,
    y           INTEGER NOT NULL,
    w           INTEGER NOT NULL,
    h           INTEGER NOT NULL,
    length_px   REAL
);

CREATE TABLE IF NOT EXISTS inspection (
    id               INTEGER PRIMARY KEY,
    part_id          INTEGER NOT NULL REFERENCES part(id) ON DELETE CASCADE,
    inspected_at     TEXT NOT NULL,
    detector_version TEXT NOT NULL,
    ai_verdict       TEXT NOT NULL CHECK (ai_verdict IN ('OK', 'NOK', 'MANUAL')),
    ai_reason        TEXT NOT NULL,
    status           TEXT NOT NULL
        CHECK (status IN ('AUTO_DONE', 'PENDING_REVIEW', 'REVIEWED'))
);

CREATE TABLE IF NOT EXISTS detection (
    id             INTEGER PRIMARY KEY,
    inspection_id  INTEGER NOT NULL REFERENCES inspection(id) ON DELETE CASCADE,
    defect_type    TEXT NOT NULL
        CHECK (defect_type IN ('RISS', 'KRATZER', 'DRUCKSTELLE', 'BLAESCHEN', 'SPIEGELUNG')),
    x              INTEGER NOT NULL,
    y              INTEGER NOT NULL,
    w              INTEGER NOT NULL,
    h              INTEGER NOT NULL,
    length_px      REAL,
    confidence     REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    severity       TEXT NOT NULL CHECK (severity IN ('NONE', 'MINOR', 'MAJOR', 'CRITICAL')),
    injected_error TEXT
);

CREATE TABLE IF NOT EXISTS review (
    id            INTEGER PRIMARY KEY,
    inspection_id INTEGER NOT NULL UNIQUE REFERENCES inspection(id) ON DELETE CASCADE,
    decision      TEXT NOT NULL CHECK (decision IN ('CONFIRMED', 'CORRECTED')),
    final_verdict TEXT NOT NULL CHECK (final_verdict IN ('OK', 'NOK')),
    comment       TEXT,
    reviewed_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS review_change (
    id              INTEGER PRIMARY KEY,
    review_id       INTEGER NOT NULL REFERENCES review(id) ON DELETE CASCADE,
    detection_id    INTEGER REFERENCES detection(id),
    change          TEXT NOT NULL CHECK (change IN ('REJECTED', 'TYPE_CHANGED', 'MISSED')),
    new_defect_type TEXT
        CHECK (new_defect_type IS NULL
               OR new_defect_type IN ('RISS', 'KRATZER', 'DRUCKSTELLE', 'BLAESCHEN', 'SPIEGELUNG'))
);

CREATE INDEX IF NOT EXISTS idx_batch_line ON batch(line_id);
CREATE INDEX IF NOT EXISTS idx_part_batch ON part(batch_id);
CREATE INDEX IF NOT EXISTS idx_inspection_part ON inspection(part_id);
CREATE INDEX IF NOT EXISTS idx_inspection_time ON inspection(inspected_at);
CREATE INDEX IF NOT EXISTS idx_detection_inspection ON detection(inspection_id);
