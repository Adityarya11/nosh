# Findings log: problems caught, how, and what changed

Each entry: what went wrong, how it was found, why it matters for the spec, the fix, and how the fix
was verified. Decisions live in `docs/decisions.md`; this file is the evidence behind several of them.
How this was done: all code is AI-generated (Claude Code). I wrote the spec (AGENTS.md), directed the work and
reviewed every checkpoint, with AI-assisted reviews that probe the built database. Each entry records the method
that found the problem, so it can be re-run.

Status: OPEN = found, fix not verified; FIXED = fix verified with real output (numbers quoted).

---

## F1. Epicurious instructions are duplicated (data, before build)

- **Symptom:** ~20,500 of 25,323 Epicurious instruction texts contain their own text twice.
- **Found by:** raw-JSON probe before any code (heuristic: first 40 chars recur later).
- **Why it matters:** "fewest/most instruction steps" (T1 example) would double-count steps for 20% of the dataset.
- **Fix:** step rule v1 removes exact halves (line-level and block-level) before counting (`data/steps.py`).
- **Status:** FIXED in step 2 (step stats: median 4, max 71).

## F2. Substring matching is wrong for ingredients (data, before build)

- **Symptom:** the substring `rum` appears in ingredient lines of 12,441 recipes ("cRUMbs", "dRUMsticks",
  "spectRUM"), while the whole word `rum(s)` appears in 2,206. Same for `ham`: 4,745 by substring vs 2,052 as a word.
- **Found by:** comparing LIKE substring counts with whole-word regex counts over all ingredient lines.
- **Why it matters:** any count, filter or restriction built on raw substrings is wrong.
- **Fix:** parse every ingredient line into name -> canonical -> family; match whole names only (D2, AGENTS.md section 5).
- **Status:** FIXED by design in step 2.

## F3. `.gitignore` rule hid the code package (repo)

- **Symptom:** `data/` (meant for the built DB) also matched `src/nosh/data/`, so load/steps/ingredients/build.py
  would never have been committed.
- **Found by:** `git status` showed `src/nosh/data/` missing; confirmed with `git check-ignore -v`.
- **Fix:** anchored rules `/data/`, `/tests/`.
- **Status:** FIXED (git status lists `src/nosh/data/` as untracked; DB and tests still ignored).
- **Lesson:** anchor root-only ignore rules.

## F4. Zero-step recipes would win "fewest steps" (step 2 review)

- **Symptom:** 33 recipes have no instructions (0 steps); an ascending ranking puts them first.
- **Found by:** step-2 checkpoint stats (min 0).
- **Fix:** `RANKABLE_SQL` (`n_steps >= MIN_RANKABLE_STEPS`) in `data/steps.py`; every step answer states the
  excluded count.
- **Status:** FIXED in step 3 (superseded in size by F6).

## F5. "No dairy" let dairy through (step 3 review), hard-constraint violation

- **Symptom:** exclusion checked only family + a 14-item product list. Compound products whose family is not
  dairy passed as dairy-free:

  | Canonical | Assigned family | Recipes |
  |---|---|---|
  | condensed cream of mushroom soup | soup | 460 |
  | condensed cream of chicken soup | soup | 288 |
  | milk chocolate chip | chocolate | 155 |
  | mexican cheese blend | blend | 117 |
  | processed cheese food | food | 106 |
  | condensed cream of celery soup | soup | 101 |
  | irish cream liqueur | liqueur | 80 |
  | italian cheese blend | blend | 58 |
  | blue cheese dressing | dressing | 55 |

  Overall, 2,123 canonicals containing a dairy word (10,634 recipe hits) were outside the dairy group; some are
  correctly non-dairy (coconut milk, peanut butter, cream of tartar, almond milk, butter lettuce).
- **Found by:** whole-word scan of every canonical in `recipe_ingredients` for dairy words, compared against
  `restrictions.expand("dairy")`.
- **Why it matters:** the spec's first example ("penne" -> "I cannot have dairy") could return a recipe with
  cheese blend. "Never relax a hard constraint" and "respect allergies" are the strictest rules in the spec.
- **Fix:** two layers. Definite = family/product match; possible = whole-word keyword hit not in a per-restriction
  `safe` list. Hard constraints exclude both; possible hits are labelled "may contain". `nosh build` prints a
  leak report per restriction so the map is audited, not guessed.
- **Status:** PARTLY FIXED (2026-11-07). Verified on the rebuilt DB: mexican cheese blend and processed cheese
  food now have family cheese; cream-of-X soups, cheese blends and milk chocolate are flagged dataset-wide.
  Penne: 164 ingredient matches, 29 survive no-dairy. A hard no-dairy now excludes 60.6% of recipes
  (56.8% definite, 4,559 may-contain only). Remaining leaks are tracked in F10-F13.
- **Interview point:** a restriction map built from a family taxonomy alone fails on compound products; the safe
  direction for allergies is to over-exclude and say "may contain", and to measure leaks instead of trusting the map.

## F6. Boilerplate counted as instruction steps (step 3 review)

