"""All tunable settings: paths, thresholds, retry counts, temperatures, LLM model names."""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")
RAW_DIR = ROOT / "recipes_raw"
DATA_DIR = ROOT / "data"
RESOURCES_DIR = ROOT / "resources"
PROMPTS_DIR = Path(__file__).parent / "prompts"
DB_PATH = DATA_DIR / "nosh.duckdb"
LLM_CACHE_DIR = DATA_DIR / "llm_cache"

RAW_FILES = {
    "ar": "recipes_raw_nosource_ar.json",
    "epi": "recipes_raw_nosource_epi.json",
    "fn": "recipes_raw_nosource_fn.json",
}
DATASET_URL = "https://storage.googleapis.com/recipe-box/recipes_raw.zip"

STEP_RULE = "v2"
MAP_VERSION = "v1"
UNTITLED = "(untitled)"

SEARCH_TOP_K = 5
# Initial values from probing real titles; re-tune on evals/cases.yaml (step 8) and record in docs/decisions.md
COVERAGE_EXACT_MIN = 0.99
COVERAGE_NEAR_MIN = 0.70

# SIMILAR_N is large enough that the WITHOUT group is not starved for common dishes (D24)
SIMILAR_N = 200
MIN_SIMILAR = 30
MIN_SUPPORT = 3
MIN_LIFT = 2.0
CONTRAST_TOP = 8
EXPLICIT_OR_STOP = ["to taste", "more", "less", "as needed", "as desired", "if desired", "as required", "to garnish", "to serve",
                    "extra", "additional", "as necessary", "optional"]
SUBSTITUTION_CUES = r"substitut(?:e|ed|es|ing|ion)\s+(?:for|with|in)\b|substituting|substitution|instead of|in place of|replac(?:e|ing)\b"
TEXT_MENTIONS_SHOWN = 5
MAX_RETRIES = 2

SLOTS_TEMPERATURE = 0.0
ROLES_TEMPERATURE = 0.0
ADAPT_TEMPERATURE = 0.2
PHRASE_TEMPERATURE = 0.0
HISTORY_TURNS = 6

UNSAFE_CLAIM_PHRASES = [
    "allergy safe",
    "allergy-safe",
    "medically safe",
    "safe for people with",
    "safe for allergy",
    "guaranteed safe",
    "hypoallergenic",
]
SUPPORTED_ATTRIBUTES = ["ingredients", "instructions", "title", "steps"]

LLM_PROVIDER = os.getenv("NOSH_LLM_PROVIDER", "gemini")
# Model names are read from env so they can be updated without code changes; set them in .env
GEMINI_MODEL = os.getenv("NOSH_GEMINI_MODEL", "")
ANTHROPIC_MODEL = os.getenv("NOSH_ANTHROPIC_MODEL", "")
ANTHROPIC_MAX_TOKENS = 4096
# transient provider errors (overload, rate limit) are retried with a doubling wait
LLM_TRANSIENT_CODES = [429, 500, 503, 504]
LLM_CALL_RETRIES = 3
LLM_RETRY_SECONDS = 2.0

STEP_BOILERPLATE_EXACT = ["watch how to make this recipe."]
STEP_BOILERPLATE_CONTAINS = ["©", "copyright", "all rights reserved"]
STEP_BOILERPLATE_PREFIX = ["recipe courtesy of", "photograph by", "photography by", "photo by"]
HEADER_MAX_WORDS = 3
MAP_COVERAGE_TARGET = 0.85

MIN_RANKABLE_STEPS = 1
STATS_TOP_N = 5
STATS_NOTE_TOP = 6
LEAK_REPORT_TOP = 6

# lines that point to another recipe hide its ingredients, so they count as "may contain" for every restriction
SUB_RECIPE_PHRASES = ["recipe follows", "recipe to follow", "recipe below", "see recipe", "see below"]
FUZZY_MIN_RATIO = 85
FUZZY_MAX_LEN_DIFF = 3

# search and coverage probe: coverage = share of query words found in the best candidate title
SEARCH_POOL = 50
PROBE_POOL = 20
TOKEN_MATCH_MIN = 85
QUERY_STOPWORDS = ["a", "an", "and", "the", "of", "in", "for", "with", "without", "recipe", "recipes", "some", "any"]
NEAREST_TITLES = 3
ORDINALS = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4, "fifth": 5, "5th": 5}
EVIDENCE_LINES = 2
# typo tolerance for restriction words: only words this long, and Damerau-Levenshtein similarity at least this high
ALIAS_FUZZY_MIN_LEN = 5
ALIAS_FUZZY_MIN_SIM = 0.8
NEGATION_PREFIXES = ["no", "without", "exclude", "excluding"]
# recipes with no instruction steps sort after every recipe that has steps when "fewer steps" is a sort key
STEP_SORT_LAST = 1_000_000

# T2 ranking: preference words move a key to the front; default key order is matched, extras, steps, ID
MATCH_PREF_KEYS = {"step": "steps_key", "extra": "extra_n"}
MATCH_TOP_K = 5
