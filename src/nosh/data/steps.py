"""Step-counting rule v2: dedupe repeated text, drop boilerplate, split on newlines, fall back to sentences."""

import re

from nosh.config import (
    MIN_RANKABLE_STEPS,
    STEP_BOILERPLATE_CONTAINS,
    STEP_BOILERPLATE_EXACT,
    STEP_BOILERPLATE_PREFIX,
)

SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")


def _squash(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def _is_boilerplate(line: str, title: str) -> bool:
    low = line.lower()
    return (
        low in STEP_BOILERPLATE_EXACT
        or any(p in low for p in STEP_BOILERPLATE_CONTAINS)
        or any(low.startswith(p) for p in STEP_BOILERPLATE_PREFIX)
        or (bool(_squash(title)) and _squash(line) == _squash(title))
    )


def _dedupe_lines(lines: list[str]) -> list[str]:
    half, odd = divmod(len(lines), 2)
    if not odd and half and lines[:half] == lines[half:]:
        return lines[:half]
    return lines


def _dedupe_block(text: str) -> str:
    n = len(text)
    half = n // 2
    if n % 2 == 1 and text[:half] == text[half + 1 :] and text[half] == " ":
        return text[:half]
    return text


def split_steps(instructions: str, title: str = "") -> list[str]:
    lines = [" ".join(line.split()) for line in instructions.splitlines()]
    lines = _dedupe_lines([line for line in lines if line])
    # a lone block is filtered per sentence, otherwise one copyright line would drop the whole text
    kept = (
        lines
        if len(lines) == 1
        else [line for line in lines if not _is_boilerplate(line, title)]
    )
    if len(kept) == 1:
        sentences = SENTENCE_SPLIT.split(_dedupe_block(kept[0]))
        return [
            s.strip()
            for s in sentences
            if s.strip() and not _is_boilerplate(s.strip(), title)
        ]
    return kept


RANKABLE_SQL = f"n_steps >= {MIN_RANKABLE_STEPS}"


def no_instructions_note(count: int) -> str:
    return f"{count} recipes with no instruction steps (0 steps after boilerplate removal) excluded from step rankings"
