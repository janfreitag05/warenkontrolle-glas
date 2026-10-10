"""Evaluation rules: detections -> severity and verdict (docs/konzept.md, section 4).

Pure functions: no database, no randomness. All thresholds come from config.py.
"""

from collections.abc import Sequence
from typing import Protocol

from app import config


class DetectionLike(Protocol):
    defect_type: str
    confidence: float
    length_px: float | None


def is_certain(detection: DetectionLike) -> bool:
    return detection.confidence >= config.CONFIDENCE_MIN


def length_mm(detection: DetectionLike) -> float | None:
    if detection.length_px is None:
        return None
    return detection.length_px * config.MM_PER_PX


def is_long_scratch(detection: DetectionLike) -> bool:
    """Unknown length counts as long (conservative)."""
    mm = length_mm(detection)
    return mm is None or mm >= config.SCRATCH_MAX_MM


def severity(detection: DetectionLike, bubble_count: int) -> str:
    """Severity of one detection; bubbles depend on how many there are on the part."""
    if detection.defect_type in config.SEVERITY_BY_TYPE:
        return config.SEVERITY_BY_TYPE[detection.defect_type]
    if detection.defect_type == "KRATZER":
        return "MAJOR" if is_long_scratch(detection) else "MINOR"
    if detection.defect_type == "BLAESCHEN":
        return "MAJOR" if bubble_count >= config.BUBBLE_NOK_COUNT else "MINOR"
    raise ValueError(f"Unknown defect type: {detection.defect_type}")


def severities(detections: Sequence[DetectionLike]) -> list[str]:
    bubbles = count_type(detections, "BLAESCHEN")
    return [severity(d, bubbles) for d in detections]


def count_type(detections: Sequence[DetectionLike], defect_type: str) -> int:
    return sum(1 for d in detections if d.defect_type == defect_type)


# --- Text helpers -------------------------------------------------------------------

def fmt_number(value: float, decimals: int = 1) -> str:
    return f"{value:.{decimals}f}".replace(".", ",")


def fmt_limit(value: float) -> str:
    return f"{value:g}".replace(".", ",")


def type_name(defect_type: str) -> str:
    return config.DEFECT_TYPES[defect_type]


def severity_name(code: str) -> str:
    return config.SEVERITIES[code]


def scratch_text(detection: DetectionLike) -> str:
    mm = length_mm(detection)
    return type_name("KRATZER") if mm is None else f"{type_name('KRATZER')} {fmt_number(mm)} mm"


# --- Verdict ------------------------------------------------------------------------

def nok_findings(certain: Sequence[DetectionLike]) -> list[str]:
    """Reasons why certain detections make the part NOK, most severe first."""
    findings = []
    for defect_type in ("RISS", "DRUCKSTELLE"):
        if count_type(certain, defect_type):
            label = severity_name(config.SEVERITY_BY_TYPE[defect_type])
            findings.append(f"{type_name(defect_type)} erkannt ({label})")

    long_scratches = [d for d in certain if d.defect_type == "KRATZER" and is_long_scratch(d)]
    if long_scratches:
        longest = max(long_scratches, key=lambda d: d.length_px if d.length_px is not None else float("inf"))
        findings.append(
            f"{scratch_text(longest)} erkannt ({severity_name('MAJOR')}, "
            f"Grenzwert ab {fmt_limit(config.SCRATCH_MAX_MM)} mm)"
        )

    bubbles = count_type(certain, "BLAESCHEN")
    if bubbles >= config.BUBBLE_NOK_COUNT:
        findings.append(
            f"{bubbles} {type_name('BLAESCHEN')} erkannt ({severity_name('MAJOR')}, "
            f"Grenzwert ab {config.BUBBLE_NOK_COUNT})"
        )
    return findings


def uncertain_findings(uncertain: Sequence[DetectionLike]) -> list[str]:
    """One entry per defect type with its lowest confidence."""
    lowest: dict[str, float] = {}
    for d in uncertain:
        lowest[d.defect_type] = min(d.confidence, lowest.get(d.defect_type, d.confidence))
    return [
        f"{type_name(t)} unsicher erkannt (Konfidenz {fmt_number(c, 2)})"
        for t, c in sorted(lowest.items(), key=lambda item: item[1])
    ]


def minor_findings(detections: Sequence[DetectionLike]) -> list[str]:
    findings = [scratch_text(d) for d in detections if d.defect_type == "KRATZER"]
    bubbles = count_type(detections, "BLAESCHEN")
    if bubbles:
        findings.append(f"{bubbles} {type_name('BLAESCHEN')}")
    return findings


def evaluate(detections: Sequence[DetectionLike]) -> tuple[str, str]:
    """Return (verdict, reason) with verdict OK, NOK or MANUAL.

    Precedence: a certain NOK beats MANUAL. MANUAL only if nothing certain leads
    to NOK and at least one detection (reflections included) is uncertain.
    """
    certain = [d for d in detections if is_certain(d)]
    uncertain = [d for d in detections if not is_certain(d)]

    findings = nok_findings(certain)
    if findings:
        return "NOK", "NOK: " + "; ".join(findings)
    if uncertain:
        return "MANUAL", "Manuelle Prüfung: " + "; ".join(uncertain_findings(uncertain))
    if not detections:
        return "OK", "OK: keine Auffälligkeiten erkannt"
    minor = minor_findings(detections)
    if not minor:
        return "OK", f"OK: nur {type_name('SPIEGELUNG')} erkannt, kein Fehler"
    return "OK", f"OK: nur geringfügige Fehler ({'; '.join(minor)})"
