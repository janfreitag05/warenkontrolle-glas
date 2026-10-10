import json

import pytest
from PIL import Image

from app import config, db
from tools import generate_images as gen

COUNT = 60
SEED = 42


def read_labels(data_dir):
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((data_dir / "labels").glob("*.json"))
    ]


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("data")
    db_path = data_dir / "test.db"
    labels = gen.generate(COUNT, SEED, data_dir=data_dir, db_path=db_path)
    return {"data_dir": data_dir, "db_path": db_path, "labels": labels}


@pytest.fixture
def conn(run):
    connection = db.connect(run["db_path"])
    yield connection
    connection.close()


def all_defects(labels):
    return [d for label in labels for d in label["defects"]]


# --- Files and labels ---------------------------------------------------------------

def test_files_written(run):
    images = sorted((run["data_dir"] / "images").glob("*.png"))
    labels = sorted((run["data_dir"] / "labels").glob("*.json"))
    assert len(images) == COUNT
    assert len(labels) == COUNT
    assert [p.stem for p in images] == [p.stem for p in labels]
    with Image.open(images[0]) as image:
        assert image.size == (config.IMAGE_WIDTH_PX, config.IMAGE_HEIGHT_PX)


def test_json_labels_match_returned_labels(run):
    assert read_labels(run["data_dir"]) == sorted(run["labels"], key=lambda l: l["serial"])


def test_same_seed_gives_identical_labels(run, tmp_path):
    gen.generate(COUNT, SEED, data_dir=tmp_path, db_path=tmp_path / "again.db")
    assert read_labels(tmp_path) == read_labels(run["data_dir"])


def test_same_seed_gives_identical_images(run, tmp_path):
    gen.generate(3, SEED, data_dir=tmp_path, db_path=tmp_path / "img.db")
    for path in sorted((tmp_path / "images").glob("*.png")):
        with Image.open(path) as a, Image.open(run["data_dir"] / "images" / path.name) as b:
            assert a.tobytes() == b.tobytes()


def test_other_seed_gives_other_labels(run, tmp_path):
    other = gen.generate(COUNT, SEED + 1, data_dir=tmp_path, db_path=tmp_path / "other.db")
    assert [l["defects"] for l in other] != [l["defects"] for l in run["labels"]]
    assert {l["serial"] for l in other}.isdisjoint({l["serial"] for l in run["labels"]})


def test_all_boxes_inside_plate(run):
    x0, y0 = config.PLATE_OFFSET_PX
    x1, y1 = x0 + config.PLATE_WIDTH_PX, y0 + config.PLATE_HEIGHT_PX
    for d in all_defects(run["labels"]):
        assert d["w"] > 0 and d["h"] > 0
        assert x0 <= d["x"] and d["x"] + d["w"] <= x1, d
        assert y0 <= d["y"] and d["y"] + d["h"] <= y1, d


def test_all_five_defect_types_occur(run):
    types = {d["defect_type"] for d in all_defects(run["labels"])}
    assert types == set(config.DEFECT_TYPES)


def test_clean_and_reflection_only_parts_occur(run):
    kinds = [{d["defect_type"] for d in label["defects"]} for label in run["labels"]]
    assert set() in kinds
    assert {"SPIEGELUNG"} in kinds


def test_scratch_lengths_within_config_ranges(run):
    short_lo, short_hi = config.SCRATCH_SHORT_PX_RANGE
    long_lo, long_hi = config.SCRATCH_LONG_PX_RANGE
    lengths = [d["length_px"] for d in all_defects(run["labels"]) if d["defect_type"] == "KRATZER"]
    assert lengths
    for length in lengths:
        assert short_lo <= length <= short_hi or long_lo <= length <= long_hi, length
    assert any(length <= short_hi for length in lengths)
    assert any(length >= long_lo for length in lengths)


def test_bubble_counts_on_both_sides_of_rule(run):
    counts = [
        sum(1 for d in label["defects"] if d["defect_type"] == "BLAESCHEN")
        for label in run["labels"]
    ]
    with_bubbles = [c for c in counts if c > 0]
    assert any(c < config.BUBBLE_NOK_COUNT for c in with_bubbles)
    assert any(c >= config.BUBBLE_NOK_COUNT for c in with_bubbles)
    assert max(with_bubbles) <= config.GENERATOR_BUBBLES_MAX


