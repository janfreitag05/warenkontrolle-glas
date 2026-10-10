"""Central configuration: all thresholds, rules and paths live here (see docs/konzept.md)."""

from pathlib import Path

# --- Paths -------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
APP_DIR = BASE_DIR / "app"
DATA_DIR = BASE_DIR / "data"
IMAGE_DIR = DATA_DIR / "images"
LABEL_DIR = DATA_DIR / "labels"
DB_PATH = DATA_DIR / "warenkontrolle.db"
SCHEMA_PATH = APP_DIR / "schema.sql"
TEMPLATE_DIR = APP_DIR / "templates"
STATIC_DIR = APP_DIR / "static"

APP_TITLE = "Warenkontrolle Glas"
STATION_NAME = "Prüfplatz 1"

# --- Inspection station (steps 4 and 5) --------------------------------------------
STATION_INFEED_INTERVAL_S = 0.5   # cycle time of the automatic infeed (starts with the page)
STATION_COMMENT_MAX_LENGTH = 1000
HISTORY_PAGE_SIZE = 50            # rows per page in /station/history

# --- Demo session (tools/reset_demo.py) --------------------------------------------
# The station stream: the 60 demo parts (DEMO_SEED) spread evenly between defect-free
# filler parts (FILLER_SEED). Fillers = STATION_MAX_PARTS - demo parts. With 18 flagged
# demo parts (seed 42) and 450 parts in the stream, about 4 % land in "Zu prüfen".
DEMO_SEED = 42
FILLER_SEED = 900
STATION_MAX_PARTS = 450           # stream length; the infeed stops after this many parts
DEMO_PREINSPECTED = 150           # first stream parts inspected by the mock on reset
DEMO_PREINSPECTED_SPAN_MIN = 60   # their timestamps are spread over this many minutes

# --- History (tools/seed_history.py) -------------------------------------------------
HISTORY_SEED = 800                # serials and batches of history parts
HISTORY_POOL_SEED = 801           # own image pool, separate from the demo parts
HISTORY_POOL_SIZE = 200
HISTORY_POOL_DIRNAME = "history_pool"   # below DATA_DIR
HISTORY_DAYS = 30                 # days before today (today itself is not included)
# Per day about as many as one complete station run today (STATION_MAX_PARTS = 450).
HISTORY_PARTS_PER_DAY = {"L1": 180, "L2": 150, "L3": 120}
# 450 parts x 30 days exceed the 4-digit index of SERIAL_TEMPLATE; same prefix
# (SERIAL_PREFIX_TEMPLATE), 5-digit index.
HISTORY_SERIAL_TEMPLATE = "GT-{seed:03d}{index:05d}"

# --- Dashboard (step 6) ------------------------------------------------------------
# Periods include today: "7 Tage" = today and the 6 days before.
DASHBOARD_PERIODS = {"today": ("Heute", 1), "7d": ("7 Tage", 7), "30d": ("30 Tage", 30), "custom": ("Frei wählbar", None)}
DASHBOARD_DEFAULT_PERIOD = "30d"
HISTORY_DEFECT_RATE = {"L1": 0.03, "L2": 0.06, "L3": 0.04}   # share of defective parts (target NOK)
HISTORY_SHIFT_HOURS = (6, 22)     # inspections spread over this part of the day
HISTORY_REVIEW_DELAY_MIN = (2, 45)

# --- Fictitious master data ---------------------------------------------------
PRODUCTION_LINES = [
    ("L1", "Linie 1 – Flachglas"),
    ("L2", "Linie 2 – Flachglas"),
    ("L3", "Linie 3 – Sonderformat"),
]
PART_TYPE = "Glasplatte 300×200 mm"

# --- Catalogues ---------------------------------------------------------------
DEFECT_TYPES = {
    "RISS": "Riss",
    "KRATZER": "Kratzer",
    "DRUCKSTELLE": "Druckstelle",
    "BLAESCHEN": "Bläschen",
    "SPIEGELUNG": "Spiegeleffekt",
}
NON_DEFECT_TYPES = {"SPIEGELUNG"}

VERDICTS = {"OK": "OK", "NOK": "NOK", "MANUAL": "Manuelle Prüfung"}
SEVERITIES = {"NONE": "kein Fehler", "MINOR": "geringfügig", "MAJOR": "schwerwiegend", "CRITICAL": "kritisch"}