- **Symptom:** the #1 answer to the spec's example "tomato, five fewest steps" was
  `.sdL81yHSwQ5Q.mJx4bSd8JwTj9BXrO` "Sweet Pepper Salmon with Zucchini Ribbon Salad", whose entire instructions
  are `©2010 Publix Super Markets, Inc.`. It has no real steps.
- **Scale:** instructions containing "copyright" 266 (63 with <= 2 steps), "all rights reserved" 259 (50),
  "©" 95 (16), "recipe courtesy" 73 (2). Some recipes' instructions are just the title ("Friendship Butterflies").
- **Found by:** reading the instruction text of the top-5 result rows, not just their IDs.
- **Why it matters:** the ranking was correct per the rule but the rule counted non-instructions; an exact
  answer to the wrong question.
- **Fix:** step rule v2 drops boilerplate lines (copyright, ©, all rights reserved, recipe courtesy of,
  photograph by, line equal to title) before counting; emptied recipes become 0 steps and are excluded with the note.
- **Status:** FIXED (step rule v2). 0-step recipes 33 -> 44. Tomato fewest-steps top 5 read by hand: Mozzarella
  Pesto Spread, Tomato Basil Smoothie, Black Bean Salad, Antipasti Platter, Tomato Vinaigrette, all with real
  one-step instructions; pool 14,549 ranked, 2 excluded for no instructions; matches independent SQL.
- **Interview point:** checking that a query is computed right (independent SQL) is not the same as checking
  that the data means what you think; always read the rows behind a top-k.

## F7. "Distinct forms" rule was a list, and inconsistent (step 3 review)

- **Symptom:** "garlic" excluded garlic powder and granulated garlic but counted granulated garlic powder (22),
  dried garlic; "onion" excluded onion powder but counted dried onion flake, granulated onion.
- **Found by:** listing every canonical in the garlic/onion/tomato families with recipe counts.
- **Fix:** replace the enumerated `distinct_forms` names with a modifier-word rule in `map_rules.json`
  (powder, granulated, dried, flake, salt, paste, sauce, juice, puree, soup, mix = processed form).
- **Status:** FIXED. `modifier_words` rule in map_rules.json. Garlic + onion = 15,877 (12.91% of 122,968),
  matches an independent set-intersection query.

## F8. Fallback family picked the flavor word, not the product (step 3 review)

- **Symptom:** unmapped long-tail names took the first matching family rule: "boursin garlic and herb cheese" ->
  garlic, "roasted garlic and herb tomato sauce" -> garlic, "mexican cheese blend" -> blend.
- **Found by:** family tails in the same listing as F7.
- **Fix:** product head words (cheese, sauce, soup, dressing, liqueur, ...) win over flavor modifiers; "cheese
  blend" -> cheese.
- **Status:** MOSTLY FIXED. mexican cheese blend (122 lines) and processed cheese food (106) -> cheese. Two
  one-off tail names still -> garlic (boursin garlic and herb cheese, roasted garlic and herb tomato sauce).

## F9. Parser leftovers in user-visible names (step 3 review, cosmetic)

- **Symptom:** canonicals such as "or cloves garlic", "and tomato", "very garlic", "from heads of garlic",
  "oz can crushed tomato", and one line with two ingredients ("onion powder teaspoon garlic powder").
- **Why it matters:** families are mostly right, so counts barely move, but these names appear in the
  normalization notes the user reads.
- **Fix:** strip leading connectors (or/and/from/very/extra) and leftover units; singularize every word.
- **Status:** FIXED. "or cloves garlic" and "and tomato" are gone. The singularizer's artifacts (cooky, py, chily,
  cassi) are fixed by the `irregular_singulars` map (cookies, brownies, pies, smoothies, veggies, chilies, ...) and a
  "cassis" exception. Verified: 0 canonicals contain "cooky". molasses, asparagus, couscous, hummus survive.

## F10. Accented names were mangled; crème fraîche passed "no dairy" (step 3 re-review), hard-constraint violation

- **Symptom:** the parser dropped non-ASCII letters, so "crème fraîche" -> "cr me fra che" and "jalapeño" ->
  "jalape o". 670 crème fraîche lines were unflagged; 191 recipes with it passed a hard no-dairy filter.
- **Scale:** 12,161 raw ingredient lines contain non-ASCII characters.
- **Found by:** raw-line scan for dairy terms in recipes with NO dairy flag, against an independent term list.
- **Fix:** `fold()` (Unicode NFD, strip combining marks) runs before parsing and before restriction matching. NFD,
  not NFKD: NFKD rewrites the vulgar fractions (143 lines, "½" -> "1⁄2") and "™" (396 lines, -> "tm").
  "creme fraiche", "fraeche" and "frache" are dairy keywords (the data also holds misspellings).
- **Status:** FIXED, with one residue. Verified on the rebuilt DB: 988 lines (952 recipes) mention crème fraîche in
  any spelling, 988 are flagged banned dairy, 0 unflagged. Non-ASCII characters left in parsed names: 0.
  The "0 canonicals containing 'cr me' or 'jalape o'" target is NOT met literally: 1 and 2 canonicals remain, from
  source corruption (the raw line is "cr��me fraîche", "jalape‱o", "jalape&241;o"), where the letters
  are already gone before parsing. Those lines are still flagged by the "fraiche" keyword.

