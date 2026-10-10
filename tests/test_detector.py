from collections import Counter

import pytest

from app import config, db, rules
from app.detector import Detector, MockDetector, load_parts, part_from_label, target_verdict
from tools import generate_images as gen

RATE_TOLERANCE = 0.02
RATE_PARTS = 1000
SEED = 42


@pytest.fixture(scope="module")
def big_sample():
    """1,000 parts as labels in memory only (no PNG files)."""
    parts = [part_from_label(label) for label in gen.build_labels(RATE_PARTS, SEED)]
    detector = MockDetector(parts=parts)  # including the required scenarios
    return parts, [detector.simulate(p) for p in parts]


def measured_rates(parts, outcomes):
    truths = [t for p in parts for t in p.ground_truth]
    reflections = sum(1 for t in truths if t.defect_type in config.NON_DEFECT_TYPES)
    real = len(truths) - reflections
    confusable = sum(1 for t in truths if t.defect_type in config.MOCK_TYPE_CONFUSION)

    errors = Counter(d.injected_error for o in outcomes for d in o.detections if d.injected_error)
    missed = sum(len(o.missed) for o in outcomes)
    missed_confusable = sum(
        1 for o in outcomes for t in o.missed if t.defect_type in config.MOCK_TYPE_CONFUSION
    )
    # detections that went through the low-confidence roll: all not otherwise altered
    low_population = (reflections - errors["FALSE_POSITIVE"]) + (real - missed - errors["TYPE_CONFUSION"])

    return {
        "reflection_as_scratch": (errors["FALSE_POSITIVE"], reflections, config.MOCK_RATE_REFLECTION_AS_SCRATCH),
        "missed": (missed, real, config.MOCK_RATE_MISSED_DEFECT),
        "type_confusion": (errors["TYPE_CONFUSION"], confusable - missed_confusable, config.MOCK_RATE_TYPE_CONFUSION),
        "low_confidence": (errors["LOW_CONFIDENCE"], low_population, config.MOCK_RATE_LOW_CONFIDENCE),
    }


@pytest.mark.parametrize("name", ["reflection_as_scratch", "missed", "type_confusion", "low_confidence"])
def test_error_rates_within_tolerance(big_sample, name):
    hits, population, expected = measured_rates(*big_sample)[name]
    assert population > 100, f"{name}: population too small ({population})"
    rate = hits / population
    assert abs(rate - expected) <= RATE_TOLERANCE, f"{name}: {hits}/{population} = {rate:.3f}, expected {expected}"


def test_two_runs_identical(big_sample):
    parts, outcomes = big_sample
    again = [MockDetector(parts=parts).simulate(p) for p in parts]
    assert again == outcomes


def test_other_mock_seed_differs(big_sample):
    parts, outcomes = big_sample
    other = [MockDetector(seed=config.MOCK_SEED + 1).simulate(p) for p in parts]
    assert other != outcomes


def test_result_depends_on_serial_not_on_order(big_sample):
    parts, outcomes = big_sample
    detector = MockDetector(parts=list(reversed(parts)))
    assert [detector.simulate(p) for p in reversed(parts)] == list(reversed(outcomes))


# --- Required scenarios -------------------------------------------------------------

def sample(seed, count=60):
    """Labels and mock seed share the same number, as in a test session."""
    parts = [part_from_label(label) for label in gen.build_labels(count, seed)]
    return parts, MockDetector(seed=seed, parts=parts)


def deviating(parts, detector):
    return {p.serial for p in parts if rules.evaluate(detector.analyze(p))[0] != target_verdict(p)}


def test_seed_42_exactly_the_required_parts_deviate():
    # Holds for the session data (seed 42). With unchanged rates, random errors can change
    # further verdicts for other seeds (e.g. seed 7); see the general tests below.
    parts, detector = sample(42)
    assert sorted(detector.required.values()) == sorted(config.MOCK_REQUIRED_SCENARIOS)
    assert deviating(parts, detector) == set(detector.required)


@pytest.mark.parametrize("seed", [1, 2, 3, 42, 7])
def test_required_parts_are_among_the_deviating(seed):
    parts, detector = sample(seed)
    assert set(detector.required) <= deviating(parts, detector)


@pytest.mark.parametrize("seed", [1, 2, 3, 42, 7])
def test_required_scenarios_change_verdict_as_specified(seed):
    parts, detector = sample(seed)
    by_serial = {p.serial: p for p in parts}
    assert sorted(detector.required.values()) == sorted(config.MOCK_REQUIRED_SCENARIOS)
    for serial, (error, target) in detector.required.items():
        part = by_serial[serial]
        detections = detector.analyze(part)
        assert target_verdict(part) == target
        assert rules.evaluate(detections)[0] != target
        assert error in {d.injected_error for d in detections}


@pytest.mark.parametrize("seed", [1, 2, 3, 42, 7])
def test_every_deviation_is_recorded(seed):
    parts, detector = sample(seed)
    for part in parts:
        outcome = detector.simulate(part)
        if rules.evaluate(outcome.detections)[0] != target_verdict(part):
            assert outcome.missed or any(d.injected_error for d in outcome.detections), part.serial


