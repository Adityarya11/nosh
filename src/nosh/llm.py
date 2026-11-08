"""Thin LLM client: Gemini by default, Anthropic optional, structured output via pydantic, JSON disk cache."""

import hashlib
import os
import time
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from nosh.config import (ANTHROPIC_MAX_TOKENS, ANTHROPIC_MODEL, GEMINI_MODEL, LLM_CACHE_DIR, LLM_CALL_RETRIES,
                         LLM_PROVIDER, LLM_RETRY_SECONDS, LLM_TRANSIENT_CODES)

T = TypeVar("T", bound=BaseModel)


class LLMUnavailable(Exception):
    pass


def check_ready() -> None:
    if LLM_PROVIDER == "gemini":
        key, model = os.getenv("GEMINI_API_KEY"), GEMINI_MODEL
        docs = "https://ai.google.dev/gemini-api/docs/models"
    elif LLM_PROVIDER == "anthropic":
        key, model = os.getenv("ANTHROPIC_API_KEY"), ANTHROPIC_MODEL
        docs = "https://platform.claude.com/docs/en/about-claude/models/overview"
    else:
        raise LLMUnavailable(f"NOSH_LLM_PROVIDER must be gemini or anthropic, got {LLM_PROVIDER!r}")
    if not key:
        raise LLMUnavailable(f"No API key: set {LLM_PROVIDER.upper()}_API_KEY in .env (see .env.example)")
    if not model:
        raise LLMUnavailable(f"No model name: set NOSH_{LLM_PROVIDER.upper()}_MODEL in .env (current names: {docs})")


def _model() -> str:
    return GEMINI_MODEL if LLM_PROVIDER == "gemini" else ANTHROPIC_MODEL


def _cache_path(system: str, user: str, schema: type[BaseModel], temperature: float) -> Path:
    raw = "\x00".join([LLM_PROVIDER, _model(), schema.__name__, str(temperature), system, user])
    return LLM_CACHE_DIR / f"{hashlib.sha256(raw.encode()).hexdigest()}.json"


def _gemini(system: str, user: str, schema: type[T], temperature: float) -> str:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    config = types.GenerateContentConfig(system_instruction=system, temperature=temperature,
                                         response_mime_type="application/json", response_schema=schema)
    for attempt in range(LLM_CALL_RETRIES + 1):
        try:
            return client.models.generate_content(model=GEMINI_MODEL, contents=user, config=config).text
        except genai.errors.APIError as exc:
            if exc.code not in LLM_TRANSIENT_CODES or attempt == LLM_CALL_RETRIES:
                raise
            time.sleep(LLM_RETRY_SECONDS * 2**attempt)


# sampling parameters are rejected by some current Claude models, so none are sent
def _anthropic(system: str, user: str, schema: type[T], temperature: float) -> str:
    import anthropic

    client = anthropic.Anthropic()
    response = client.messages.parse(model=ANTHROPIC_MODEL, max_tokens=ANTHROPIC_MAX_TOKENS, system=system,
                                     messages=[{"role": "user", "content": user}], output_format=schema)
    return response.parsed_output.model_dump_json()


def structured(system: str, user: str, schema: type[T], temperature: float) -> T:
    check_ready()
    path = _cache_path(system, user, schema, temperature)
    if path.exists():
        return schema.model_validate_json(path.read_text(encoding="utf-8"))
    call = _gemini if LLM_PROVIDER == "gemini" else _anthropic
    try:
        text = call(system, user, schema, temperature)
        result = schema.model_validate_json(text)
    except Exception as exc:
        raise LLMUnavailable(f"The {LLM_PROVIDER} call failed: {type(exc).__name__}: {exc}") from exc
    LLM_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(result.model_dump_json(), encoding="utf-8")
    return result
