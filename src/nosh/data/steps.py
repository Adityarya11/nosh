"""Step-counting rule v1: dedupe repeated text, split on newlines, fall back to sentences."""

import re

from nosh.config import STEP_BOILERPLATE

SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")


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


def split_steps(instructions: str) -> list[str]:
    lines = [" ".join(line.split()) for line in instructions.splitlines()]
    lines = _dedupe_lines([line for line in lines if line])
    lines = [line for line in lines if line.lower() not in STEP_BOILERPLATE]
    if len(lines) == 1:
        block = _dedupe_block(lines[0])
        return [s.strip() for s in SENTENCE_SPLIT.split(block) if s.strip()]
    return lines
