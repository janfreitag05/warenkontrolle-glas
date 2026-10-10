"""Detector interface and the mock AI (docs/konzept.md, section 4).

The mock never looks at the image. It derives its findings from the known labels
(gt_defect) and adds realistic noise: varying confidences, box jitter and deliberate
errors. Every deliberate deviation is recorded in Detection.injected_error.
"""

from __future__ import annotations

import random
import sqlite3
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field

from app import config, rules


@dataclass(frozen=True)
class GroundTruth:
    defect_type: str
    x: int
    y: int
    w: int
    h: int
    length_px: float | None = None


@dataclass(frozen=True)
class PartInput:
    """What a detector gets: the serial plus, for the mock only, the known labels."""
    serial: str
    ground_truth: tuple[GroundTruth, ...] = ()
    part_id: int | None = None


@dataclass(frozen=True)
class Detection:
    defect_type: str
    x: int
    y: int
    w: int
    h: int
    length_px: float | None
    confidence: float
    injected_error: str | None = None


@dataclass(frozen=True)
class MockOutcome:
    """Detections plus the ground truth defects the mock deliberately missed."""
    detections: list[Detection]
    missed: list[GroundTruth] = field(default_factory=list)


class Detector(ABC):
    version: str

    @abstractmethod
    def analyze(self, part: PartInput) -> list[Detection]:
        """Return the findings for one part."""


Force = tuple[int, str]  # (index of the ground truth defect, injected_error to apply)


def target_verdict(part: PartInput) -> str:
    """Verdict a perfect detector would give (ground truth, full confidence)."""
    perfect = [Detection(t.defect_type, t.x, t.y, t.w, t.h, t.length_px, 1.0) for t in part.ground_truth]
    return rules.evaluate(perfect)[0]


class MockDetector(Detector):
    """Mock AI. Given the full list of parts, it also guarantees the required
    scenarios (MOCK_REQUIRED_SCENARIOS), each on a part whose verdict it changes."""

    version = config.DETECTOR_VERSION

    def __init__(self, seed: int = config.MOCK_SEED, parts: Sequence[PartInput] = ()):
        self.seed = seed
        self._forced: dict[str, Force] = {}
        self.required: dict[str, tuple[str, str]] = {}  # serial -> scenario
        if parts:
            self._assign_required(parts)

    def analyze(self, part: PartInput) -> list[Detection]:
        return self.simulate(part).detections

    def simulate(self, part: PartInput) -> MockOutcome:
        return self._run(part, self._forced.get(part.serial))

    def _run(self, part: PartInput, force: Force | None) -> MockOutcome:
        detections: list[Detection] = []
        missed: list[GroundTruth] = []
        for index, truth in enumerate(part.ground_truth):
            # One random stream per defect: forcing one defect never shifts the others.
            rng = random.Random(f"{self.seed}:{part.serial}:{index}")
            forced = force[1] if force and force[0] == index else None
            if truth.defect_type in config.NON_DEFECT_TYPES:
                detections.append(self._reflection(rng, truth, forced))
            elif rng.random() < config.MOCK_RATE_MISSED_DEFECT and forced is None:
                missed.append(truth)
            else:
                detections.append(self._defect(rng, truth, forced))
        return MockOutcome(detections, missed)

    # --- required scenarios ---------------------------------------------------------

    def _assign_required(self, parts: Sequence[PartInput]) -> None:
        """Pick one part per scenario, deterministically from seed and serial.

        Parts where the scenario already happens by chance come first, so no extra
        deviation is added. Otherwise the error is forced onto a suitable defect.
        """
        for error, target in config.MOCK_REQUIRED_SCENARIOS:
            pool = [p for p in parts if p.serial not in self.required and target_verdict(p) == target]
            pool.sort(key=lambda p: (not self._happens_naturally(p, error, target), self._rank(error, target, p.serial)))
            for part in pool:
                if self._happens_naturally(part, error, target):
                    self.required[part.serial] = (error, target)
                    break
                force = self._find_force(part, error, target)
                if force is not None:
                    self._forced[part.serial] = force
                    self.required[part.serial] = (error, target)
                    break

    def _rank(self, error: str, target: str, serial: str) -> float:
        return random.Random(f"{self.seed}:{error}:{target}:{serial}").random()

    def _happens_naturally(self, part: PartInput, error: str, target: str) -> bool:
        outcome = self._run(part, None)
        has_error = any(d.injected_error == error for d in outcome.detections)
        return has_error and rules.evaluate(outcome.detections)[0] != target

    def _find_force(self, part: PartInput, error: str, target: str) -> Force | None:
        for index, truth in enumerate(part.ground_truth):
            if error == "FALSE_POSITIVE" and truth.defect_type not in config.NON_DEFECT_TYPES:
                continue
            if error == "TYPE_CONFUSION" and truth.defect_type not in config.MOCK_TYPE_CONFUSION:
                continue
            outcome = self._run(part, (index, error))
            if rules.evaluate(outcome.detections)[0] != target:
                return index, error
        return None

    # --- per defect --------------------------------------------------------------
    # A forced error only overrides the decision of its own defect.

    def _reflection(self, rng: random.Random, truth: GroundTruth, forced: str | None) -> Detection:
        roll = rng.random() < config.MOCK_RATE_REFLECTION_AS_SCRATCH
        if forced == "FALSE_POSITIVE" or (forced is None and roll):
            length = float(max(truth.w, truth.h))
            return self._detect(rng, truth, "KRATZER", length, config.MOCK_CONFIDENCE_RANGE, "FALSE_POSITIVE")
        return self._with_confidence(rng, truth, truth.defect_type, truth.length_px, config.MOCK_CONFIDENCE_RANGE, forced)

    def _defect(self, rng: random.Random, truth: GroundTruth, forced: str | None) -> Detection:
        confidence_range = self._confidence_range(truth)
        confused = config.MOCK_TYPE_CONFUSION.get(truth.defect_type)
        if confused:
            roll = rng.random() < config.MOCK_RATE_TYPE_CONFUSION
            if forced == "TYPE_CONFUSION" or (forced is None and roll):
                return self._detect(rng, truth, confused, truth.length_px, confidence_range, "TYPE_CONFUSION")
        return self._with_confidence(rng, truth, truth.defect_type, truth.length_px, confidence_range, forced)

    def _with_confidence(self, rng, truth, defect_type, length_px, confidence_range, forced) -> Detection:
        """Unaltered finding, unless its confidence is deliberately pushed down."""
        roll = rng.random() < config.MOCK_RATE_LOW_CONFIDENCE
        if forced == "LOW_CONFIDENCE" or (forced is None and roll):
            return self._detect(rng, truth, defect_type, length_px, config.MOCK_CONFIDENCE_RANGE_LOW, "LOW_CONFIDENCE")
        return self._detect(rng, truth, defect_type, length_px, confidence_range, None)

    @staticmethod
    def _confidence_range(truth: GroundTruth) -> tuple[float, float]:
        subtle = truth.defect_type in config.MOCK_SUBTLE_TYPES or (
            truth.defect_type == "KRATZER"
            and truth.length_px is not None
            and truth.length_px * config.MM_PER_PX < config.SCRATCH_MAX_MM
        )
        return config.MOCK_CONFIDENCE_RANGE_SUBTLE if subtle else config.MOCK_CONFIDENCE_RANGE

    @staticmethod
    def _detect(rng, truth, defect_type, length_px, confidence_range, injected_error) -> Detection:
        confidence = round(rng.uniform(*confidence_range), config.MOCK_CONFIDENCE_DECIMALS)
        x, y, w, h = jitter_box(rng, truth)
        return Detection(defect_type, x, y, w, h, length_px, confidence, injected_error)