# --- Image geometry and scale --------------------------------------------------
IMAGE_WIDTH_PX = 800
IMAGE_HEIGHT_PX = 600
PLATE_WIDTH_MM = 300
PLATE_HEIGHT_MM = 200
PLATE_WIDTH_PX = 600
PLATE_HEIGHT_PX = 400
PLATE_OFFSET_PX = ((IMAGE_WIDTH_PX - PLATE_WIDTH_PX) // 2, (IMAGE_HEIGHT_PX - PLATE_HEIGHT_PX) // 2)

# Derived from the drawn plate width, never set freely.
MM_PER_PX = PLATE_WIDTH_MM / PLATE_WIDTH_PX

# Scratch length ranges used by the generator (pixels, inclusive).
SCRATCH_SHORT_PX_RANGE = (15, 60)
SCRATCH_LONG_PX_RANGE = (100, 250)

# --- Evaluation rules -----------------------------------------------------------
CONFIDENCE_MIN = 0.7          # below: detection is uncertain
SCRATCH_MAX_MM = 40.0         # scratch at or above this length -> MAJOR -> NOK
BUBBLE_NOK_COUNT = 3          # this many bubbles or more -> NOK

# Severity per defect type; KRATZER and BLAESCHEN depend on length / count.
SEVERITY_BY_TYPE = {
    "RISS": "CRITICAL",
    "DRUCKSTELLE": "MAJOR",
    "SPIEGELUNG": "NONE",
}

# --- Test image generator (step 2) ------------------------------------------------
GENERATOR_DEFAULT_COUNT = 60
GENERATOR_DEFAULT_SEED = 42
GENERATOR_SEED_RANGE = (0, 999)        # seed is part of the serial (3 digits)
GENERATOR_COUNT_RANGE = (1, 9999)      # index is part of the serial (4 digits)

# Serial and batch codes carry the seed, so a rerun replaces exactly its own data.
SERIAL_PREFIX_TEMPLATE = "GT-{seed:03d}"
SERIAL_TEMPLATE = SERIAL_PREFIX_TEMPLATE + "{index:04d}"
BATCH_PREFIX_TEMPLATE = "CH-{seed:03d}-"
BATCH_TEMPLATE = BATCH_PREFIX_TEMPLATE + "{line}-{number:02d}"
GENERATOR_PARTS_PER_BATCH = 20

# Share of parts per production line.
GENERATOR_LINE_SHARES = {"L1": 0.40, "L2": 0.35, "L3": 0.25}

# Defect profile per line (overall roughly 55 % clean, 15 % reflection only).
# defect_weights: relative frequency of real defect types on defective parts.
LINE_PROFILES = {
    "L1": {
        "share_clean": 0.60,
        "share_reflection_only": 0.15,
        "defect_weights": {"RISS": 1, "KRATZER": 2, "DRUCKSTELLE": 1, "BLAESCHEN": 2},
    },
    "L2": {  # noticeably more scratches
        "share_clean": 0.50,
        "share_reflection_only": 0.15,
        "defect_weights": {"RISS": 1, "KRATZER": 6, "DRUCKSTELLE": 1, "BLAESCHEN": 1},
    },
    "L3": {  # special format: more dents and bubbles
        "share_clean": 0.55,
        "share_reflection_only": 0.15,
        "defect_weights": {"RISS": 1, "KRATZER": 1, "DRUCKSTELLE": 3, "BLAESCHEN": 3},
    },
}

GENERATOR_DEFECTS_PER_PART = (1, 3)        # distinct real defect types per defective part
GENERATOR_SHARE_REFLECTION_ADDON = 0.25    # defective parts that additionally show a reflection
GENERATOR_SHARE_LONG_SCRATCH = 0.5         # otherwise short (see SCRATCH_*_PX_RANGE)
GENERATOR_SHARE_MANY_BUBBLES = 0.5         # many: >= BUBBLE_NOK_COUNT, few: below
GENERATOR_BUBBLES_MAX = 8

# Scenarios placed once at random positions (if count allows), so every run covers
# all defect types and both sides of the scratch and bubble rules.
GENERATOR_REQUIRED_SCENARIOS = [
    [],
    ["SPIEGELUNG"],
    ["RISS"],
    ["KRATZER_SHORT"],
    ["KRATZER_LONG"],
    ["DRUCKSTELLE"],
    ["BLAESCHEN_FEW"],
    ["BLAESCHEN_MANY"],
]

# Geometry (pixels)
GENERATOR_EDGE_MARGIN_PX = 12        # defects keep this distance from the plate edge
GENERATOR_BOX_PADDING_PX = 3
GENERATOR_PLACEMENT_ATTEMPTS = 30    # tries to place a defect without overlapping others
CRACK_LENGTH_PX_RANGE = (80, 300)
CRACK_STEP_PX_RANGE = (6, 14)
CRACK_MAX_TURN_DEG = 35
CRACK_BRANCHES = (1, 2)
CRACK_BRANCH_LENGTH_SHARE = (0.2, 0.4)
CRACK_LINE_WIDTH_PX = 2
CRACK_GLINT_OFFSET_PX = 1
SCRATCH_LINE_WIDTH_PX = 1
DENT_RADIUS_PX_RANGE = (10, 24)
DENT_ASPECT_RANGE = (0.6, 1.0)
DENT_RING_WIDTH_PX = 3
DENT_BLUR_PX = 4
BUBBLE_RADIUS_PX_RANGE = (3, 6)
BUBBLE_CLUSTER_RADIUS_PX = 45
BUBBLE_GAP_PX = 2
BUBBLE_GLINT_SHARE = 0.35            # glint radius relative to bubble radius
REFLECTION_WIDTH_PX_RANGE = (50, 110)
REFLECTION_ANGLE_DEG_RANGE = (25, 65)
REFLECTION_LENGTH_SHARE_RANGE = (0.5, 0.9)   # relative to the plate diagonal
REFLECTION_BLUR_PX = 14

# Appearance
CONVEYOR_STRIPE_SPACING_PX = 40
PLATE_EDGE_WIDTH_PX = 4
NOISE_BLEND = 0.04
NOISE_GRAIN_PX = 3                   # coarser grain keeps the PNG files small
DENT_ALPHA = 150
DENT_RING_ALPHA = 130
REFLECTION_ALPHA = 80                # broad and semi-transparent, unlike a sharp scratch
COLORS = {
    "conveyor": (44, 48, 52),
    "conveyor_stripe": (54, 58, 62),
    "glass_top": (208, 230, 228),
    "glass_bottom": (176, 206, 208),
    "glass_edge": (118, 148, 152),
    "crack_dark": (62, 78, 84),
    "crack_glint": (245, 250, 250),
    "scratch": (252, 254, 254),
    "dent_dark": (120, 142, 148),
    "dent_ring": (236, 244, 244),
    "bubble_fill": (222, 238, 238),
    "bubble_rim": (110, 136, 142),
    "bubble_glint": (255, 255, 255),
    "reflection": (255, 255, 255),
}

# --- Mock AI (step 3) ---------------------------------------------------------
DETECTOR_VERSION = "mock-0.1"
MOCK_SEED = 42
MOCK_CONFIDENCE_RANGE = (0.80, 0.99)          # clearly visible findings
MOCK_CONFIDENCE_RANGE_SUBTLE = (0.72, 0.90)   # bubbles, short scratches: subtle but still certain
MOCK_CONFIDENCE_RANGE_LOW = (0.45, 0.69)      # injected LOW_CONFIDENCE, always below CONFIDENCE_MIN
MOCK_CONFIDENCE_DECIMALS = 2
MOCK_SUBTLE_TYPES = {"BLAESCHEN"}             # plus scratches shorter than SCRATCH_MAX_MM
MOCK_BOX_JITTER_PX = 3

# Error rates, each relative to the defects it can affect:
#   reflection as scratch -> share of reflections
#   missed defect         -> share of real defects
#   type confusion        -> share of defects listed in MOCK_TYPE_CONFUSION
#   low confidence        -> share of the remaining (otherwise unaltered) detections
MOCK_RATE_REFLECTION_AS_SCRATCH = 0.05
MOCK_RATE_MISSED_DEFECT = 0.03
MOCK_RATE_TYPE_CONFUSION = 0.03
MOCK_RATE_LOW_CONFIDENCE = 0.08
MOCK_TYPE_CONFUSION = {"KRATZER": "RISS", "RISS": "KRATZER"}

# Required mock scenarios: when the mock knows the full list of parts, each scenario
# occurs once, on a part where the deviation actually changes the verdict.
# (injected_error, target verdict of the chosen part). No scenario for MISSED.
MOCK_REQUIRED_SCENARIOS = [
    ("FALSE_POSITIVE", "OK"),   # reflection reported as long scratch: OK -> NOK
    ("TYPE_CONFUSION", "OK"),   # short scratch reported as crack: OK -> NOK
    ("LOW_CONFIDENCE", "NOK"),  # real defect uncertain: NOK -> MANUAL
    ("LOW_CONFIDENCE", "OK"),   # harmless finding uncertain: OK -> MANUAL
]

# Codes stored in detection.injected_error (never shown in the inspection UI).
INJECTED_ERRORS = {
    "FALSE_POSITIVE": "Spiegelung als Kratzer gemeldet",
    "MISSED": "Fehler übersehen",
    "TYPE_CONFUSION": "Fehlertyp verwechselt",
    "LOW_CONFIDENCE": "Konfidenz gedrückt",
}
