# Sales-rate measurements before M33b (temporary)

167 snapshots over 167.1 h (0 refused or empty); 3005982 early leaves (gone within 2 h of a LONG or VERY_LONG sighting: bought or cancelled, never expired), 58106 partial fills; 1320 commodity items.

## 1. Do commodity purchases take the cheapest units first?

A partial fill while a cheaper auction of the same item survived the gap unchanged contradicts cheapest-first.

| kind | partial fills | with a cheaper auction kept | share |
|---|---|---|---|
| commodities | 57779 | 11 | 0.0% |
| regular | 327 | 0 | 0.0% |

## 2. The commodity cancel floor

Early-leaving commodity units whose item kept a cheaper auction unchanged through the gap. If cheapest-first holds (1), these were cancelled, not bought.

| item | early-leaving units | with a cheaper auction kept | share | units per day |
|---|---|---|---|---|
| all commodities | 32333272 | 4399133 | 13.6% | 4643238 |
| Sharp Arrow | 3646265 | 0 | 0.0% | 523624 |
| Razor Arrow | 2946162 | 65049 | 2.2% | 423085 |
| Crafted Solid Shot | 1369672 | 137353 | 10.0% | 196693 |
| Crafted Heavy Shot | 1056743 | 107736 | 10.2% | 151754 |
| Rough Arrow | 965981 | 0 | 0.0% | 138720 |
| Solid Shot | 527145 | 38350 | 7.3% | 75701 |
| Hi-Impact Mithril Slugs | 457895 | 88661 | 19.4% | 65756 |
| Feathered Arrow | 411526 | 56879 | 13.8% | 59097 |
| Heavy Shot | 404717 | 0 | 0.0% | 58120 |
| Exploding Shot | 404333 | 47954 | 11.9% | 58064 |
| Jagged Arrow | 396315 | 30488 | 7.7% | 56913 |
| Linen Cloth | 385195 | 32567 | 8.5% | 55316 |
| Silk Cloth | 318147 | 9806 | 3.1% | 45688 |

## 3. Early leaves by listing age

Auctions whose posting gap is known, at risk in a gap when LONG or VERY_LONG; the share that left early.

| age h | commodities at risk | left | regular at risk | left |
|---|---|---|---|---|
| 0 | 3384506 | 32.2% | 3518704 | 20.4% |
| 1 | 1718861 | 16.9% | 2105856 | 8.2% |
| 2 | 1406114 | 7.5% | 1900044 | 5.4% |
| 3 | 1262108 | 5.3% | 1745926 | 4.1% |
| 4 | 1225719 | 4.0% | 1715244 | 3.3% |
| 5 | 723259 | 3.6% | 1004649 | 3.1% |
| 6 | 229226 | 3.0% | 371544 | 2.5% |
| 7 | 213130 | 3.1% | 347492 | 2.4% |
| 8 | 207821 | 3.3% | 342421 | 2.3% |
| 9 | 198612 | 3.1% | 329174 | 2.2% |
| 10 | 191716 | 3.0% | 319358 | 2.1% |
| 11 | 182084 | 2.8% | 307564 | 2.0% |
| 12 | 178445 | 2.7% | 305247 | 1.9% |
| 13 | 175245 | 2.7% | 303001 | 1.8% |
| 14 | 165815 | 2.5% | 287867 | 1.7% |
| 15 | 160248 | 2.7% | 280615 | 1.7% |
| 16 | 154441 | 2.6% | 274663 | 1.6% |
| 17 | 149962 | 2.4% | 268597 | 1.5% |
| 18 | 139197 | 2.5% | 250845 | 1.7% |
| 19 | 132323 | 2.3% | 238426 | 1.5% |
| 20 | 120921 | 2.3% | 221953 | 1.4% |
| 21 | 78265 | 2.0% | 139179 | 1.4% |
| 22 | 253 | 2.0% | 396 | 1.8% |
| 23 | 0 | — | 0 | — |
| 24+ | 0 | — | 0 | — |

## 4. Items with 30 endings per cell

Projected from this span's early leaves; hour-of-week cells assume an even spread over the week.

| weeks of data | items with early leaves | item level | item × tier (some tier) | item × hour × tier (some tier) |
|---|---|---|---|---|
| 1 | 42315 | 6582 | 5889 | 15 |
| 4 | 42315 | 19662 | 19222 | 241 |

## 5. Early leavers' price against the hourly ask close

Units-weighted median price of early leavers (commodity leaves with a cheaper auction kept left out) against the median of the item's cheapest listing per snapshot; items with 10 or more early leaves.