def jitter_box(rng: random.Random, truth: GroundTruth) -> tuple[int, int, int, int]:
    """Shift the box edges by up to MOCK_BOX_JITTER_PX, kept on the glass plate."""
    j = config.MOCK_BOX_JITTER_PX
    px, py = config.PLATE_OFFSET_PX
    px1, py1 = px + config.PLATE_WIDTH_PX, py + config.PLATE_HEIGHT_PX
    x0 = min(max(truth.x + rng.randint(-j, j), px), px1 - 1)
    y0 = min(max(truth.y + rng.randint(-j, j), py), py1 - 1)
    x1 = min(max(truth.x + truth.w + rng.randint(-j, j), x0 + 1), px1)
    y1 = min(max(truth.y + truth.h + rng.randint(-j, j), y0 + 1), py1)
    return x0, y0, x1 - x0, y1 - y0


# --- Loading parts ------------------------------------------------------------------

def part_from_label(label: dict) -> PartInput:
    truths = tuple(
        GroundTruth(d["defect_type"], d["x"], d["y"], d["w"], d["h"], d.get("length_px"))
        for d in label["defects"]
    )
    return PartInput(label["serial"], truths)


def load_parts(conn: sqlite3.Connection) -> list[PartInput]:
    """All parts with their ground truth from the database, ordered by serial."""
    rows = conn.execute(
        "SELECT p.id AS part_id, p.serial, g.defect_type, g.x, g.y, g.w, g.h, g.length_px "
        "FROM part p LEFT JOIN gt_defect g ON g.part_id = p.id "
        "ORDER BY p.serial, g.id"
    ).fetchall()
    parts: dict[str, dict] = {}
    for row in rows:
        entry = parts.setdefault(row["serial"], {"part_id": row["part_id"], "truths": []})
        if row["defect_type"] is not None:
            entry["truths"].append(
                GroundTruth(row["defect_type"], row["x"], row["y"], row["w"], row["h"], row["length_px"])
            )
    return [PartInput(serial, tuple(e["truths"]), e["part_id"]) for serial, e in parts.items()]
