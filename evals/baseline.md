# Baseline: one-shot LLM vs nosh

Question: *How many recipes use both garlic and onion, and what percent of the dataset is that?*

One-shot call: anthropic / claude-haiku-5-5, no retrieval, temperature 0.0.

## One-shot LLM answer

- Count: **31,250**
- Percent: **25.0%**
- Its explanation: This is a best-guess estimate from general knowledge, not a computed result. Garlic and onion together are common in savory cooking, so I assumed roughly a quarter of the roughly 125,000 recipes (about 31,250) include both.

## nosh answer

15,877 recipes match (12.91% of 122,968 usable recipes).

- **Filters applied:** contains ALL of: garlic, onion
- **Recipes analyzed:** 15,877
- **Denominator:** 122,968 usable recipes (2,196 records dropped at build: no ingredient list)
- **Calculation:** count = recipes matching the filters; percent = count / 122,968 x 100
- **Normalization:** "garlic" matched ingredient names across the dataset (top 6 by recipes): garlic (31,400), crushed garlic (281), roasted garlic (208), whole garlic clove (63), whole garlic (50), whole clove garlic (49); not counted as garlic (modified forms, top 6): garlic powder (3,556), granulated garlic (411), granulated garlic powder (22), garlic and herb seasoning blend (16), garlic and herb seasoning (11), salt-free garlic and herb seasoning (10)
- **Normalization:** "onion" matched ingredient names across the dataset (top 6 by recipes): onion (22,632), red onion (5,924), sweet onion (808), spanish onion (624), vidalia onion (290), pearl onion (173); not counted as onion (modified forms, top 6): onion powder (1,484), granulated onion (86), granulated onion powder (8), dried granulated onion (2), bertolli vidalia onion with roasted garlic sauce (1), charred spring onion and sesame-chile butter (1)

## Independent check (plain Python over the ingredient table)

- Recipes with both garlic and onion: **15,877** (12.91% of 122,968 usable recipes; 2,196 records dropped at build)

## Where the one-shot answer is wrong or ungrounded

- Its count differs from the real count by 15,373 (97% of the true value), and its percent by 12.09 points.
- It cannot name the denominator (the number of usable recipes), the filters, or how ingredient names were normalized, so the number cannot be checked or reproduced.
- nosh computes the count over the complete filtered set in SQL, states the denominator and the dropped-record count, lists which names counted as garlic and onion, and the count matches the independent recomputation.
