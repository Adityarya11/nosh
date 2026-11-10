"""Baseline: one plain LLM call (no retrieval) vs nosh on the garlic+onion question; writes evals/baseline.md."""

import sys
from pathlib import Path

from pydantic import BaseModel

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import checks  # noqa: E402

from nosh import llm  # noqa: E402
from nosh.chat import turn  # noqa: E402
from nosh.config import ANTHROPIC_MODEL, GEMINI_MODEL, LLM_PROVIDER, PHRASE_TEMPERATURE  # noqa: E402
from nosh.session import SessionState  # noqa: E402
from nosh.store import totals  # noqa: E402

QUESTION = "How many recipes use both garlic and onion, and what percent of the dataset is that?"
SYSTEM = ("You answer questions about the Recipe Box dataset (about 125,000 recipes scraped from Allrecipes, Epicurious and Food Network). "
          "Answer from your own knowledge only; no tools or data are available. Give a single best-guess count and percent.")


class OneShot(BaseModel):
    count: int
    percent: float
    explanation: str


def main() -> None:
    guess = llm.structured(SYSTEM, QUESTION, OneShot, PHRASE_TEMPERATURE)
    nosh_text = turn(QUESTION, SessionState())
    n, pct = checks.count_both("garlic", "onion")
    usable, dropped = totals()
    model = GEMINI_MODEL if LLM_PROVIDER == "gemini" else ANTHROPIC_MODEL
    lines = ["# Baseline: one-shot LLM vs nosh", "", f"Question: *{QUESTION}*", "",
             f"One-shot call: {LLM_PROVIDER} / {model}, no retrieval, temperature {PHRASE_TEMPERATURE}.", "",
             "## One-shot LLM answer", "", f"- Count: **{guess.count:,}**", f"- Percent: **{guess.percent}%**", f"- Its explanation: {guess.explanation}", "",
             "## nosh answer", "", nosh_text.split("\n", 1)[1], "",
             "## Independent check (plain Python over the ingredient table)", "",
             f"- Recipes with both garlic and onion: **{n:,}** ({pct:.2f}% of {usable:,} usable recipes; {dropped:,} records dropped at build)", "",
             "## Where the one-shot answer is wrong or ungrounded", "",
             f"- Its count differs from the real count by {abs(guess.count - n):,} ({abs(guess.count - n) / n:.0%} of the true value), and its percent by {abs(guess.percent - pct):.2f} points.",
             "- It cannot name the denominator (the number of usable recipes), the filters, or how ingredient names were normalized, so the number cannot be checked or reproduced.",
             "- nosh computes the count over the complete filtered set in SQL, states the denominator and the dropped-record count, lists which names counted as garlic and onion, and the count matches the independent recomputation."]
    (HERE / "baseline.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("wrote", HERE / "baseline.md")


if __name__ == "__main__":
    main()