def test_reflection_is_broad_unlike_scratch(run):
    reflections = [d for d in all_defects(run["labels"]) if d["defect_type"] == "SPIEGELUNG"]
    assert reflections
    assert min(max(d["w"], d["h"]) for d in reflections) >= config.REFLECTION_WIDTH_PX_RANGE[0]


def test_all_lines_used(run):
    assert {label["line"] for label in run["labels"]} == set(config.GENERATOR_LINE_SHARES)


# --- Config consistency -------------------------------------------------------------

def test_line_profiles_cover_lines_and_defect_types():
    real_defects = set(config.DEFECT_TYPES) - config.NON_DEFECT_TYPES
    assert set(config.LINE_PROFILES) == {code for code, _ in config.PRODUCTION_LINES}
    assert set(config.GENERATOR_LINE_SHARES) == set(config.LINE_PROFILES)
    for profile in config.LINE_PROFILES.values():
        assert set(profile["defect_weights"]) == real_defects
        assert profile["share_clean"] + profile["share_reflection_only"] < 1


def test_line_profiles_differ():
    weights = [tuple(sorted(p["defect_weights"].items())) for p in config.LINE_PROFILES.values()]
    assert len(set(weights)) == len(weights)


def test_required_scenarios_use_known_variants():
    for scenario in config.GENERATOR_REQUIRED_SCENARIOS:
        assert set(scenario) <= set(gen.FACTORIES)


def test_bubble_maximum_above_rule():
    assert config.GENERATOR_BUBBLES_MAX >= config.BUBBLE_NOK_COUNT


# --- Database -----------------------------------------------------------------------

def gt_rows_from_db(conn):
    rows = conn.execute(
        "SELECT p.serial, g.defect_type, g.x, g.y, g.w, g.h, g.length_px "
        "FROM gt_defect g JOIN part p ON p.id = g.part_id"
    ).fetchall()
    return sorted(tuple(row) for row in rows)


def gt_rows_from_labels(labels):
    return sorted(
        (l["serial"], d["defect_type"], d["x"], d["y"], d["w"], d["h"], d["length_px"])
        for l in labels
        for d in l["defects"]
    )


def test_db_part_count(conn):
    assert conn.execute("SELECT COUNT(*) FROM part").fetchone()[0] == COUNT


def test_db_gt_defects_match_json(run, conn):
    assert gt_rows_from_db(conn) == gt_rows_from_labels(read_labels(run["data_dir"]))


def test_db_parts_linked_to_batches_of_their_line(run, conn):
    rows = conn.execute(
        "SELECT p.serial, b.code, l.code AS line FROM part p "
        "JOIN batch b ON b.id = p.batch_id JOIN production_line l ON l.id = b.line_id"
    ).fetchall()
    expected = {l["serial"]: (l["batch"], l["line"]) for l in run["labels"]}
    assert {r["serial"]: (r["code"], r["line"]) for r in rows} == expected


def test_rerun_replaces_instead_of_duplicating(tmp_path):
    db_path = tmp_path / "rerun.db"
    gen.generate(COUNT, SEED, data_dir=tmp_path, db_path=db_path)
    gen.generate(COUNT, SEED, data_dir=tmp_path, db_path=db_path)
    conn = db.connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM part").fetchone()[0] == COUNT
        assert gt_rows_from_db(conn) == gt_rows_from_labels(read_labels(tmp_path))
        batches = conn.execute("SELECT COUNT(*) FROM batch").fetchone()[0]
        assert batches == len({l["batch"] for l in read_labels(tmp_path)})
    finally:
        conn.close()
    assert len(list((tmp_path / "images").glob("*.png"))) == COUNT


def test_other_seed_keeps_existing_data(tmp_path):
    db_path = tmp_path / "two.db"
    gen.generate(10, SEED, data_dir=tmp_path, db_path=db_path)
    gen.generate(5, SEED + 1, data_dir=tmp_path, db_path=db_path)
    conn = db.connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM part").fetchone()[0] == 15
    finally:
        conn.close()


def test_cli_rejects_seed_out_of_range():
    with pytest.raises(SystemExit):
        gen.main(["--seed", str(config.GENERATOR_SEED_RANGE[1] + 1)])