## F11. The leak report checked the keyword list against itself (step 3 re-review)

- **Symptom:** the build reported "338 of 338 cheese-blend lines caught", true but circular: it only tests terms
  already in the list. An independent term list found unflagged dairy lines (line counts): crème fraîche 657,
  eggnog 89, dulce de leche 79, nutella 24, pepper jack 22, velveeta 20, roquefort 20, chevre 16, fromage 15, plain
  "Swiss", and compounds like "Swiss Buttercream".
- **Found by:** a hand-written list not taken from restrictions.json.
- **Fix:** (1) `resources/leak_probes.json`, a separate probe list per group, scanned by `nosh build`, reporting lines
  and recipes that carry a probe term but no flag from the group or its constituents; (2) every canonical in
  canonical_map.json now has a `groups` tag (1,937 names, 668 tagged, reviewed by hand: whole tagged list and whole
  untagged list read), with keywords as the fallback for the 15% of lines outside the map; (3) buttercream,
  butterscotch, milkshake, creme fraiche, eggnog, dulce de leche, nutella, velveeta, pepper jack, roquefort, chevre,
  fromage, plain "swiss" (with "swiss chard" safe-listed) are keywords.
- **What the probe found on its first runs (before the fixes), unflagged lines:** dairy: alfredo 60, gelato 19,
  bechamel 17, ganache 6; gluten: pie crust 323, cereal 425, stout 66, lager 47, "roll" 942, linguini and
  rigatoni (tag review), egg/spring roll wrappers (found by the full-build probe); shellfish: conch 25; fish: lox 3,
  kipper 2; nuts: filbert; soy: natto; pork: spare/back ribs 81, speck 9.
- **Status:** FIXED for the probe list. Final build, unflagged lines / recipes per group: dairy, egg, gluten, nuts,
  peanut, soy, shellfish, fish, pork, vegetarian: 0 / 0. Vegan showed 71 / 66, all noise from the probe itself (cream
  sherry, hemp milk are safe); both are now in the probe's exceptions.
- **Limit:** the probe list is also mine. It finds what the keyword list misses only where my two lists differ. The
  reviewed tag list is the second check. Neither covers unknown unknowns (curry paste, kimchi, gochujang, ranch, tartar
  sauce are not flagged).
- **Interview point:** independent verification means independent inputs, not just a differently shaped query.

## F12. Certain allergens labelled only "may contain" (step 3 re-review)

- **Symptom:** exclusion was correct but the label understated certainty: parmigiano-reggiano, pecorino, gorgonzola,
  provolone, gruyere, queso fresco, creme fraiche (dairy); cake mix, puff pastry, panko, phyllo, english muffin
  (gluten); mayonnaise, egg wash, egg roll wrapper (egg); almond paste, almond meal, walnut oil (nuts).
- **Fix:** `keywords` split into `definite_keywords` (-> `banned`) and `maybe_keywords` (-> `may_contain`:
  chocolate, caramel, margarine, creamy, oats, worcestershire, pesto, granola, broth, marshmallow, roll, ...).
- **Status:** FIXED. Lines with the term / lines at level banned, from the rebuilt DB: parmigiano 1,436/1,436,
  pecorino 788/788, gorgonzola 420/419, provolone 591/591, gruyere 624/624, queso fresco 255/255, "fraiche" 633/633;
  cake mix 1,063/1,059, puff pastry 828/828, panko 1,172/1,172, phyllo 427/427, english muffin 169/168;
  mayonnaise 5,445/5,428, egg wash 328/328, egg roll wrapper 74/74; almond paste 164/164, almond meal 85/85,
  walnut oil 160/160. The lines that are not banned, checked one by one: "recipe follows" lines (flagged may-contain only),
  vegan mayonnaise (safe-listed), and substring look-alikes ("crabcake mix", "cheesecake mixture"). One real
  miss remains: "golden buttercake mix" (gluten, 1 line), because "buttercake" is one word.

## F13. False positives from keyword scanning (step 3 re-review)

- **Symptom:** "spaghetti squash" flagged gluten may-contain (97 lines); a few coconut and butternut squash lines.
- **Fix:** "spaghetti squash" added to the gluten `safe` list; also "swiss chard" (dairy), "cream sherry" (dairy),
  "oyster mushroom" (shellfish), "pasta sauce" and "spaghetti sauce" (gluten), found during the tag review.
- **Status:** FIXED. Of 107 spaghetti squash lines, 2 are gluten-flagged (lines that also name a gluten item).
  Remaining known over-flags are listed in D21.

## F14. Sub-recipe references hide ingredients (step 3 re-review)

- **Symptom:** lines like "Swiss Meringue Buttercream, recipe follows" reference another recipe whose ingredients
  are not in this list.
- **Fix:** lines containing "recipe follows" or "see recipe" (`SUB_RECIPE_PHRASES` in config.py) are `may_contain`
  for every restriction.