| sold median against ask median | items |
|---|---|
| sold below ask by over 20% | 7926 |
| within ±20% | 9056 |
| sold above ask by over 20% | 80 |

| item | kind | early leaves | sold median (c) | ask median (c) | sold ÷ ask |
|---|---|---|---|---|---|
| Ornate Blunderbuss | regular | 43 | 90000 | 520 | 173.08 |
| Rabbit's Foot | commodity | 92 | 65 | 9 | 7.22 |
| Gold Nugget | regular | 10 | 990000 | 200000 | 4.95 |
| Goblin Rocket Helmet | regular | 13 | 190000 | 49999 | 3.80 |
| SAF-T Tabs | commodity | 14 | 7999 | 2250 | 3.56 |
| Fissure Plant | commodity | 39 | 95 | 27 | 3.52 |
| Heavy Earthen Gloves | regular | 73 | 10000 | 3500 | 2.86 |
| Hornbeam Seed | commodity | 30 | 2400 | 900 | 2.67 |
| Runecloth Robe | regular | 84 | 64800 | 25000 | 2.59 |
| Black Whelp Cloak | regular | 86 | 9048 | 3545 | 2.55 |

## 6. Noise of a 6 h activity count

| item | 6 h blocks | early leaves per block | observed CV | Poisson CV |
|---|---|---|---|---|
| Red Wolf Meat | 27 | 549 | 0.38 | 0.04 |
| Malachite | 27 | 498 | 0.68 | 0.04 |
| Mystery Meat | 27 | 485 | 0.34 | 0.05 |
| Raptor Flesh | 27 | 466 | 0.33 | 0.05 |
| Hillman's Shoulders | 27 | 453 | 0.36 | 0.05 |
| Embossed Leather Gloves | 27 | 433 | 0.39 | 0.05 |
| Raptor Egg | 27 | 427 | 0.38 | 0.05 |
| Gooey Spider Leg | 27 | 427 | 0.45 | 0.05 |

## 7. The nothing-ahead truth: the cheapest listing's chance within 8 h, by its price against the sold level

Each auction once, from the first snapshot it was the cheapest of its item in; sold = left early (bought or cancelled) within 8 h; the level is the item's early leavers' units-weighted median over that day and the day each side (10 or more leaves). This is what SM21's price acceptance is checked against (sold-price plan §1d item 36(a)); cancels are in the sold share (section 2's floor says how many). The share counts only auctions VERY_LONG when they became the cheapest (24 h posts): a 2 h post is never LONG and an 8 h post leaves LONG after 6 h, so their sales then look like expiries and the share over every band reads low (item 42).

| shape | price ÷ the level | clean auctions | sold within 8 h | share | all auctions | share, every band (low) |
|---|---|---|---|---|---|---|
| gear and the rest (10402 items) | under 1.00× | 97356 | 81339 | 0.84 | 501217 | 0.65 |
| gear and the rest (10402 items) | 1.00–1.10× | 199989 | 74139 | 0.37 | 820375 | 0.34 |
| gear and the rest (10402 items) | 1.10–1.25× | 29798 | 11260 | 0.38 | 174458 | 0.29 |
| gear and the rest (10402 items) | 1.25–1.50× | 22171 | 7582 | 0.34 | 127947 | 0.26 |
| gear and the rest (10402 items) | 1.50–2.00× | 20856 | 5797 | 0.28 | 113088 | 0.22 |
| gear and the rest (10402 items) | 2.00× and over | 34423 | 5907 | 0.17 | 170585 | 0.15 |
| commodities (1108 items) | under 1.00× | 56290 | 51824 | 0.92 | 353883 | 0.76 |
| commodities (1108 items) | 1.00–1.10× | 135744 | 53908 | 0.40 | 592831 | 0.39 |
| commodities (1108 items) | 1.10–1.25× | 14949 | 9315 | 0.62 | 103127 | 0.50 |
| commodities (1108 items) | 1.25–1.50× | 10414 | 5403 | 0.52 | 74709 | 0.40 |
| commodities (1108 items) | 1.50–2.00× | 7685 | 3154 | 0.41 | 54616 | 0.33 |
| commodities (1108 items) | 2.00× and over | 9491 | 2311 | 0.24 | 63623 | 0.20 |

Auctions gone within their first observed gap: the nearer to 1, the more of an item's sales the snapshots never see at all (item 36(b): a listing bought before any snapshot is invisible, so sold/day reads low).

| shape | auctions cheapest when first seen | gone in that first gap | share |
|---|---|---|---|
| gear | 1814242 | 423457 | 0.23 |
| commodities | 1284746 | 430848 | 0.34 |
