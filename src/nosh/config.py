"""All tunable settings: paths, thresholds, retry counts, temperatures, LLM model names."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
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

STEP_RULE = "v1"
MAP_VERSION = "v1"
UNTITLED = "(untitled)"

SEARCH_TOP_K = 5
# Placeholders: tune on evals/cases.yaml and record the final values in docs/decisions.md
COVERAGE_EXACT_MIN = 0.90
COVERAGE_NEAR_MIN = 0.60

SIMILAR_N = 50
MIN_SUPPORT = 3
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