- **Status:** FIXED, extended in F15. 8,131 lines in 6,129 recipes (5.0%) matched "recipe follows" / "see recipe";
  with the F15 variants (below) it is 8,392 lines in 6,345 recipes (5.2%), every line flagged in all 11 groups.
  Cost: every hard constraint also drops those recipes. Every constrained answer states how many matching recipes
  were excluded only because of such a line (see F15).

## F15. Second independent probe: residual leaks after F10-F14 (step 3 third review)

- **Method:** a third term list, taken from neither restrictions.json nor
  leak_probes.json, scanned against raw lines with no flag for that group. Rebuilt DB of 2026-11-07 evening.
- **Real leaks (lines / recipes):**
  - dairy: ranch 490 / 482, caesar dressing 12 / 12, milk powder 7 / 6, quark 2, pepper jack 1
  - egg: tartar sauce 49 / 49, mayonnaise misspellings ("mayonaisse", "mayonnais", "mayonnaise1") about 20, caesar dressing 14,
    brioche 5, popover 4
  - gluten: gochujang 70 / 60, beer 165 / 152 (minus "root beer"), matzo 23 / 23, malt 8
  - fish: salted / dried codfish (about 20)
  - nuts: nougat 25 / 24, frangipane 2
  - sub-recipe variant "recipe to follow" (also "recipe below" 116, "see below" 99) not treated like "recipe follows"
- **Checked and correctly unflagged:** rice / glass / mung bean noodles (gluten), crabapples and crab apple jelly
  (shellfish), veal scallopini and "scalloped edge" (shellfish), vegan mayonnaise (egg), "coddled" (fish).
- **Fix:** terms added to `restrictions.json`: definite: ranch, caesar dressing and caesar salad dressing (dairy and
  egg), tartar sauce (egg), gochujang and matzoh (gluten), codfish (fish); maybe: nougat (nuts). Mayonnaise
  misspellings: `fuzzy_keywords` in the egg group, matched with rapidfuzz (`FUZZY_MIN_RATIO` 85,
  `FUZZY_MAX_LEN_DIFF` 3) on every token of the parsed name, canonical and raw line; scores for the real
  misspellings: mayonaise 94.7, mayonaisse 90.0, mayonnai 88.9, mayonnaise1 95.2; look-alikes score low (mayocoba
  55.6, chimayo 47.1, mayo 57.1). `SUB_RECIPE_PHRASES` now also holds "recipe to follow", "recipe below", "see below".
  `nosh build` writes a `sub_recipe_lines(recipe_id, pos)` table, and `store.sub_recipe_excluded` counts recipes that
  pass every filter and are excluded only because a sub-recipe line carries the group flag; `render.sub_recipe_note`
  prints that count in every answer that has an exclusion.
- **Verification (rebuilt DB, regenerated map, 131 s):**
  - Unflagged lines / recipes for ranch, caesar dressing (dairy and egg), tartar sauce, mayonnaise misspellings,
    gochujang, matzo, codfish, nougat: 0 / 0 each. Independent probe scan: 0 unflagged for every group; vegan
    showed 1 line, "1 pound egg-free fettuccine", which is correctly unflagged (safe phrase "egg free"); the probe's
    exception list for vegan now includes "egg free".
  - Beer: of 164 unflagged beer lines, all 164 are "root beer" or "ginger beer" (safe-listed on purpose); 0 other.
    Beer was already a definite gluten keyword, so no change was made.
  - Malt: the 52 unflagged lines containing "malt" are schmaltz, isomalt and maltodextrin, not gluten sources.
  - Sub-recipe lines: 8,392 lines, 6,345 recipes. Count check: "garlic, excluding dairy" gives 15,162 recipes, 929
    of them excluded only by a sub-recipe line; independent SQL over `line_flags` and `sub_recipe_lines` gives 929.
  - Step 3 checkpoints unchanged: garlic + onion 15,877 (12.91%), tomato fewest-steps top 5 equal to independent SQL,
    penne + no dairy 28 of 164, and all 338 / 1,261 / 694 cheese-blend / cream-of-X / milk-chocolate lines banned dairy.
  - Recipes with any flag after this round: dairy 62.5%, gluten 54.7%, vegetarian 46.5%, vegan 84.8%.
- **Not done (listed in F15 but not in the fix list):** quark (2 lines), "brioches" (plural, 5 lines; the
  singularizer turns it into "brioch"), popover (4), frangipane (2), milk powder (7, all coconut or soy milk powder,
  correct to leave unflagged).
- **Status:** FIXED for the listed terms, with the residue above.
- **Interview point:** each independent list found leaks the previous rounds missed, and the counts shrink each
  round (10k+ recipe hits -> 191 recipes -> about 800 lines). Detection by vocabulary converges but never reaches
  zero, so the README states it as a limitation and the bot never claims a recipe is allergen-free, only that no
  listed ingredient matched.

## F16. Misspelled restriction ignored ("I cannot have diary") (step 4 live run)

- **Symptom:** the first live Gemini run passed "diary" through as the constraint; no group matched, so the
  reply said it was not applied and still showed all 284 penne recipes.
- **Found by:** live `nosh chat` run: penne -> "I cannot have diary".
- **Fix:** fuzzy match against restriction *group names only* (5+ letters, similarity >= 0.8); the reply states
  "I read 'diary' as 'dairy'". Aliases are not fuzzy-matched, because "batter"/"bitter" would become "butter" and
  silently exclude dairy.
