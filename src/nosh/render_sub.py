"""Deterministic Markdown rendering of a T3 substitution: base recipe, verified evidence table, adapted recipe, caveats."""

from nosh.config import SUPPORT_SHOWN
from nosh.tasks.substitute import SubstituteResult
from nosh.tasks.verify import TargetEvidence

LABEL_REMINDER = ("Check product labels yourself: nosh does not claim any substitution is safe for an allergy or medical condition.")


def _evidence_block(ev: TargetEvidence) -> list[str]:
    s = ev.similar
    kind = "same dish" if s.level == "dish" else "broader dish type"
    lines = [f"### Evidence for replacing **{ev.target}**",
             f"- Similar dishes ({kind}, title query `{s.query}`): **{len(s.ids)}** recipes of {s.total_matching} matching; "
             f"**{len(s.with_ids)}** use {ev.target}, **{len(s.without_ids)}** do not"
             + (f", {len(s.unknown_ids)} point to another recipe and are in neither group" if s.unknown_ids else "")]
    if ev.classes:
        lines.append("- Widened classes: " + ", ".join(f"{c.name} ({c.size} recipes)" for c in ev.classes))
    for c in ev.candidates:
        lines.append(f"- **{c.name}** ({c.origin}): {c.grade.upper()} - {c.reason}")
        lines += [f"  - {b}" for b in c.basis]
        lines += [f"  - [{x.recipe_id}] {x.title}: `{x.raw_line}`" for x in c.support[:SUPPORT_SHOWN]]
    lines += [f"- Rejected: **{name}** ({why})" for name, why in ev.filtered]
    return lines


def _rejected(r: SubstituteResult) -> list[str]:
    out = []
    for i, errs in enumerate(r.rejected, 1):
        out += [f"- Attempt {i} was rejected by the validators:"] + [f"  - {e}" for e in errs]
    return out


def _adapted(r: SubstituteResult) -> list[str]:
    a = r.adapted
    lines = ["## Adapted recipe", f"Attempts needed: {r.attempts} (validators passed on the last one)"] + _rejected(r)
    if not a.can_adapt:
        return lines + [f"**Cannot adapt.** {a.uncertainty}"]
    for s in a.substitutions:
        lines.append(f"- **{s.original}** -> **{s.replacement or 'omit'}** (roles: {', '.join(s.role_covered) or 'n/a'})")
        lines += [f"  - evidence [{e.recipe_id}] ({e.strength}): {e.note}" for e in s.evidence if e.source == "local"]
        if not s.evidence:
            lines.append("  - predicted, no local evidence")
        lines.append(f"  - expected effects (predicted): {s.expected_effects}")
    lines += ["", "### Ingredients"] + [f"- {x}" for x in a.ingredients] + ["", "### Instructions"]
    lines += [f"{i}. {x}" for i, x in enumerate(a.instructions, 1)]
    if a.uncertainty:
        lines += ["", f"**Uncertainty:** {a.uncertainty}"]
    return lines


def substitute(r: SubstituteResult) -> str:
    if r.error:
        return r.error
    lines = [f"## Adapting [{r.base.id}] {r.base.title}"]
    if r.note:
        lines.append(r.note)
    lines.append("- Targets in this recipe: " + "; ".join(f"**{t}** (`{'`, `'.join(ls)}`)" for t, ls in r.targets.items()))
    if r.groups or r.dataset_only:
        lines.append("- Constraints: " + ", ".join([f"no {g}" for g in r.groups] + (["dataset ingredients only"] if r.dataset_only else [])))
    lines.append("- Roles (LLM-tagged): " + "; ".join(f"{t.ingredient}: {', '.join(t.roles)}" for t in r.roles.targets))
    lines.append("")
    for ev in r.evidence:
        lines += _evidence_block(ev) + [""]
    if r.adapted:
        lines += _adapted(r)
    else:
        lines += ["## No validated adaptation", f"I could not produce an adaptation that passes the checks after {r.attempts} attempts, so I am not showing one."]
        lines += _rejected(r)
    return "\n".join(lines + ["", LABEL_REMINDER])
