"""Eval runner: replays evals/cases.yaml through the real chat turn (LLM calls replay from the disk cache) and writes evals/results.md."""

import json
import re
import sys
from datetime import date
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import checks  # noqa: E402

from nosh.chat import turn  # noqa: E402
from nosh.config import ANTHROPIC_MODEL, GEMINI_MODEL, LLM_PROVIDER, RESOURCES_DIR  # noqa: E402
from nosh.router.actions import Search  # noqa: E402
from nosh.router.decide import decide  # noqa: E402
from nosh.router.slots import Slots  # noqa: E402
from nosh.session import SessionState  # noqa: E402
from nosh.tasks.search import Coverage, Hit  # noqa: E402

Result = tuple[bool, str]
STAPLES = json.loads((RESOURCES_DIR / "pantry.json").read_text(encoding="utf-8"))["canonicals"]


def _stats_count(text: str, p: dict) -> Result:
    m = re.search(r"([\d,]+) recipes match \(([\d.]+)%", text)
    if not m:
        return False, "no count found in the answer"
    n, pct = int(m.group(1).replace(",", "")), float(m.group(2))
    want_n, want_pct = checks.count_both(p["a"], p["b"])
    return n == want_n and abs(pct - want_pct) < 0.006, f"answer {n:,} ({pct}%); independent {want_n:,} ({want_pct:.2f}%)"


def _match_independent(text: str, p: dict) -> Result:
    m = re.search(r"Candidates:\*{0,2} ([\d,]+) recipes", text)
    count, top5 = checks.match_independent(p["terms"], p["groups"], p["max_steps"], STAPLES)
    got = int(m.group(1).replace(",", "")) if m else -1
    ids = checks.ids_in(text)[:5]
    return got == count and ids == top5, f"candidates answer {got:,} vs independent {count:,}; top-5 IDs equal: {ids == top5}"


def _t3_validated(text: str) -> Result:
    if "validators passed" in text:
        return True, "adaptation passed the validators"
    return False, "no validated adaptation (honest refusal or error): " + ("refused after retries" if "No validated adaptation" in text else text.splitlines()[-1][:120])


def run_check(check: dict, text: str, session: SessionState) -> Result:
    kind = check["kind"]
    if kind == "route":
        got = re.search(r"task: (\w+)", text.splitlines()[0])
        return bool(got) and got.group(1) == check["expect"], f"route {got.group(1) if got else None}, expected {check['expect']}"
    if kind == "contains":
        return check["text"] in text, f"text {'found' if check['text'] in text else 'MISSING'}: {check['text']!r}"
    if kind == "no_banned":
        bad = checks.banned_in_recipes(session.last_results, check["group"])
        return not bad, f"{len(session.last_results)} result recipes scanned for {check['group']}: {len(bad)} suspicious line(s) {bad[:3]}"
    if kind == "stats_count":
        return _stats_count(text, check)
    if kind == "ranked_ids":
        want = checks.fewest_steps(check["family"], check["n"])
        return checks.ids_in(text)[:check["n"]] == want, f"answer {checks.ids_in(text)[:check['n']]} vs independent {want}"
    if kind == "match_independent":
        return _match_independent(text, check)
    if kind == "t3_validated":
        return _t3_validated(text)
    if kind == "cited_ids":
        return checks.cited_ids_ok(text)
    if kind == "adapted_no_banned":
        bad = checks.banned_in_lines(checks.adapted_ingredients(text), check["group"])
        return not bad, f"{len(checks.adapted_ingredients(text))} adapted ingredient lines scanned for {check['group']}: {bad[:3] or 'none found'}"
    return False, f"unknown check kind {kind}"


def run_conversation(case: dict) -> list[dict]:
    session, out = SessionState(), []
    for t in case["turns"]:
        try:
            text = turn(t["say"], session)
        except Exception as exc:  # an eval failure must be reported, not crash the run
            out.append({"turn": t["say"], "check": "turn", "ok": False, "detail": f"{type(exc).__name__}: {exc}"[:300]})
            break
        for c in t.get("checks", []):
            ok, detail = run_check(c, text, session)
            out.append({"turn": t["say"], "check": c["kind"], "ok": ok, "detail": detail})
    return out


def run_routing(case: dict) -> dict:
    s = case["session"] if "session" in case else {}
    session = SessionState(mode=s.get("mode", "auto"))
    if s.get("pending_offer"):
        from nosh.router.actions import Substitute
        session.pending_offer = Substitute(recipe_id="x", goal="y")
    if s.get("active_search"):
        session.active_action = Search(dish="penne")
    level = case.get("coverage")
    cov = Coverage(level=level, hits=[Hit(id="id1", title="Nearest", score=0.5)]) if level else None
    action = decide(Slots(**case["slots"]), cov, session)
    return {"id": case["id"], "branch": case["branch"], "ok": action.kind == case["expect"], "got": action.kind, "expect": case["expect"]}


def write_results(convs: list, routing: list, limits: list[str], path: Path) -> None:
    model = GEMINI_MODEL if LLM_PROVIDER == "gemini" else ANTHROPIC_MODEL
    lines = ["# Eval results", "", f"Run {date.today()} with {LLM_PROVIDER} / {model}. Conversations replay from the LLM disk cache; every check is deterministic "
             "(independent SQL/Python, text, route).", "", "## Summary", "", "| Case | Checks passed | Status |", "|---|---|---|"]
    worked, failed = [], []
    for case, results in convs:
        n_ok = sum(r["ok"] for r in results)
        status = "PASS" if n_ok == len(results) else "FAIL"
        (worked if status == "PASS" else failed).append((case, results))
        lines.append(f"| {case['id']} | {n_ok}/{len(results)} | {status} |")
    r_ok = sum(r["ok"] for r in routing)
    lines.append(f"| routing (decide branches 1-8 plus pinned mode) | {r_ok}/{len(routing)} | {'PASS' if r_ok == len(routing) else 'FAIL'} |")
    lines += ["", "## What worked", ""]
    for case, results in worked:
        lines.append(f"- **{case['title']}**: {case['note']}")
        lines += [f"  - {r['check']}: {r['detail']}" for r in results]
    lines += ["", "## What failed, and why", ""]
    if not failed and r_ok == len(routing):
        lines.append("Nothing failed in this run.")
    for case, results in failed:
        lines.append(f"- **{case['title']}**: {case['note']}")
        lines += [f"  - {'ok' if r['ok'] else 'FAILED'} {r['check']}: {r['detail']}" for r in results]
        if case.get("why"):
            lines.append(f"  - Why: {case['why']}")
    lines += ["", "## Routing cases (decide(), no LLM)", "", "| Case | Branch | Expected | Got | Status |", "|---|---|---|---|---|"]
    lines += [f"| {r['id']} | {r['branch']} | {r['expect']} | {r['got']} | {'PASS' if r['ok'] else 'FAIL'} |" for r in routing]
    lines += ["", "## Known limits these cases do not cover", ""] + [f"- {x}" for x in limits]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    spec = yaml.safe_load((HERE / "cases.yaml").read_text(encoding="utf-8"))
    convs = []
    for case in spec["conversations"]:
        print(f"running {case['id']} ...", flush=True)
        convs.append((case, run_conversation(case)))
    routing = [run_routing(c) for c in spec["routing"]]
    write_results(convs, routing, spec["limits"], HERE / "results.md")
    print(f"wrote {HERE / 'results.md'}")


if __name__ == "__main__":
    main()
