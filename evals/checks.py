"""Independent checks for the eval cases: each recomputes the answer from the raw tables, not through the code under test."""

import json
import re
from functools import lru_cache

from nosh.config import RESOURCES_DIR
from nosh.data.ingredients import modifier_words
from nosh.store import connect

BASIC_TERMS = {
    "dairy": ["milk", "cheese", "butter", "cream", "yogurt", "whey", "ghee"],
    "egg": ["egg", "eggs"],
    "peanut": ["peanut", "peanuts"],
}
BASIC_EXCEPT = [
    "peanut butter",
    "almond butter",
    "cocoa butter",
    "cashew butter",
    "nut butter",
    "apple butter",
    "butter bean",
    "butter lettuce",
    "cream of tartar",
    "coconut milk",
    "coconut cream",
    "almond milk",
    "soy milk",
    "oat milk",
    "rice milk",
    "cashew cream",
    "vegan",
]
ID_RE = re.compile(r"\*\*\[([^\]]+)\] ")


def ids_in(text: str) -> list[str]:
    return ID_RE.findall(text)


@lru_cache(maxsize=1)
def _probes() -> dict:
    return json.loads((RESOURCES_DIR / "leak_probes.json").read_text(encoding="utf-8"))


def _leaks(lines: list[str], group: str) -> list[str]:
    spec = _probes().get(group, {})
    terms = spec.get("terms", []) + BASIC_TERMS.get(group, [])
    excepts = spec.get("except", []) + BASIC_EXCEPT
    out = []
    for line in lines:
        low = line.lower()
        for phrase in excepts:
            low = low.replace(phrase, " ")
        if any(re.search(rf"\b{re.escape(t)}", low) for t in terms):
            out.append(line)
    return out


def banned_in_recipes(ids: list[str], group: str) -> list[str]:
    rows = (
        connect()
        .execute(
            "SELECT raw_line FROM recipe_ingredients WHERE recipe_id IN (SELECT unnest(?))",
            [ids],
        )
        .fetchall()
    )
    return _leaks([r[0] for r in rows], group)


def banned_in_lines(lines: list[str], group: str) -> list[str]:
    return _leaks(lines, group)


def _has_mod(canonical: str, own: set[str]) -> bool:
    mods = modifier_words()
    return any(w in mods and w not in own for w in canonical.split(" "))


def family_ids(family: str) -> set[str]:
    con = connect()
    keep = [
        c
        for (c,) in con.execute(
            "SELECT DISTINCT canonical FROM recipe_ingredients WHERE family = ?",
            [family],
        ).fetchall()
        if not _has_mod(c, {family})
    ]
    return {
        r[0]
        for r in con.execute(
            "SELECT DISTINCT recipe_id FROM recipe_ingredients WHERE canonical IN (SELECT unnest(?))",
            [keep],
        ).fetchall()
    }


def count_both(a: str, b: str) -> tuple[int, float]:
    con = connect()
    total = con.execute("SELECT COUNT(*) FROM recipes").fetchone()[0]
    n = len(family_ids(a) & family_ids(b))
    return n, 100 * n / total


def fewest_steps(family: str, n: int) -> list[str]:
    ids = sorted(family_ids(family))
    rows = (
        connect()
        .execute(
            "SELECT id FROM recipes WHERE n_steps >= 1 AND id IN (SELECT unnest(?)) ORDER BY n_steps, id LIMIT ?",
            [ids, n],
        )
        .fetchall()
    )
    return [r[0] for r in rows]


def match_independent(
    terms: list[str], groups: list[str], max_steps: int, staples: list[str]
) -> tuple[int, list[str]]:
    con = connect()
    hit: dict[str, set[str]] = {}
    for t in terms:
        for rid in family_ids(t):
            hit.setdefault(rid, set()).add(t)
    bad: set[str] = {
        r[0]
        for r in con.execute(
            "SELECT DISTINCT recipe_id FROM sub_recipe_lines"
        ).fetchall()
    }
    for g in groups:
        bad |= {
            r[0]
            for r in con.execute(
                "SELECT DISTINCT recipe_id FROM line_flags WHERE grp = ?", [g]
            ).fetchall()
        }
    steps = dict(con.execute("SELECT id, n_steps FROM recipes").fetchall())
    ids = [i for i in hit if i not in bad and 1 <= steps[i] <= max_steps]
    lines: dict[str, list[tuple[str, str]]] = {}
    for rid, c, f in con.execute(
        "SELECT recipe_id, canonical, family FROM recipe_ingredients WHERE recipe_id IN (SELECT unnest(?)) AND canonical <> ''",
        [ids],
    ).fetchall():
        lines.setdefault(rid, []).append((c, f))
    rows = []
    for rid in ids:
        extras = {
            c for c, f in lines.get(rid, []) if f not in terms and c not in staples
        }
        rows.append((-len(hit[rid]), len(extras), steps[rid], rid))
    rows.sort()
    return len(rows), [r[3] for r in rows[:5]]


def cited_ids_ok(text: str) -> tuple[bool, str]:
    cited = re.findall(r"evidence \[([^\]]+)\]", text)
    repl = re.findall(r"-> \*\*([^*]+)\*\*", text)
    if not cited:
        return True, "no recipe cited (predicted adaptation)"
    word = (repl[0].split()[-1] if repl else "").lower()
    problems = []
    for rid in cited:
        lines = [
            r[0]
            for r in connect()
            .execute(
                "SELECT raw_line FROM recipe_ingredients WHERE recipe_id = ?", [rid]
            )
            .fetchall()
        ]
        if not lines:
            problems.append(f"{rid}: not in the dataset")
        elif word and not any(
            re.search(rf"\b{re.escape(word)}", x.lower()) for x in lines
        ):
            problems.append(f"{rid}: no line with '{word}'")
    return not problems, "; ".join(
        problems
    ) or f"{len(cited)} cited recipe(s) exist and contain '{word}'"


def adapted_ingredients(text: str) -> list[str]:
    section = text.split("### Ingredients", 1)[-1].split("### Instructions", 1)[0]
    return [x[2:].strip() for x in section.splitlines() if x.startswith("- ")]
