import importlib

from app import config


def test_config_loads():
    module = importlib.reload(config)
    assert module.APP_TITLE
    assert set(module.DEFECT_TYPES) == {"RISS", "KRATZER", "DRUCKSTELLE", "BLAESCHEN", "SPIEGELUNG"}
    assert module.NON_DEFECT_TYPES == {"SPIEGELUNG"}


def test_mm_per_px_is_derived_from_plate_width():
    assert config.PLATE_WIDTH_MM == 300
    assert config.MM_PER_PX == config.PLATE_WIDTH_MM / config.PLATE_WIDTH_PX


def test_plate_fits_into_image_with_consistent_aspect_ratio():
    x, y = config.PLATE_OFFSET_PX
    assert x >= 0 and y >= 0
    assert x + config.PLATE_WIDTH_PX <= config.IMAGE_WIDTH_PX
    assert y + config.PLATE_HEIGHT_PX <= config.IMAGE_HEIGHT_PX
    assert config.PLATE_HEIGHT_PX * config.MM_PER_PX == config.PLATE_HEIGHT_MM


def test_scratch_threshold_separates_short_and_long_scratches():
    longest_short_mm = config.SCRATCH_SHORT_PX_RANGE[1] * config.MM_PER_PX
    shortest_long_mm = config.SCRATCH_LONG_PX_RANGE[0] * config.MM_PER_PX
    assert longest_short_mm < config.SCRATCH_MAX_MM < shortest_long_mm


def test_rule_values():
    assert config.CONFIDENCE_MIN == 0.7
    assert config.BUBBLE_NOK_COUNT == 3


def test_mock_rates_are_probabilities():
    rates = [
        config.MOCK_RATE_REFLECTION_AS_SCRATCH,
        config.MOCK_RATE_MISSED_DEFECT,
        config.MOCK_RATE_TYPE_CONFUSION,
        config.MOCK_RATE_LOW_CONFIDENCE,
    ]
    assert all(0 <= rate <= 1 for rate in rates)
    low, high = config.MOCK_CONFIDENCE_RANGE
    assert config.CONFIDENCE_MIN < low < high <= 1
