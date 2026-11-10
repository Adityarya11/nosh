"""One-off: counts parsed ingredient names and writes resources/canonical_map.json (with group tags) from map_rules.json."""

import json
from collections import Counter

from nosh.config import MAP_COVERAGE_TARGET, RESOURCES_DIR
from nosh.data.ingredients import family_of, parse_line
from nosh.data.load import iter_lines, load_all
from nosh.restrictions import classify_by_keywords, known_groups


# tags freeze the keyword-list result; corrections found in review go into restrictions.json, then this script is re-run
def group_tags(name: str, canonical: str, family: str) -> dict[str, str]:
    return {
        g: level
        for g in known_groups()
        if (level := classify_by_keywords(name, canonical, family, g))
    }


def main() -> None:
    rules = json.loads((RESOURCES_DIR / "map_rules.json").read_text(encoding="utf-8"))
    recipes, _ = load_all()
    counts = Counter(parse_line(line).name for line in iter_lines(recipes))
    counts.pop("", None)
    total = sum(counts.values())
    mapping, covered = {}, 0
    for name, n in counts.most_common():
        if covered / total >= MAP_COVERAGE_TARGET:
            break
        canonical = rules["aliases"].get(name, name)
        family = family_of(canonical)
        entry = {"canonical": canonical, "family": family}
        if tags := group_tags(name, canonical, family):
            entry["groups"] = tags
        mapping[name] = entry
        covered += n
    for name, canonical in rules[
        "aliases"
    ].items():  # aliases are mapped even when the name is rare
        if name in counts and name not in mapping:
            family = family_of(canonical)
            mapping[name] = {
                "canonical": canonical,
                "family": family,
                **({"groups": t} if (t := group_tags(name, canonical, family)) else {}),
            }
    out = RESOURCES_DIR / "canonical_map.json"
    out.write_text(json.dumps(mapping, indent=0, sort_keys=True), encoding="utf-8")
    print(
        f"{len(counts)} distinct names; mapped {len(mapping)} names covering {covered / total:.1%} of {total} lines"
    )


if __name__ == "__main__":
    main()
