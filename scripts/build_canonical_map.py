"""One-off: counts parsed ingredient names and writes resources/canonical_map.json from map_rules.json."""

import json
from collections import Counter

from nosh.config import MAP_COVERAGE_TARGET, RESOURCES_DIR
from nosh.data.ingredients import family_of, parse_line
from nosh.data.load import iter_lines, load_all


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
        mapping[name] = {"canonical": canonical, "family": family_of(canonical)}
        covered += n
    out = RESOURCES_DIR / "canonical_map.json"
    out.write_text(json.dumps(mapping, indent=0, sort_keys=True), encoding="utf-8")
    print(f"{len(counts)} distinct names; mapped {len(mapping)} names covering {covered / total:.1%} of {total} lines")


if __name__ == "__main__":
    main()
