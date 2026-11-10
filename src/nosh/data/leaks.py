"""Per-restriction leak report for `nosh build`: flag counts, safe-list audit, and an independent probe-list scan."""

import json
from collections import Counter

import duckdb
import pandas as pd

from nosh.config import LEAK_REPORT_TOP, RESOURCES_DIR
from nosh.restrictions import (
    classify,
    constituents,
    keyword_inside_word,
    known_groups,
    pad,
    phrase_re,
)


def _top(counter: Counter) -> str:
    return (
        "; ".join(f"{name} ({n:,})" for name, n in counter.most_common(LEAK_REPORT_TOP))
        or "none"
    )


def _probe_hits(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    probes = json.loads(
        (RESOURCES_DIR / "leak_probes.json").read_text(encoding="utf-8")
    )
    regexes = {
        g: (phrase_re(p["terms"]), phrase_re(p["except"])) for g, p in probes.items()
    }
    rows = []
    for (line,) in con.execute(
        "SELECT DISTINCT raw_line FROM recipe_ingredients"
    ).fetchall():
        padded = pad(line)
        for group, (terms, excepts) in regexes.items():
            if m := terms.search(excepts.sub("", padded)):
                rows.append((line, group, m.group(1)))
    return pd.DataFrame(rows, columns=["raw_line", "grp", "term"])


# a probe hit counts as a leak when the line carries no flag from the group or any group it includes
def _probe_report(con: duckdb.DuckDBPyConnection) -> list[str]:
    hits = _probe_hits(con)
    con.register("probe_hits", hits)
    out = [
        "Independent probe scan (separate term list in leak_probes.json; counts are lines / recipes NOT flagged):"
    ]
    for group in known_groups():
        groups = ", ".join(f"'{g}'" for g in constituents(group))
        rows = con.execute(
            f"SELECT h.term, COUNT(*), COUNT(DISTINCT i.recipe_id) FROM probe_hits h "
            f"JOIN recipe_ingredients i ON i.raw_line = h.raw_line "
            f"WHERE h.grp = ? AND NOT EXISTS (SELECT 1 FROM line_flags f WHERE f.recipe_id = i.recipe_id "
            f"AND f.pos = i.pos AND f.grp IN ({groups})) GROUP BY h.term ORDER BY 2 DESC",
            [group],
        ).fetchall()
        total = con.execute(
            f"SELECT COUNT(*), COUNT(DISTINCT i.recipe_id) FROM probe_hits h JOIN recipe_ingredients i "
            f"ON i.raw_line = h.raw_line WHERE h.grp = ? AND NOT EXISTS (SELECT 1 FROM line_flags f "
            f"WHERE f.recipe_id = i.recipe_id AND f.pos = i.pos AND f.grp IN ({groups}))",
            [group],
        ).fetchone()
        top = (
            "; ".join(f"{t} ({n:,} / {r:,})" for t, n, r in rows[:LEAK_REPORT_TOP])
            or "none"
        )
        out.append(
            f"  {group}: {total[0]:,} lines / {total[1]:,} recipes unflagged. top: {top}"
        )
    return out


def _flag_report(con: duckdb.DuckDBPyConnection) -> list[str]:
    triples = con.execute(
        "SELECT name, canonical, family, COUNT(*) FROM recipe_ingredients GROUP BY ALL"
    ).fetchall()
    usable = con.execute("SELECT COUNT(*) FROM recipes").fetchone()[0]
    out = [
        "Restriction leak report (lines and recipes; flagged recipes = excluded under a hard constraint):"
    ]
    for group in known_groups():
        counts: Counter = Counter()
        safe_listed: Counter = Counter()
        leaks: Counter = Counter()
        for name, canonical, family, n in triples:
            level = classify(name, canonical, family, group)
            counts[level] += n
            if (
                level is None
                and classify(name, canonical, family, group, False) is not None
            ):
                safe_listed[canonical] += n
            elif level is None and keyword_inside_word(name, group):
                leaks[canonical] += n
        groups = ", ".join(f"'{g}'" for g in constituents(group))
        banned, anyflag = con.execute(
            f"SELECT COUNT(DISTINCT recipe_id) FILTER (WHERE level = 'banned'), COUNT(DISTINCT recipe_id) "
            f"FROM line_flags WHERE grp IN ({groups})"
        ).fetchone()
        out.append(
            f"  {group}: parsed-name lines banned {counts['banned']:,}, may-contain {counts['may_contain']:,}; "
            f"recipes with a banned line {banned:,} ({banned / usable:.1%}), "
            f"with any flag {anyflag:,} ({anyflag / usable:.1%}), may-contain only {anyflag - banned:,}"
        )
        out.append(f"      safe-listed (audit): {_top(safe_listed)}")
        out.append(
            f"      keyword inside longer word, not flagged (review): {_top(leaks)}"
        )
    return out


def leak_report(con: duckdb.DuckDBPyConnection) -> str:
    return "\n".join(_flag_report(con) + _probe_report(con))
