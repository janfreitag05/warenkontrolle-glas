"""Synthetic glass images with known defect labels (step 2, docs/konzept.md).

Usage: python -m tools.generate_images --count 60 --seed 42

Writes data/images/{serial}.png and data/labels/{serial}.json and imports batches,
parts and gt_defect into the database. A rerun with the same seed replaces its data.
All quantities, lengths and distributions come from app/config.py.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from app import config, db

Point = tuple[float, float]


# --- Geometry helpers -------------------------------------------------------------

@dataclass(frozen=True)
class Rect:
    x0: float
    y0: float
    x1: float
    y1: float

    def contains(self, x: float, y: float) -> bool:
        return self.x0 <= x <= self.x1 and self.y0 <= y <= self.y1

    def inset(self, amount: float) -> Rect:
        return Rect(self.x0 + amount, self.y0 + amount, self.x1 - amount, self.y1 - amount)

    def overlaps(self, other: Rect) -> bool:
        return not (
            self.x1 <= other.x0 or other.x1 <= self.x0 or self.y1 <= other.y0 or other.y1 <= self.y0
        )

    @property
    def center(self) -> Point:
        return (self.x0 + self.x1) / 2, (self.y0 + self.y1) / 2


PLATE = Rect(
    config.PLATE_OFFSET_PX[0],
    config.PLATE_OFFSET_PX[1],
    config.PLATE_OFFSET_PX[0] + config.PLATE_WIDTH_PX,
    config.PLATE_OFFSET_PX[1] + config.PLATE_HEIGHT_PX,
)
INNER = PLATE.inset(config.GENERATOR_EDGE_MARGIN_PX)


def bounds(points: list[Point], pad: float) -> Rect:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return Rect(min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)


def label_box(rect: Rect) -> dict:
    """Integer box clipped to the glass plate."""
    x0 = math.floor(max(rect.x0, PLATE.x0))
    y0 = math.floor(max(rect.y0, PLATE.y0))
    x1 = math.ceil(min(rect.x1, PLATE.x1))
    y1 = math.ceil(min(rect.y1, PLATE.y1))
    return {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}


def random_point(rng: random.Random, area: Rect) -> Point:
    return rng.uniform(area.x0, area.x1), rng.uniform(area.y0, area.y1)


def path_length(points: list[Point]) -> float:
    return sum(math.dist(a, b) for a, b in zip(points, points[1:]))


# --- Shapes: geometry plus label plus drawing ---------------------------------------

@dataclass
class Crack:
    paths: list[list[Point]]
    defect_type: str = "RISS"

    @property
    def rect(self) -> Rect:
        pad = config.CRACK_LINE_WIDTH_PX + config.CRACK_GLINT_OFFSET_PX + config.GENERATOR_BOX_PADDING_PX
        return bounds([p for path in self.paths for p in path], pad)

    @property
    def length_px(self) -> float:
        return round(sum(path_length(path) for path in self.paths), 1)

    def draw(self, image: Image.Image) -> None:
        draw = ImageDraw.Draw(image)
        offset = config.CRACK_GLINT_OFFSET_PX
        for path in self.paths:
            draw.line(path, fill=config.COLORS["crack_dark"], width=config.CRACK_LINE_WIDTH_PX, joint="curve")
            glint = [(x + offset, y + offset) for x, y in path]
            draw.line(glint, fill=config.COLORS["crack_glint"], width=config.SCRATCH_LINE_WIDTH_PX)


@dataclass
class Scratch:
    start: Point
    end: Point
    length_px: float
    defect_type: str = "KRATZER"

    @property
    def rect(self) -> Rect:
        return bounds([self.start, self.end], config.SCRATCH_LINE_WIDTH_PX + config.GENERATOR_BOX_PADDING_PX)

    def draw(self, image: Image.Image) -> None:
        ImageDraw.Draw(image).line(
            [self.start, self.end], fill=config.COLORS["scratch"], width=config.SCRATCH_LINE_WIDTH_PX
        )


@dataclass
class Dent:
    center: Point
    rx: float
    ry: float
    defect_type: str = "DRUCKSTELLE"
    length_px: float | None = None

    @property
    def ellipse(self) -> list[float]:
        cx, cy = self.center
        return [cx - self.rx, cy - self.ry, cx + self.rx, cy + self.ry]

    @property
    def rect(self) -> Rect:
        x0, y0, x1, y1 = self.ellipse
        pad = config.DENT_RING_WIDTH_PX + config.GENERATOR_BOX_PADDING_PX
        return Rect(x0 - pad, y0 - pad, x1 + pad, y1 + pad)

    def draw(self, image: Image.Image) -> Image.Image:
        dark = Image.new("L", image.size, 0)
        ImageDraw.Draw(dark).ellipse(self.ellipse, fill=config.DENT_ALPHA)
        dark = dark.filter(ImageFilter.GaussianBlur(config.DENT_BLUR_PX))
        image = Image.composite(Image.new("RGB", image.size, config.COLORS["dent_dark"]), image, dark)

        ring = Image.new("L", image.size, 0)
        ImageDraw.Draw(ring).ellipse(self.ellipse, outline=config.DENT_RING_ALPHA, width=config.DENT_RING_WIDTH_PX)
        ring = ring.filter(ImageFilter.GaussianBlur(config.DENT_RING_WIDTH_PX / 2))
        return Image.composite(Image.new("RGB", image.size, config.COLORS["dent_ring"]), image, ring)


@dataclass
class Bubble:
    center: Point
    radius: float
    defect_type: str = "BLAESCHEN"
    length_px: float | None = None

    @property
    def rect(self) -> Rect:
        cx, cy = self.center
        r = self.radius + config.GENERATOR_BOX_PADDING_PX
        return Rect(cx - r, cy - r, cx + r, cy + r)

    def draw(self, image: Image.Image) -> None:
        draw = ImageDraw.Draw(image)
        cx, cy = self.center
        r = self.radius
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=config.COLORS["bubble_fill"], outline=config.COLORS["bubble_rim"])
        g = r * config.BUBBLE_GLINT_SHARE
        gx, gy = cx - r / 2, cy - r / 2
        draw.ellipse([gx - g, gy - g, gx + g, gy + g], fill=config.COLORS["bubble_glint"])


@dataclass
class Reflection:
    polygon: list[Point]
    defect_type: str = "SPIEGELUNG"
    length_px: float | None = None

    @property
    def rect(self) -> Rect:
        return bounds(self.polygon, 0)

    def draw(self, image: Image.Image) -> Image.Image:
        mask = Image.new("L", image.size, 0)
        ImageDraw.Draw(mask).polygon(self.polygon, fill=config.REFLECTION_ALPHA)
        mask = mask.filter(ImageFilter.GaussianBlur(config.REFLECTION_BLUR_PX))
        mask = ImageChops.multiply(mask, plate_mask(image.size))
        return Image.composite(Image.new("RGB", image.size, config.COLORS["reflection"]), image, mask)


Shape = Crack | Scratch | Dent | Bubble | Reflection


# --- Shape factories ----------------------------------------------------------------

def random_walk(rng: random.Random, start: Point, angle: float, target: float) -> list[Point]:
    points = [start]
    walked = 0.0
    turn = math.radians(config.CRACK_MAX_TURN_DEG)
    while walked < target:
        step = min(rng.uniform(*config.CRACK_STEP_PX_RANGE), target - walked)
        angle += rng.uniform(-turn, turn)
        x, y = points[-1]
        nx, ny = x + step * math.cos(angle), y + step * math.sin(angle)
        if not INNER.contains(nx, ny):
            cx, cy = INNER.center
            angle = math.atan2(cy - y, cx - x)
            nx, ny = x + step * math.cos(angle), y + step * math.sin(angle)
        points.append((nx, ny))
        walked += step
    return points


def make_crack(rng: random.Random) -> list[Shape]:
    target = rng.uniform(*config.CRACK_LENGTH_PX_RANGE)
    main = random_walk(rng, random_point(rng, INNER), rng.uniform(0, math.tau), target)
    paths = [main]
    for _ in range(rng.randint(*config.CRACK_BRANCHES)):
        origin = rng.choice(main)
        share = rng.uniform(*config.CRACK_BRANCH_LENGTH_SHARE)
        paths.append(random_walk(rng, origin, rng.uniform(0, math.tau), target * share))
    return [Crack(paths)]


def make_scratch(rng: random.Random, long: bool) -> list[Shape]:
    length_range = config.SCRATCH_LONG_PX_RANGE if long else config.SCRATCH_SHORT_PX_RANGE
    length = round(rng.uniform(*length_range), 1)
    angle = rng.uniform(0, math.pi)
    dx, dy = length / 2 * math.cos(angle), length / 2 * math.sin(angle)
    cx = rng.uniform(INNER.x0 + abs(dx), INNER.x1 - abs(dx))
    cy = rng.uniform(INNER.y0 + abs(dy), INNER.y1 - abs(dy))
    return [Scratch((cx - dx, cy - dy), (cx + dx, cy + dy), length)]


def make_dent(rng: random.Random) -> list[Shape]:
    rx = rng.uniform(*config.DENT_RADIUS_PX_RANGE)
    ry = rx * rng.uniform(*config.DENT_ASPECT_RANGE)
    area = INNER.inset(rx + config.DENT_RING_WIDTH_PX)
    return [Dent(random_point(rng, area), rx, ry)]


def make_bubbles(rng: random.Random, many: bool) -> list[Shape]:
    if many:
        count = rng.randint(config.BUBBLE_NOK_COUNT, config.GENERATOR_BUBBLES_MAX)
    else:
        count = rng.randint(1, config.BUBBLE_NOK_COUNT - 1)
    cluster_center = random_point(rng, INNER.inset(config.BUBBLE_CLUSTER_RADIUS_PX))
    bubbles: list[Bubble] = []
    while len(bubbles) < count:
        radius = rng.uniform(*config.BUBBLE_RADIUS_PX_RANGE)
        for _ in range(config.GENERATOR_PLACEMENT_ATTEMPTS):
            angle = rng.uniform(0, math.tau)
            distance = rng.uniform(0, config.BUBBLE_CLUSTER_RADIUS_PX - radius)
            center = (
                cluster_center[0] + distance * math.cos(angle),
                cluster_center[1] + distance * math.sin(angle),
            )
            if all(math.dist(center, b.center) > radius + b.radius + config.BUBBLE_GAP_PX for b in bubbles):
                break
        bubbles.append(Bubble(center, radius))
    return bubbles


def make_reflection(rng: random.Random) -> list[Shape]:
    diagonal = math.hypot(config.PLATE_WIDTH_PX, config.PLATE_HEIGHT_PX)
    length = diagonal * rng.uniform(*config.REFLECTION_LENGTH_SHARE_RANGE)
    width = rng.uniform(*config.REFLECTION_WIDTH_PX_RANGE)
    angle = math.radians(rng.uniform(*config.REFLECTION_ANGLE_DEG_RANGE)) * rng.choice((-1, 1))
    cx, cy = random_point(rng, INNER)
    ux, uy = math.cos(angle), math.sin(angle)      # along the band
    vx, vy = -uy, ux                               # across the band
    hl, hw = length / 2, width / 2
    polygon = [
        (cx - ux * hl - vx * hw, cy - uy * hl - vy * hw),
        (cx + ux * hl - vx * hw, cy + uy * hl - vy * hw),
        (cx + ux * hl + vx * hw, cy + uy * hl + vy * hw),
        (cx - ux * hl + vx * hw, cy - uy * hl + vy * hw),
    ]
    return [Reflection(polygon)]


FACTORIES = {
    "RISS": make_crack,
    "KRATZER_SHORT": lambda rng: make_scratch(rng, long=False),
    "KRATZER_LONG": lambda rng: make_scratch(rng, long=True),
    "DRUCKSTELLE": make_dent,
    "BLAESCHEN_FEW": lambda rng: make_bubbles(rng, many=False),
    "BLAESCHEN_MANY": lambda rng: make_bubbles(rng, many=True),
    "SPIEGELUNG": make_reflection,
}


def union(shapes: list[Shape]) -> Rect:
    rects = [s.rect for s in shapes]
    return Rect(min(r.x0 for r in rects), min(r.y0 for r in rects), max(r.x1 for r in rects), max(r.y1 for r in rects))


def build_shapes(rng: random.Random, scenario: list[str]) -> list[Shape]:
    """Create shapes for a scenario; real defects avoid overlapping each other."""
    shapes: list[Shape] = []
    occupied: list[Rect] = []
    for variant in scenario:
        factory = FACTORIES[variant]
        if variant == "SPIEGELUNG":
            shapes.extend(factory(rng))
            continue
        for _ in range(config.GENERATOR_PLACEMENT_ATTEMPTS):
            candidate = factory(rng)
            area = union(candidate)
            if not any(area.overlaps(other) for other in occupied):
                break
        occupied.append(area)
        shapes.extend(candidate)
    return shapes


# --- Planning -----------------------------------------------------------------------

def weighted_sample(rng: random.Random, weights: dict[str, float], k: int) -> list[str]:
    """k distinct keys, drawn by weight without replacement."""
    pool = dict(weights)
    chosen = []
    for _ in range(min(k, len(pool))):
        key = rng.choices(list(pool), weights=list(pool.values()))[0]
        chosen.append(key)
        del pool[key]
    return chosen


def random_scenario(rng: random.Random, line: str) -> list[str]:
    profile = config.LINE_PROFILES[line]
    roll = rng.random()
    if roll < profile["share_clean"]:
        return []
    if roll < profile["share_clean"] + profile["share_reflection_only"]:
        return ["SPIEGELUNG"]
    scenario = []
    for defect_type in weighted_sample(rng, profile["defect_weights"], rng.randint(*config.GENERATOR_DEFECTS_PER_PART)):
        if defect_type == "KRATZER":
            long = rng.random() < config.GENERATOR_SHARE_LONG_SCRATCH
            scenario.append("KRATZER_LONG" if long else "KRATZER_SHORT")
        elif defect_type == "BLAESCHEN":
            many = rng.random() < config.GENERATOR_SHARE_MANY_BUBBLES
            scenario.append("BLAESCHEN_MANY" if many else "BLAESCHEN_FEW")
        else:
            scenario.append(defect_type)
    if rng.random() < config.GENERATOR_SHARE_REFLECTION_ADDON:
        scenario.append("SPIEGELUNG")
    return scenario


def plan_parts(count: int, seed: int) -> list[dict]:
    """Line, batch and scenario for each part, deterministic for a seed."""
    rng = random.Random(seed)
    lines = list(config.GENERATOR_LINE_SHARES)
    shares = list(config.GENERATOR_LINE_SHARES.values())
    plans = []
    for index in range(1, count + 1):
        line = rng.choices(lines, weights=shares)[0]
        plans.append({"index": index, "line": line, "scenario": random_scenario(rng, line)})

    required = config.GENERATOR_REQUIRED_SCENARIOS
    if count >= len(required):
        for position, scenario in zip(rng.sample(range(count), len(required)), required):
            plans[position]["scenario"] = list(scenario)

    per_line: dict[str, int] = {}
    for plan in plans:
        position = per_line.get(plan["line"], 0)
        per_line[plan["line"]] = position + 1
        number = position // config.GENERATOR_PARTS_PER_BATCH + 1
        plan["batch"] = config.BATCH_TEMPLATE.format(seed=seed, line=plan["line"], number=number)
        plan["serial"] = config.SERIAL_TEMPLATE.format(seed=seed, index=plan["index"])
    return plans


# --- Rendering ----------------------------------------------------------------------

def plate_mask(size: tuple[int, int]) -> Image.Image:
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rectangle([PLATE.x0, PLATE.y0, PLATE.x1 - 1, PLATE.y1 - 1], fill=255)
    return mask


def lerp_color(a: tuple[int, ...], b: tuple[int, ...], t: float) -> tuple[int, ...]:
    return tuple(round(ca + (cb - ca) * t) for ca, cb in zip(a, b))


def render_base(rng: random.Random) -> Image.Image:
    size = (config.IMAGE_WIDTH_PX, config.IMAGE_HEIGHT_PX)
    image = Image.new("RGB", size, config.COLORS["conveyor"])
    draw = ImageDraw.Draw(image)
    for x in range(0, config.IMAGE_WIDTH_PX, config.CONVEYOR_STRIPE_SPACING_PX):
        draw.line([(x, 0), (x, config.IMAGE_HEIGHT_PX)], fill=config.COLORS["conveyor_stripe"])

    last_row = max(config.PLATE_HEIGHT_PX - 1, 1)
    for row in range(config.PLATE_HEIGHT_PX):
        color = lerp_color(config.COLORS["glass_top"], config.COLORS["glass_bottom"], row / last_row)
        y = PLATE.y0 + row
        draw.line([(PLATE.x0, y), (PLATE.x1 - 1, y)], fill=color)

    grain = (size[0] // config.NOISE_GRAIN_PX, size[1] // config.NOISE_GRAIN_PX)
    noise = Image.frombytes("L", grain, rng.randbytes(grain[0] * grain[1]))
    noise = noise.resize(size, Image.Resampling.BILINEAR).convert("RGB")
    image = Image.blend(image, noise, config.NOISE_BLEND)
    ImageDraw.Draw(image).rectangle(
        [PLATE.x0, PLATE.y0, PLATE.x1 - 1, PLATE.y1 - 1],
        outline=config.COLORS["glass_edge"],
        width=config.PLATE_EDGE_WIDTH_PX,
    )
    return image


def render(shapes: list[Shape], rng: random.Random) -> Image.Image:
    image = render_base(rng)
    # Reflections last, so they lie over the surface like a real glare.
    for shape in sorted(shapes, key=lambda s: isinstance(s, Reflection)):
        result = shape.draw(image)
        if result is not None:
            image = result
    return image


# --- Output -------------------------------------------------------------------------

def make_label(plan: dict, seed: int, shapes: list[Shape]) -> dict:
    return {
        "serial": plan["serial"],
        "seed": seed,
        "index": plan["index"],
        "line": plan["line"],
        "batch": plan["batch"],
        "image": f"{plan['serial']}.png",
        "width": config.IMAGE_WIDTH_PX,
        "height": config.IMAGE_HEIGHT_PX,
        "plate": {
            "x": int(PLATE.x0),
            "y": int(PLATE.y0),
            "w": config.PLATE_WIDTH_PX,
            "h": config.PLATE_HEIGHT_PX,
            "mm_per_px": config.MM_PER_PX,
        },
        "scenario": plan["scenario"],
        "defects": [
            {"defect_type": s.defect_type, **label_box(s.rect), "length_px": s.length_px} for s in shapes
        ],
    }


def remove_files(directory: Path, prefix: str, suffix: str) -> None:
    for path in directory.glob(f"{prefix}*{suffix}"):
        path.unlink()


def write_database(conn: sqlite3.Connection, seed: int, labels: list[dict]) -> None:
    serial_prefix = config.SERIAL_PREFIX_TEMPLATE.format(seed=seed)
    batch_prefix = config.BATCH_PREFIX_TEMPLATE.format(seed=seed)
    produced_on = date.today().isoformat()
    created_at = datetime.now().isoformat(timespec="seconds")
    with conn:
        conn.execute("DELETE FROM part WHERE serial LIKE ?", (serial_prefix + "%",))
        conn.execute("DELETE FROM batch WHERE code LIKE ?", (batch_prefix + "%",))
        line_ids = {row["code"]: row["id"] for row in conn.execute("SELECT id, code FROM production_line")}
        batch_ids: dict[str, int] = {}
        for label in labels:
            if label["batch"] not in batch_ids:
                cursor = conn.execute(
                    "INSERT INTO batch (code, line_id, produced_on) VALUES (?, ?, ?)",
                    (label["batch"], line_ids[label["line"]], produced_on),
                )
                batch_ids[label["batch"]] = cursor.lastrowid
            cursor = conn.execute(
                "INSERT INTO part (serial, batch_id, image_path, created_at) VALUES (?, ?, ?, ?)",
                (label["serial"], batch_ids[label["batch"]], f"images/{label['image']}", created_at),
            )
            part_id = cursor.lastrowid
            conn.executemany(
                "INSERT INTO gt_defect (part_id, defect_type, x, y, w, h, length_px) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (part_id, d["defect_type"], d["x"], d["y"], d["w"], d["h"], d["length_px"])
                    for d in label["defects"]
                ],
            )


def build_parts(count: int, seed: int):
    """Yield (plan, shapes, label) per part, without writing anything."""
    for plan in plan_parts(count, seed):
        shape_rng = random.Random(f"{seed}:{plan['index']}:shapes")
        shapes = build_shapes(shape_rng, plan["scenario"])
        yield plan, shapes, make_label(plan, seed, shapes)


def build_labels(count: int, seed: int) -> list[dict]:
    """Labels only, in memory (no images, no database)."""
    return [label for _, _, label in build_parts(count, seed)]


def generate(
    count: int,
    seed: int,
    data_dir: Path | None = None,
    db_path: Path | None = None,
) -> list[dict]:
    """Generate images, labels and database rows; returns the labels."""
    data_dir = Path(data_dir) if data_dir is not None else config.DATA_DIR
    image_dir = data_dir / config.IMAGE_DIR.name
    label_dir = data_dir / config.LABEL_DIR.name
    image_dir.mkdir(parents=True, exist_ok=True)
    label_dir.mkdir(parents=True, exist_ok=True)

    serial_prefix = config.SERIAL_PREFIX_TEMPLATE.format(seed=seed)
    remove_files(image_dir, serial_prefix, ".png")
    remove_files(label_dir, serial_prefix, ".json")

    labels = []
    for plan, shapes, label in build_parts(count, seed):
        render_rng = random.Random(f"{seed}:{plan['index']}:render")
        render(shapes, render_rng).save(image_dir / label["image"])
        (label_dir / f"{label['serial']}.json").write_text(
            json.dumps(label, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        labels.append(label)

    conn = db.connect(db_path)
    try:
        db.init_db(conn)
        write_database(conn, seed, labels)
    finally:
        conn.close()
    return labels


# --- CLI ----------------------------------------------------------------------------

def bounded_int(low: int, high: int):
    def parse(text: str) -> int:
        value = int(text)
        if not low <= value <= high:
            raise argparse.ArgumentTypeError(f"Wert muss zwischen {low} und {high} liegen")
        return value
    return parse


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Erzeugt künstliche Glas-Testbilder mit bekannten Fehlern.")
    parser.add_argument("--count", type=bounded_int(*config.GENERATOR_COUNT_RANGE), default=config.GENERATOR_DEFAULT_COUNT)
    parser.add_argument("--seed", type=bounded_int(*config.GENERATOR_SEED_RANGE), default=config.GENERATOR_DEFAULT_SEED)
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    labels = generate(args.count, args.seed)
    print(f"{len(labels)} Bilder erzeugt (Seed {args.seed}) in {config.IMAGE_DIR}")
    print(f"Labels in {config.LABEL_DIR}, Datenbank {config.DB_PATH}")
    counts: dict[str, int] = {}
    for label in labels:
        for defect in label["defects"]:
            counts[defect["defect_type"]] = counts.get(defect["defect_type"], 0) + 1
    clean = sum(1 for label in labels if not label["defects"])
    print(f"Fehlerfrei: {clean}")
    for defect_type, name in config.DEFECT_TYPES.items():
        print(f"{name}: {counts.get(defect_type, 0)} Markierungen")


if __name__ == "__main__":
    main()