@pytest.mark.parametrize("seed", [1, 2, 3, 42, 7])
def test_forcing_changes_only_one_finding(seed):
    parts, detector = sample(seed)
    plain = MockDetector(seed=seed)
    by_serial = {p.serial: p for p in parts}
    for serial, (error, _) in detector.required.items():
        natural = plain.simulate(by_serial[serial])
        forced = detector.simulate(by_serial[serial])
        removed = Counter(natural.detections) - Counter(forced.detections)
        added = Counter(forced.detections) - Counter(natural.detections)
        assert sum(removed.values()) + len(natural.missed) - len(forced.missed) <= 1, serial
        assert sum(added.values()) <= 1, serial
        assert all(d.injected_error == error for d in added), serial


def test_required_scenarios_deterministic():
    parts, first = sample(42)
    _, second = sample(42)
    assert first.required == second.required
    assert [first.simulate(p) for p in parts] == [second.simulate(p) for p in parts]


def test_no_required_scenario_for_missed():
    assert "MISSED" not in {error for error, _ in config.MOCK_REQUIRED_SCENARIOS}
    assert {error for error, _ in config.MOCK_REQUIRED_SCENARIOS} <= set(config.INJECTED_ERRORS)


def test_without_part_list_no_scenarios_are_forced(big_sample):
    parts, _ = big_sample
    assert MockDetector().required == {}


def test_only_injected_errors_are_uncertain(big_sample):
    _, outcomes = big_sample
    for o in outcomes:
        for d in o.detections:
            if d.injected_error == "LOW_CONFIDENCE":
                assert d.confidence < config.CONFIDENCE_MIN
            else:
                assert d.confidence >= config.CONFIDENCE_MIN


def test_injected_errors_are_consistent(big_sample):
    parts, outcomes = big_sample
    assert all(
        d.injected_error in (None, *config.INJECTED_ERRORS) for o in outcomes for d in o.detections
    )
    for o in outcomes:
        for d in o.detections:
            if d.injected_error == "FALSE_POSITIVE":
                assert d.defect_type == "KRATZER"
            if d.injected_error == "TYPE_CONFUSION":
                assert d.defect_type in config.MOCK_TYPE_CONFUSION.values()
    # without errors every ground truth defect is found with its own type
    for p, o in zip(parts, outcomes):
        if not o.missed and not any(d.injected_error in ("FALSE_POSITIVE", "TYPE_CONFUSION") for d in o.detections):
            assert sorted(d.defect_type for d in o.detections) == sorted(t.defect_type for t in p.ground_truth)


def test_boxes_jittered_within_limit_and_on_plate(big_sample):
    parts, outcomes = big_sample
    x0, y0 = config.PLATE_OFFSET_PX
    x1, y1 = x0 + config.PLATE_WIDTH_PX, y0 + config.PLATE_HEIGHT_PX
    j = config.MOCK_BOX_JITTER_PX
    for p, o in zip(parts, outcomes):
        if o.missed:
            continue
        for t, d in zip(p.ground_truth, o.detections):
            assert abs(d.x - t.x) <= j and abs(d.y - t.y) <= j
            assert abs((d.x + d.w) - (t.x + t.w)) <= j and abs((d.y + d.h) - (t.y + t.h)) <= j
            assert x0 <= d.x and d.x + d.w <= x1 and y0 <= d.y and d.y + d.h <= y1
            assert d.w > 0 and d.h > 0


def test_confidences_rounded_and_in_range(big_sample):
    _, outcomes = big_sample
    lowest = config.MOCK_CONFIDENCE_RANGE_LOW[0]
    for o in outcomes:
        for d in o.detections:
            assert lowest <= d.confidence <= config.MOCK_CONFIDENCE_RANGE[1]
            assert round(d.confidence, config.MOCK_CONFIDENCE_DECIMALS) == d.confidence


def test_mock_is_a_detector():
    detector = MockDetector()
    assert isinstance(detector, Detector)
    assert detector.version == config.DETECTOR_VERSION


def test_confidence_ranges_consistent():
    assert config.MOCK_CONFIDENCE_RANGE_LOW[1] < config.CONFIDENCE_MIN
    assert config.CONFIDENCE_MIN <= config.MOCK_CONFIDENCE_RANGE_SUBTLE[0]
    assert config.CONFIDENCE_MIN <= config.MOCK_CONFIDENCE_RANGE[0]


# --- Database input, no image access ------------------------------------------------

@pytest.fixture
def db_without_images(tmp_path):
    labels = gen.build_labels(60, SEED)
    conn = db.connect(tmp_path / "labels_only.db")
    db.init_db(conn)
    gen.write_database(conn, SEED, labels)
    yield conn, labels
    conn.close()


def test_load_parts_matches_labels(db_without_images):
    conn, labels = db_without_images
    parts = load_parts(conn)
    assert len(parts) == 60
    assert all(p.part_id is not None for p in parts)
    expected = {l["serial"]: part_from_label(l).ground_truth for l in labels}
    assert {p.serial: p.ground_truth for p in parts} == expected


def test_mock_never_opens_images(db_without_images, monkeypatch):
    import PIL.Image

    def forbidden(*args, **kwargs):
        raise AssertionError("mock must not open image files")

    monkeypatch.setattr(PIL.Image, "open", forbidden)
    conn, _ = db_without_images
    detector = MockDetector()
    results = [detector.analyze(p) for p in load_parts(conn)]
    assert len(results) == 60


def test_db_and_label_input_give_same_result(db_without_images):
    conn, labels = db_without_images
    detector = MockDetector()
    from_db = {p.serial: detector.analyze(p) for p in load_parts(conn)}
    from_labels = {l["serial"]: detector.analyze(part_from_label(l)) for l in labels}
    assert from_db == from_labels