- **Status:** FIXED. Live re-run 2026-11-08: penne -> "I cannot have diary" -> 41 of 284, filters: exclude dairy,
  1 recipe excluded only for a sub-recipe line; "dairy is fine now" -> back to 284.

## F17. Plain ingredient exclusions are not applied ("no cilantro") (step 4 live run)

- **Symptom:** "no cilantro" -> "I have no restriction list for 'no cilantro', so I did not apply it." Only the 11
  restriction groups can be excluded. The spec needs arbitrary ingredients ("exclude peanuts", "Exclude eggs too").
  Also, the slot came through as "no cilantro" with the negation still attached.
- **Found by:** live `nosh chat` run.
- **Why it matters:** honest (not silent), but it fails the T1/T2 requirement that a newly stated restriction applies.
- **Fix:** resolve a constraint in order: group name (exact, then fuzzy) -> ingredient term (resolve_term:
  family/canonical plus a whole-word raw-line scan) -> only if it matches no line in the dataset, say "not applied".
  Strip leading "no/without/exclude" in code as a backstop, and ask for the bare ingredient in prompts/slots.txt.
- **Implementation:** `constraints.resolve` (strips leading no/without/exclude/excluding, then group name exact ->
  fuzzy -> ingredient term -> "not applied"). An ingredient exclusion matches the normalized name (family/canonical
  via `term_sql`) OR a whole-word regex on the accent-folded raw line (plural-aware: egg/eggs, cherry/cherries), so
  "cilantro leaf" is caught although its family is "leaf". Like the restriction groups, an ingredient exclusion also
  drops recipes with a "recipe follows"-type line (their ingredients are hidden) and counts them in the sub-recipe
  sentence. `prompts/slots.txt` now asks for bare names ("cilantro", "dairy"). Also applies to stats
  (`StatsRequest.exclude_terms`), not just search.
- **Evidence (item 2):** each result prints its canonical ingredient names and "Checked: no <restriction or
  ingredient> match in N lines" for every active exclusion.
- **Status:** FIXED. Live run 2026-11-08 (gemini-3.6-flash): "penne" -> 284 title matches; "no cilantro" -> filters:
  exclude cilantro, 271 of 284 (13 recipes dropped: 5 have a cilantro line, 8 only a sub-recipe line); "I cannot have
  dairy" on top -> 38 of 284. Independent check (BM25 ids + Python regex over the raw lines + `is_sub_recipe`):
  5 recipes with a cilantro line, 8 with only a sub-recipe line, 271 remaining, 38 after dairy + cilantro; the code
  gives 271 and 38. Constraint resolution checked offline: "no cilantro" -> ingredient, "without eggs" -> egg group,
  "exclude peanuts" -> peanut group, "Exclude eggs too" -> egg group, "no jalapeno" -> ingredient,
  "no zzzfoo" -> not applied (no line in the dataset matches).
- **Side effect:** "no cheese" or "no butter" resolve to the dairy group (they are dairy aliases), which is stricter
  than excluding that one ingredient.


## F18. Search order is mostly ID order (step 4 review, minor)

- **Symptom:** BM25 over titles gives only 10 distinct scores across 284 penne titles; the top 5 all score 3.2639,
  so the visible order is the recipe-ID tie-break. Truthfully labelled, but arbitrary for a user.
