import pytest

from app import config, rules
from app.detector import Detection

SCRATCH_AT_LIMIT_PX = config.SCRATCH_MAX_MM / config.MM_PER_PX
SCRATCH_JUST_BELOW_PX = (config.SCRATCH_MAX_MM - 0.1) / config.MM_PER_PX
SHORT_SCRATCH_PX = config.SCRATCH_SHORT_PX_RANGE[0]
SURE = 0.90
UNSURE = 0.69
LIMIT = 0.70


def det(defect_type, confidence=SURE, length_px=None):
    return Detection(defect_type, 100, 100, 10, 10, length_px, confidence)


def bubbles(count, confidence=SURE):
    return [det("BLAESCHEN", confidence) for _ in range(count)]


N = config.BUBBLE_NOK_COUNT

CASES = [
    # id, detections, expected verdict
    ("keine Erkennung", [], "OK"),
    ("nur Spiegelung sicher", [det("SPIEGELUNG")], "OK"),
    ("Spiegelung genau 0,70", [det("SPIEGELUNG", LIMIT)], "OK"),
    ("unsichere Spiegelung allein", [det("SPIEGELUNG", UNSURE)], "MANUAL"),
    ("Riss sicher", [det("RISS")], "NOK"),
    ("Riss genau 0,70", [det("RISS", LIMIT)], "NOK"),
    ("Riss 0,69", [det("RISS", UNSURE)], "MANUAL"),
    ("Druckstelle sicher", [det("DRUCKSTELLE")], "NOK"),
    ("Druckstelle 0,69", [det("DRUCKSTELLE", UNSURE)], "MANUAL"),
    ("Kratzer genau am Grenzwert", [det("KRATZER", length_px=SCRATCH_AT_LIMIT_PX)], "NOK"),
    ("Kratzer knapp darunter", [det("KRATZER", length_px=SCRATCH_JUST_BELOW_PX)], "OK"),
    ("Kratzer am Grenzwert, 0,69", [det("KRATZER", UNSURE, SCRATCH_AT_LIMIT_PX)], "MANUAL"),
    ("Kratzer kurz, 0,69", [det("KRATZER", UNSURE, SHORT_SCRATCH_PX)], "MANUAL"),
    ("Kratzer ohne Länge gilt als lang", [det("KRATZER")], "NOK"),
    ("Blasen genau am Schwellwert", bubbles(N), "NOK"),
    ("Blasen eine darunter", bubbles(N - 1), "OK"),
    ("Blasen am Schwellwert, eine unsicher", bubbles(N - 1) + bubbles(1, UNSURE), "MANUAL"),
    ("Blasen am Schwellwert, alle genau 0,70", bubbles(N, LIMIT), "NOK"),
    ("sicherer Riss plus unsichere Erkennung", [det("RISS"), det("KRATZER", UNSURE, SHORT_SCRATCH_PX)], "NOK"),
    ("sicherer Riss plus unsichere Spiegelung", [det("RISS"), det("SPIEGELUNG", UNSURE)], "NOK"),
    ("sichere Blasen-NOK plus unsicherer Riss", bubbles(N) + [det("RISS", UNSURE)], "NOK"),
    ("kurzer Kratzer plus unsichere Spiegelung", [det("KRATZER", length_px=SHORT_SCRATCH_PX), det("SPIEGELUNG", UNSURE)], "MANUAL"),
    ("kurzer Kratzer plus Spiegelung", [det("KRATZER", length_px=SHORT_SCRATCH_PX), det("SPIEGELUNG")], "OK"),
    ("Spiegelung zählt nie als Fehler", [det("SPIEGELUNG", 0.99) for _ in range(5)], "OK"),
]


@pytest.mark.parametrize("detections, expected", [c[1:] for c in CASES], ids=[c[0] for c in CASES])
def test_verdict(detections, expected):
    verdict, reason = rules.evaluate(detections)
    assert verdict == expected
    prefix = {"OK": "OK: ", "NOK": "NOK: ", "MANUAL": "Manuelle Prüfung: "}[expected]
    assert reason.startswith(prefix)


REASONS = [
    ([], "OK: keine Auffälligkeiten erkannt"),
    ([det("RISS")], "NOK: Riss erkannt (kritisch)"),
    ([det("DRUCKSTELLE")], "NOK: Druckstelle erkannt (schwerwiegend)"),
    ([det("SPIEGELUNG")], "OK: nur Spiegeleffekt erkannt, kein Fehler"),
    ([det("SPIEGELUNG", UNSURE)], "Manuelle Prüfung: Spiegeleffekt unsicher erkannt (Konfidenz 0,69)"),
    (
        [det("KRATZER", length_px=SCRATCH_AT_LIMIT_PX)],
        "NOK: Kratzer 40,0 mm erkannt (schwerwiegend, Grenzwert ab 40 mm)",
    ),
    (bubbles(3), "NOK: 3 Bläschen erkannt (schwerwiegend, Grenzwert ab 3)"),
    (bubbles(2), "OK: nur geringfügige Fehler (2 Bläschen)"),
    (
        [det("KRATZER", length_px=SCRATCH_JUST_BELOW_PX)],
        "OK: nur geringfügige Fehler (Kratzer 39,9 mm)",
    ),
    (
        [det("RISS"), det("DRUCKSTELLE")],
        "NOK: Riss erkannt (kritisch); Druckstelle erkannt (schwerwiegend)",
    ),
]


@pytest.mark.parametrize("detections, reason", REASONS)
def test_reason_text(detections, reason):
    assert rules.evaluate(detections)[1] == reason


def test_threshold_values_used_by_tests():
    # the reason texts above assume the agreed values
    assert config.SCRATCH_MAX_MM == 40.0
    assert config.BUBBLE_NOK_COUNT == 3
    assert config.CONFIDENCE_MIN == LIMIT


SEVERITY_CASES = [
    (det("RISS"), 0, "CRITICAL"),
    (det("DRUCKSTELLE"), 0, "MAJOR"),
    (det("SPIEGELUNG"), 0, "NONE"),
    (det("KRATZER", length_px=SCRATCH_AT_LIMIT_PX), 0, "MAJOR"),
    (det("KRATZER", length_px=SCRATCH_JUST_BELOW_PX), 0, "MINOR"),
    (det("BLAESCHEN"), N, "MAJOR"),
    (det("BLAESCHEN"), N - 1, "MINOR"),
]


@pytest.mark.parametrize("detection, bubble_count, expected", SEVERITY_CASES)
def test_severity(detection, bubble_count, expected):
    assert rules.severity(detection, bubble_count) == expected


def test_severities_count_bubbles_of_the_part():
    assert rules.severities(bubbles(N) + [det("RISS")]) == ["MAJOR"] * N + ["CRITICAL"]
    assert rules.severities(bubbles(N - 1)) == ["MINOR"] * (N - 1)


def test_evaluate_is_pure():
    detections = [det("RISS"), det("SPIEGELUNG", UNSURE)]
    copy = list(detections)
    assert rules.evaluate(detections) == rules.evaluate(detections)
    assert detections == copy
