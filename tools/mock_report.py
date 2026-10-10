"""Run the mock AI over all parts in the database and print a table (step 3).

Usage: python -m tools.mock_report [--mock-seed 42]

Read-only: nothing is written to the database (that follows in step 4).
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter

from app import config, db, rules
from app.detector import Detection, GroundTruth, MockDetector, MockOutcome, PartInput, load_parts

VERDICT_ORDER = ("OK", "NOK", "MANUAL")


def summarize_truth(truths: tuple[GroundTruth, ...] | list[GroundTruth]) -> str:
    counts = Counter(t.defect_type for t in truths)
    return ", ".join(f"{n}×{t}" if n > 1 else t for t, n in counts.items()) or "–"


def summarize_detections(detections: list[Detection]) -> str:
    lowest: dict[str, float] = {}
    counts: Counter[str] = Counter()
    for d in detections:
        counts[d.defect_type] += 1
        lowest[d.defect_type] = min(d.confidence, lowest.get(d.defect_type, d.confidence))
    parts = []
    for defect_type, n in counts.items():
        prefix = f"{n}×" if n > 1 else ""
        parts.append(f"{prefix}{defect_type} {rules.fmt_number(lowest[defect_type], 2)}")
    return ", ".join(parts) or "–"


def injected_errors(outcome: MockOutcome) -> list[str]:
    codes = [d.injected_error for d in outcome.detections if d.injected_error]
    return codes + ["MISSED"] * len(outcome.missed)


def target_verdict(part: PartInput) -> str:
    """Verdict a perfect detector would give (ground truth, full confidence)."""
    perfect = [Detection(t.defect_type, t.x, t.y, t.w, t.h, t.length_px, 1.0) for t in part.ground_truth]
    return rules.evaluate(perfect)[0]


def build_rows(parts: list[PartInput], detector: MockDetector) -> list[dict]:
    rows = []
    for part in parts:
        outcome = detector.simulate(part)
        verdict, reason = rules.evaluate(outcome.detections)
        rows.append(
            {
                "serial": part.serial,
                "truth": summarize_truth(part.ground_truth),
                "detected": summarize_detections(outcome.detections),
                "verdict": verdict,
                "target": target_verdict(part),
                "reason": reason,
                "errors": injected_errors(outcome),
            }
        )
    return rows


def format_table(rows: list[dict]) -> str:
    headers = ["Seriennummer", "Echte Fehler", "Erkannt (min. Konfidenz)", "Urteil", "Begründung", "injected_error"]
    lines = [
        [r["serial"], r["truth"], r["detected"], r["verdict"], r["reason"], ", ".join(r["errors"]) or "–"]
        for r in rows
    ]
    widths = [max(len(h), *(len(line[i]) for line in lines)) for i, h in enumerate(headers)]
    widths[-1] = len(headers[-1])  # last column is not padded

    def fmt(cells: list[str]) -> str:
        return " | ".join(cell.ljust(width) for cell, width in zip(cells, widths)).rstrip()

    out = [fmt(headers), "-+-".join("-" * w for w in widths)]
    out.extend(fmt(line) for line in lines)
    return "\n".join(out)


def format_summary(rows: list[dict]) -> str:
    verdicts = Counter(r["verdict"] for r in rows)
    errors = Counter(code for r in rows for code in r["errors"])
    deviating = [r for r in rows if r["verdict"] != r["target"]]
    lines = [f"Teile: {len(rows)}", "Urteile:"]
    lines += [f"  {config.VERDICTS[v]:<17} {verdicts.get(v, 0):>3}" for v in VERDICT_ORDER]
    lines.append("injected_error:")
    lines += [f"  {code:<15} {errors.get(code, 0):>3}  ({name})" for code, name in config.INJECTED_ERRORS.items()]
    noun = "Teil" if len(deviating) == 1 else "Teile"
    lines.append(f"Urteil weicht vom Soll (laut Labels) ab: {len(deviating)} {noun}")
    lines += [f"  {r['serial']}: Soll {r['target']}, Mock {r['verdict']}" for r in deviating]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Lässt die Mock-KI über alle Teile laufen (nur Anzeige).")
    parser.add_argument("--mock-seed", type=int, default=config.MOCK_SEED)
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    conn = db.connect()
    try:
        db.init_db(conn)
        parts = load_parts(conn)
    finally:
        conn.close()
    if not parts:
        print("Keine Teile in der Datenbank. Zuerst: python -m tools.generate_images")
        return

    rows = build_rows(parts, MockDetector(seed=args.mock_seed, parts=parts))
    print(format_table(rows))
    print()
    print(format_summary(rows))


if __name__ == "__main__":
    main()
