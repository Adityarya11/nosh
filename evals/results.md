# Eval results

Run 2026-11-10 with anthropic / claude-haiku-5-5. Conversations replay from the LLM disk cache; every check is deterministic (independent SQL/Python, text, route).

## Summary

| Case | Checks passed | Status |
|---|---|---|
| t1_penne_no_dairy | 5/5 | PASS |
| t1_garlic_onion_count | 3/3 | PASS |
| t1_tomato_fewest_steps | 3/3 | PASS |
| t1_no_match_custard_pie | 3/3 | PASS |
| t1_unsupported_calories | 2/2 | PASS |
| t2_match_exclude_eggs | 7/7 | PASS |
| t3_banana_bread_no_eggs | 4/4 | PASS |
| t3_cream_pasta_no_dairy | 4/4 | PASS |
| t3_weak_evidence_gelatin | 4/4 | PASS |
| routing (decide branches 1-8 plus pinned mode) | 10/10 | PASS |

## What worked

- **Spec T1: penne, then 'I cannot have dairy'**: Hard constraint follow-up; the surviving recipes must carry no dairy line, checked against the independent probe list.
  - route: route search, expected search
  - route: route search, expected search
  - contains: text found: 'exclude dairy'
  - no_banned: 5 result recipes scanned for dairy: 0 suspicious line(s) []
  - contains: text found: 'excluded only because an ingredient line points to another recipe'
- **Spec T1 stats: garlic and onion count and percent**: The count and percent must equal an independent recomputation (plain Python over the ingredient table).
  - route: route stats, expected stats
  - stats_count: answer 15,877 (12.91%); independent 15,877 (12.91%)
  - contains: text found: 'Denominator'
- **Spec T1 stats: five tomato recipes with the fewest steps**: The ranked IDs must equal an independent ordering by n_steps then ID, excluding recipes with no instructions.
  - route: route stats, expected stats
  - ranked_ids: answer ['.uDSSfq9G3/nNX0I/wxSHJ3jd2XmDEO', '/0wx00RefE0VHzlff/GBszObKmj63Pi', '0jiuG.aUIS8aPIOl/fWeQpXs41uQera', '1aUc3u7ilBze2F6UmtZy0ZIC2fYMAUK', '2fJq96BLh.ikKPXygmkqogZ1rufkECm'] vs independent ['.uDSSfq9G3/nNX0I/wxSHJ3jd2XmDEO', '/0wx00RefE0VHzlff/GBszObKmj63Pi', '0jiuG.aUIS8aPIOl/fWeQpXs41uQera', '1aUc3u7ilBze2F6UmtZy0ZIC2fYMAUK', '2fJq96BLh.ikKPXygmkqogZ1rufkECm']
  - contains: text found: 'Step rule v2'
- **No-match: banana custard pie**: No title covers the query: say so first, show nearest titles, offer (never execute) an adaptation.
  - route: route search, expected search
  - contains: text found: 'No recipe in the dataset has a title that covers'
  - contains: text found: 'Reply yes'
- **Unsupported attribute: calories**: The dataset has no nutrition data; the answer must say so instead of inventing a number.
  - route: route unsupported, expected unsupported
  - contains: text found: 'does not contain'
- **Spec T2: eggs, onions, tomatoes, rice, then 'Exclude eggs too'**: The spec request word for word. Candidate counts and the top 5 must equal an independent ranking; after the follow-up no result may contain egg.
  - route: route match, expected match
  - match_independent: candidates answer 47,522 vs independent 47,522; top-5 IDs equal: True
  - contains: text found: 'Matched (4/4)'
  - route: route match, expected match
  - match_independent: candidates answer 23,919 vs independent 23,919; top-5 IDs equal: True
  - no_banned: 5 result recipes scanned for egg: 0 suspicious line(s) []
  - contains: text found: 'Matched (3/4)'
- **Spec T3: banana bread without eggs, dataset only**: The spec request word for word. The dataset has only 8 egg-free banana breads, so weak evidence is expected: the answer must label it predicted, and anything it cites must exist and contain the replacement.
  - route: route substitute, expected substitute
  - t3_validated: adaptation passed the validators
  - cited_ids: no recipe cited (predicted adaptation)
  - contains: text found: 'predicted'
- **Spec T3: cream in a pasta recipe without dairy**: The spec request word for word. The adapted ingredient list must contain no dairy line, and every cited recipe must contain the replacement.
  - route: route substitute, expected substitute
  - t3_validated: adaptation passed the validators
  - adapted_no_banned: 14 adapted ingredient lines scanned for dairy: none found
  - cited_ids: 3 cited recipe(s) exist and contain 'milk'
- **Weak evidence: panna cotta without gelatin**: All 36 panna cottas use gelatin, so every candidate rests on widened dishes. The answer must grade honestly (indirect or predicted) and cite only real recipes.
  - route: route substitute, expected substitute
  - t3_validated: adaptation passed the validators
  - cited_ids: 3 cited recipe(s) exist and contain 'cornstarch'
  - contains: text found: 'predicted'

## What failed, and why

Nothing failed in this run.

## Routing cases (decide(), no LLM)

| Case | Branch | Expected | Got | Status |
|---|---|---|---|---|
| r1_affirmation_executes_offer | 1 | substitute | substitute | PASS |
| r2_unsupported_attribute | 2 | unsupported | unsupported | PASS |
| r3_aggregate_is_stats | 3 | stats | stats | PASS |
| r4_remove_with_dish_is_substitute | 4 | substitute | substitute | PASS |
| r5_two_ingredients_is_match | 5 | match | match | PASS |
| r6_followup_reruns_active_action | 6 | search | search | PASS |
| r7a_dish_is_search | 7 | search | search | PASS |
| r7b_unknown_dish_is_no_match | 7 | search_no_match | search_no_match | PASS |
| r8_nothing_is_clarify | 8 | clarify | clarify | PASS |
| r9_pinned_mode_blocks_other_tasks | pinned | clarify | clarify | PASS |