- **Found by:** listing match_bm25 scores for "penne".
- **Fix (optional):** a meaningful second key (e.g. fewer steps, then ID), stated in the "Order:" line.
- **Status:** FIXED. Second sort key is fewer steps (recipes with no instruction steps sort after all others), then ID;
  the "Order:" line says so ("title relevance (BM25), then fewer steps (recipes without steps last), then recipe
  ID"; ingredient-only and fuzzy searches skip the BM25 part). Live: the top 5 for "penne" changed from
  Meaty Penne (5 steps), Penne Without the Vodka (6), Penne Primavera (3), Baked Penne (4), Penne with Eggplant (2) to
  Penne with Eggplant (2), Ratatouille with Penne (2), Cheesy Penne (2), Penne Primavera (3), Penne Rustica (3).

## F19. The spec's own T2 example ranked 2/4 matches above 4/4 (step 5 live run)

- **Symptom:** the exact spec request ("I have eggs, onions, tomatoes, and rice ... as many of these as possible,
  no peanuts, at most six steps. Prefer recipes that need the fewest additional ingredients.") returned a top 5
  that each match only 2 of 4 ingredients (Tempura Onion Rings, a tomato salad, ...). After "Exclude eggs too",
  #4 was "QA", a 1-line junk record matching only rice.
- **Cause:** the "fewest additional ingredients" preference moved the extras key to position 1, above "most of
  your ingredients matched". Recipes with 0 non-staple extras are mostly tiny recipes using 1-2 of the items.
  The step-5 checkpoint was run without the preference sentence, so it showed 4/4 matches.
- **Found by:** live `nosh chat` run of the spec request verbatim.
- **Fix:** "matched" stays the first key (the request says "as many of these as possible"); preferences reorder
  only the keys below it (fewest extras, fewest steps, ID).
- **Status:** FIXED. `order_keys()` inserts a preference after "matched". Spec request verbatim, live: top 5 all match
  4/4 (47,522 candidates; 3/4 after "Exclude eggs too", 23,919 candidates, five 2-step recipes, no "QA"). With "fewest
  steps" the order is matched, steps, extras, ID and the top-5 step counts are 2,3,3,3,3. An independent Python recomputation
  over the raw DB lines gives equal candidate counts, top-5 IDs and matched counts in all three runs.
- **Interview point:** run the spec's examples verbatim; a paraphrased checkpoint hid a ranking bug.

## F20. Pantry staples by family are too broad (step 5 review)

- **Symptom:** pantry families include all of oil, vinegar, flour, butter, so cake flour, balsamic vinegar, and
  (by the same rule) sesame oil or almond flour count as staples the user is assumed to have.
- **Fix:** pantry by canonical (salt, black pepper, water, sugar, all-purpose flour, vegetable/olive oil, butter,
  baking soda/powder, ...), not whole families. Staples are shown either way, so this is a ranking-accuracy fix.
- **Status:** FIXED. `pantry.json` is now 39 canonicals (was 20 families + 13 canonicals); a family such as oil, vinegar or
  flour no longer makes sesame oil, balsamic vinegar or cake flour a staple. The independent check uses the same list and
  agrees on the top 5 and the extras counts.

## F21. "no cheese" / "no butter" exclude the whole dairy group (step 5 report)

- **Symptom:** dairy aliases resolve to the group, so "no cheese" also drops recipes with milk or yogurt.
  Over-exclusion is safe, but it changes the user's stated constraint and shrinks results.
- **Fix:** only group names (dairy, egg, gluten, nuts, ...) resolve to groups; aliases resolve as ingredients
  (family/canonical + whole-word raw-line scan). State the reading in the reply.
- **Status:** FIXED. `restrictions.json` keeps group names and synonyms (dairy, lactose, crustacean, ...) in `aliases` and moves
  milk/cheese/butter (dairy) and shrimp (shellfish) to `ingredient_aliases`. "no cheese" now resolves to the ingredient cheese
  and the reply says: "I read 'cheese' as the ingredient cheese only, not the whole dairy group; say 'no dairy' to exclude all of
  it." Verified for cheese, butter, milk, shrimp; "no dairy" and "dairy" still resolve to the group with no note.

## F22. Small form leaks in ingredient matching (step 5 review, minor)

- **Symptom:** "onion" matches green-onion tail names ("whole green onion", "green onion with top", about 50
  recipes) although "scallion" is separate; "egg" matches egg noodles (3); "rice" matches rice cereal (about 8).
- **Fix:** add noodle and cereal to modifier_words; alias green onion -> scallion.
- **Status:** FIXED. noodle and cereal are modifier words, and 29 green-onion name variants alias to scallion (the map script
  now includes aliased names even when rare). Recipes whose only "onion" match was a green-onion line: 73 before, 6 after (the
  rest are lines such as "onion powder with green onion ..."). Recipes whose only egg match is an egg-noodle line: 0; whose only
  rice match is a rice-cereal line: 0. Map rebuilt; the leak probe scan is still 0 unflagged in all 11 groups.

## F23. Contrast evidence finds dish differences, not substitutes (step 6 checkpoint)

- **Symptom:** cream in "pasta", dairy excluded: the top contrast candidates are olive (21/159 without cream vs 0/35 with),
  spaghetti (20/159 vs 1/35), egg (14/159 vs 0/35), water, caper, flour, zucchini. None of the olive/spaghetti/egg lines
  replaces cream; they are what a tomato or egg-pasta dish contains instead of a cream sauce. Same for the "alfredo" type set
  (broccoli florets, sausage, zucchini).
- **Cause:** a WITH/WITHOUT contrast measures how the two groups of dishes differ overall; without the role of the target
  (step 3's LLM tag, used in step 7) code cannot tell a replacement from a correlated add-in.
- **Mitigation now:** contrast candidates are graded indirect with the reason "role not verified"; only an author-written
  "X or Y" line in a similar dish is graded direct. The role check is step 7's job.
- **Status:** OPEN (mitigated). **Interview point:** the contrast table is evidence of co-occurrence, and the grade says so.

## F24. Banana bread without egg: the dataset has almost no egg-free examples (step 6 checkpoint)

- **Result:** 139 banana breads (titles ending in "bread"), 131 with egg, 8 without (Vegan x2, Microwave Tofu, Whole Grain,
  Carrot, Rich and Delicious, Fruit Bread, Ice Cream Bread). No family reaches support 3 in the WITHOUT group with 2x lift:
  best are walnut 2/8 (vs 52/131 with), oil 3/8 (vs 49/131), chocolate 2/8. Applesauce is in 1/8 egg-free vs 10/131 egg breads,
  so it is not over-represented. No "egg or X" line occurs in a banana bread; the only explicit lines are glaze lines in other
  dishes ("beaten egg or milk", "beaten egg or heavy cream", graded indirect).
- **Text mentions:** 26 recipes mention substituting egg in their instructions, none is a banana bread (they are egg
  substitute products or other dishes), so they are shown but not used for grading.
- **Meaning:** this is the weak-evidence case step 7 must handle (predicted substitution, labelled, with the uncertainty
  stated). It is not a bug. Without the head-noun title filter the WITHOUT group was 13 and gave oat and pecan, which came
  from "Banana Oat Bread Pudding" and "Overnight Oats" and are not egg replacements.
- **Status:** FIXED (filter), result recorded as data.

## F25. Explicit-evidence and similar-set false positives, found by reading the checkpoint lines (step 6)

- **Symptom:** "X or Y" matches included "Easter Egg or bunny-shaped cookie cutters", "ice cream or powdered sugar",
  "whipped cream or confectioners' sugar" (target cream), "egg or pumpernickel bread slices" and one unparseable tail
  ("to make your pie richer"). The text-mention cue "substitut" matched the product "egg substitute" and "sugar substitute".
- **Fix:** the text before "or" must parse to the target itself ("8 eggs" yes; "Easter egg", "ice cream" no); the tail must be a
  mapped ingredient; cues are now "substitute for/with/in", substituting, substitution, instead of, in place of, replace.
  Similar dishes must have a title ending in the dish's head noun, falling back to all matches when fewer than MIN_SIMILAR
  remain. Mentions dropped from 172 to 11 (with the old cue) to 26 (new cue, egg; several still unrelated: "instead of
  flipping"), so the mentions stay out of the grading.
- **Status:** FIXED, with the remaining text noise recorded here.

## F26. Gemini free tier ran out during step 7; dev runs moved to Anthropic
- **Symptom:** `429 RESOURCE_EXHAUSTED ... generate_content_free_tier_requests, limit: 20` on `gemini-3.6-flash` (about 17 hours until reset). One substitution turn uses 4-6 calls.
- **Fix:** dev runs use `NOSH_LLM_PROVIDER=anthropic`, `claude-haiku-5-5`; Gemini stays the default in the README with the 20 requests/day note. The Anthropic path (`messages.parse`, no sampling parameters) worked live for slots, roles, proposals and adaptation, with the disk cache on.
- **Status:** FIXED for dev; the limit is documented.

## F27. Dairy-free replacements rejected as dairy (step 7 checkpoint b)
- **Symptom:** the LLM proposed "cashew cream" and "vegan sour cream" for cream; the restriction check rejected both ("breaks the dairy (banned) restriction") because "cream" is a dairy keyword and neither is on the dairy safe list.
- **Effect:** the two most natural dairy-free replacements never reach the evidence table. Over-rejection is the safe direction, but it costs recall.
- **Fix needed:** add the plant-based phrases to `restrictions.json` dairy `safe`, rebuild (line_flags and map tags depend on it). Not done: a rebuild is a heavy job (D26).
- **Status:** OPEN.

## F28. Weak evidence in checkpoints (a) and (b), and three grading bugs found by reading the output
- **Result (a), banana bread minus eggs:** 139 banana breads, 131 with egg, 8 without. Proposals applesauce, plain yogurt, mashed banana all grade NONE (applesauce 0/8 without vs 5/131 with, independent regex agrees); flax meal and chia were rejected as "not an ingredient in the dataset". The adapted recipe is labelled predicted with no citations. Haiku did not propose silken tofu, which the step 6 contrast found in 1 egg-free banana bread, so LLM-first proposals lose that recall.
- **Result (b), cream in pasta, no dairy:** base "Best Ever Pasta Salad" (its cream line is "1 (8 ounce) container sour cream"; "cream" matches the family). Candidates coconut yogurt, silken tofu, mashed avocado all NONE; adapted with mashed avocado, labelled predicted.
- **Bugs fixed while reading:** (1) a candidate's citable lines came from every group, so "Tilapia Salad" and "Avocado Salads" were cited for avocado although the widened class did not support it; lines now come only from the group that met the criterion. (2) one WITHOUT recipe was enough for "indirect" (avocado from "Bacon Avocado Pasta", whose line only shows avocado in a creamless pasta); contrast now needs MIN_SUPPORT recipes and MIN_LIFT. (3) substitution-mention matching accepted "eggs and oil, ... instead of 1/2 cup oil, ... add 1/4 cup applesauce"; the target, the cue and the candidate must now be within 15 characters.
- **Status:** FIXED (bugs); recall limit OPEN.

## F29. Weak-evidence case (c): panna cotta without gelatin, and what the validators cannot see
- **Result:** 36 panna cottas, all use gelatin. Cornstarch grades INDIRECT from the widened classes (54/404 without gelatin vs 1/17 with; the WITH group is tiny). Cited: Rose Petal and Pistachio Raspberry Custard (1 quart milk, 1/2 cup cornstarch, no gelatin) and Banana Pudding (4 cups milk, 1/2 cup plus 2 tablespoons cornstarch, 8 yolks): both support cornstarch as a setting agent. Lucy's Carrot Pudding uses 1 1/2 tablespoons cornstarch in a lemon sauce: that is sauce thickening, only loosely related.
- **Gap:** the adapted recipe keeps "1 1/2 teaspoons cornstarch" (the gelatin amount) for 2 cups of dairy; the cited recipes use about 1/2 cup per quart. Validators check restrictions, citations, grades and claims but not quantities. The LLM flagged the amount as unverified in its uncertainty text.
- **Status:** OPEN (quantity check is not in the spec's validator list).

## F30. Validators work: planted bad outputs and live rejections (step 7)
- **Planted, no LLM (6 cases):** valid output accepted; citing a recipe not retrieved this turn, citing a retrieved recipe with no line for the substitute, a banned ingredient ("1/2 cup butter" with dairy excluded), the phrase "allergy-safe", and evidence stronger than the verified grade were all rejected.
- **Live rejections seen:** a citation of a recipe that was only a "mention" (not retrieved); an ingredient line "1 avocado, mashed (replaces 1 (8 ounce) container sour cream)" rejected as dairy; a substitution whose `original` was the recipe line instead of the target name. Each was fixed in the next attempt (adapt.txt now says to use the target name and no notes).
- **Memory:** every run under `scripts/guard.py` (then `tests/guard.py`) peaked at 340-445 MB (job memory), minimum system free RAM 2.7 GB.
- **Status:** FIXED.

## F31. Data candidates and explicit partners need a role check (step 7 follow-up)
- **F27 after the rebuild:** cashew cream, vegan sour cream and coconut cream are no longer flagged as dairy (check_line returns None). "plant-based milk" and "dairy-free cheese" still are, because a safe phrase must cover the keyword itself; residual, not fixed.
- **Result (b) now:** base "Crawfish and Pasta" (its line is "1/3 cup cream"); the adapted recipe replaces cream with unsweetened coconut milk 1:1, citing South Florida Stuffing: "1/2 cup cream or 1/2 cup unsweetened coconut milk" (read: the line supports the swap and the amount; the dish is a stuffing, so INDIRECT). Data candidates black olive, spaghetti and egg graded indirect by co-occurrence only; the LLM did not use them.
- **Result (a) before the glaze filter:** the explicit partner "1 tablespoon beaten egg or heavy cream" (Peach Galette, an egg wash) made heavy cream an INDIRECT candidate for binding in banana bread, and the LLM used it (6 tablespoons, "(estimate)", with the caveat in its uncertainty). Fix: lines with brush/glaze/garnish/serving/topping/dusting/sprinkle/egg wash are not explicit evidence, and the adapt prompt says not to treat a use that does not match the target's roles as support.
- **Bug:** a substitution mention matched "cream" inside "cream soda"; the target pattern now excludes "cream soda/cheese/of tartar/sauce".
- **Status:** FIXED except the plant-based safe-list residual (OPEN) and the lack of a code-level role check (the roles are only used by the LLM).

## F32. First eval run failed on my own checker (step 8)
- **Symptom:** `t2_match_exclude_eggs` failed "candidates answer -1 vs independent 47,522" although the top-5 IDs matched.
- **Cause:** the check parsed "Candidates: N recipes" but the Markdown renderer now prints "**Candidates:** N recipes". The product was right, the checker was stale after the Markdown change.
- **Fix:** the regex accepts the bold form. After the fix all nine conversations and ten routing cases pass (see `evals/results.md`).
- **Status:** FIXED.

## F33. The dataset download link in the README is dead (pre-submission review)

- **Symptom:** `DATASET_URL = https://storage.googleapis.com/recipe-box/recipes_raw.zip` returns HTTP 404, so the
  README's second setup command fails on a grader's machine.
- **Found by:** `curl -I` on the configured URL, then reading the links on the dataset page.
- **Fix:** the dataset page links `/recipes_raw.zip`; `https://eightportions.com/recipes_raw.zip` returns 200
  (application/x-zip-compressed, 53,355,492 bytes). Point `DATASET_URL` there and test `get_data.py` end to end.
- **Verified:** the first run with Python's default user agent got `HTTP Error 403: Forbidden` from the host, so `get_data.py` now sends a generic `User-Agent: nosh-get-data` (`DOWNLOAD_USER_AGENT` in config). `get_data.py <temp folder>` then wrote `recipes_raw_nosource_ar.json` (49,784,325 bytes), `recipes_raw_nosource_epi.json` (61,133,971) and `recipes_raw_nosource_fn.json` (93,702,755); the archive layout needed no change, and all three files are byte-identical (SHA-256) to the ones already in `recipes_raw/`. Peak memory 337 MB under the guard.
- **Status:** FIXED.

## F34. README and AGENTS.md point to a file that is never committed (pre-submission review)

- **Symptom:** both say to run heavy jobs under `tests/guard.py`, but `/tests/` is gitignored, so the file does not
  exist in the repo.
- **Found by:** `git grep "tests/"` against `git ls-files`.
- **Fix:** move the guard to `scripts/guard.py` (committed) and update both references.
- **Verified:** `scripts/guard.py` is tracked; README, AGENTS.md section 13, D26 and F30 now say `scripts/guard.py`; the guard ran the download test above and printed `GUARD: peak job memory 337 MB (cap 3500 MB)`.
- **Status:** FIXED.
