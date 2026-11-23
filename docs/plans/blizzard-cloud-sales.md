# Sales-rate measurements before M33b (temporary)

354 snapshots over 358.1 h (0 refused or empty); 4949051 early leaves (gone within 2 h of a LONG or VERY_LONG sighting: bought or cancelled, never expired), 101255 partial fills; 1304 commodity items.

## 1. Do commodity purchases take the cheapest units first?

A partial fill while a cheaper auction of the same item survived the gap unchanged contradicts cheapest-first.

| kind | partial fills | with a cheaper auction kept | share |
|---|---|---|---|
| commodities | 100475 | 23 | 0.0% |
| regular | 780 | 0 | 0.0% |

## 2. The commodity cancel floor

Early-leaving commodity units whose item kept a cheaper auction unchanged through the gap. If cheapest-first holds (1), these were cancelled, not bought.

| item | early-leaving units | with a cheaper auction kept | share | units per day |
|---|---|---|---|---|
| all commodities | 50209016 | 6497514 | 12.9% | 3365388 |
| Sharp Arrow | 6482560 | 0 | 0.0% | 434510 |
| Razor Arrow | 3530889 | 203473 | 5.8% | 236667 |
| Rough Arrow | 2019453 | 100 | 0.0% | 135359 |
| Crafted Solid Shot | 1919256 | 228036 | 11.9% | 128643 |
| Crafted Heavy Shot | 1385061 | 83693 | 6.0% | 92837 |
| Heavy Shot | 919658 | 2738 | 0.3% | 61642 |
| Solid Shot | 879828 | 64617 | 7.3% | 58973 |
| Linen Cloth | 792785 | 63103 | 8.0% | 53138 |
| Crafted Light Shot | 720046 | 6200 | 0.9% | 48263 |
| Feathered Arrow | 702717 | 48466 | 6.9% | 47101 |
| Exploding Shot | 653248 | 18607 | 2.8% | 43786 |
| Light Leather | 637130 | 48227 | 7.6% | 42705 |
| Silk Cloth | 564858 | 34327 | 6.1% | 37861 |

## 3. Early leaves by listing age

Auctions whose posting gap is known, at risk in a gap when LONG or VERY_LONG; the share that left early.

| age h | commodities at risk | left | regular at risk | left |
|---|---|---|---|---|
| 0 | 5714061 | 30.5% | 6765564 | 18.5% |
| 1 | 3149266 | 16.2% | 4442194 | 6.7% |
| 2 | 2530233 | 6.9% | 3967075 | 4.3% |
| 3 | 2353016 | 4.8% | 3807093 | 3.2% |
| 4 | 2275686 | 3.7% | 3729271 | 2.5% |
| 5 | 1307081 | 3.3% | 2184606 | 2.3% |
| 6 | 393535 | 2.8% | 764477 | 1.8% |
| 7 | 376504 | 2.9% | 744083 | 1.7% |
| 8 | 362682 | 3.1% | 720930 | 1.7% |
| 9 | 351129 | 2.9% | 707377 | 1.7% |
| 10 | 332884 | 2.9% | 674524 | 1.5% |
| 11 | 323690 | 2.7% | 671985 | 1.5% |
| 12 | 314040 | 2.5% | 658287 | 1.4% |
| 13 | 301636 | 2.5% | 639573 | 1.3% |
| 14 | 288402 | 2.4% | 615850 | 1.3% |
| 15 | 285600 | 2.4% | 620426 | 1.3% |
| 16 | 275777 | 2.3% | 606207 | 1.2% |
| 17 | 262623 | 2.2% | 581769 | 1.1% |
| 18 | 250748 | 2.3% | 563542 | 1.3% |
| 19 | 241566 | 2.3% | 545454 | 1.2% |
| 20 | 232773 | 2.1% | 538075 | 1.1% |
| 21 | 126336 | 1.8% | 291127 | 1.1% |
| 22 | 275 | 1.8% | 610 | 0.5% |
| 23 | 0 | — | 0 | — |
| 24+ | 0 | — | 0 | — |

## 4. Items with 30 endings per cell

Projected from this span's early leaves; hour-of-week cells assume an even spread over the week.

| weeks of data | items with early leaves | item level | item × tier (some tier) | item × hour × tier (some tier) |
|---|---|---|---|---|
| 1 | 41768 | 5554 | 5039 | 7 |
| 4 | 41768 | 16391 | 16029 | 181 |

## 5. Early leavers' price against the hourly ask close

Units-weighted median price of early leavers (commodity leaves with a cheaper auction kept left out) against the median of the item's cheapest listing per snapshot; items with 10 or more early leaves.

| sold median against ask median | items |
|---|---|
| sold below ask by over 20% | 11259 |
| within ±20% | 9324 |
| sold above ask by over 20% | 76 |

| item | kind | early leaves | sold median (c) | ask median (c) | sold ÷ ask |
|---|---|---|---|---|---|
| Gold Nugget | regular | 10 | 990000 | 200000 | 4.95 |
| Goblin Rocket Helmet | regular | 11 | 190000 | 49999 | 3.80 |
| Heavy Earthen Gloves | regular | 180 | 10950 | 4096 | 2.67 |
| Hornbeam Seed | commodity | 32 | 2500 | 940 | 2.66 |
| 53 Pound Yellowtail | regular | 26 | 20000 | 8000 | 2.50 |
| Brightcloth Robe | regular | 34 | 45000 | 20000 | 2.25 |
| Runecloth Robe | regular | 78 | 64800 | 29000 | 2.23 |
| 37 Pound Redgill | regular | 11 | 10000 | 4600 | 2.17 |
| Earthenweave Cord | regular | 32 | 168200 | 80000 | 2.10 |
| Blueprint: Master Forge | regular | 40 | 1220000 | 600000 | 2.03 |

## 6. Noise of a 6 h activity count

| item | 6 h blocks | early leaves per block | observed CV | Poisson CV |
|---|---|---|---|---|
| Red Linen Bag | 59 | 480 | 0.37 | 0.05 |
| Malachite | 59 | 440 | 0.61 | 0.05 |
| Hillman's Shoulders | 59 | 432 | 0.42 | 0.05 |
| Pyrite | 59 | 403 | 0.36 | 0.05 |
| Embossed Leather Gloves | 59 | 394 | 0.58 | 0.05 |
| Linen Cloth | 59 | 383 | 0.35 | 0.05 |
| Silk Cloth | 59 | 376 | 0.33 | 0.05 |
| Mystery Meat | 59 | 372 | 0.51 | 0.05 |

## 7. The nothing-ahead truth: the cheapest listing's chance within 8 h, by its price against the sold level

Each auction once, from the first snapshot it was the cheapest of its item in; sold = left early (bought or cancelled) within 8 h; the level is the item's early leavers' units-weighted median over that day and the day each side (10 or more leaves). This is what SM21's price acceptance is checked against (sold-price plan §1d item 36(a)); cancels are in the sold share (section 2's floor says how many).

| shape | price ÷ the level | auctions | sold within 8 h | share |
|---|---|---|---|---|
| gear and the rest (11274 items) | under 1.00× | 761370 | 497137 | 0.65 |
| gear and the rest (11274 items) | 1.00–1.10× | 2520259 | 603423 | 0.24 |
| gear and the rest (11274 items) | 1.10–1.25× | 322217 | 85778 | 0.27 |
| gear and the rest (11274 items) | 1.25–1.50× | 257281 | 61519 | 0.24 |
| gear and the rest (11274 items) | 1.50–2.00× | 244570 | 46079 | 0.19 |
| gear and the rest (11274 items) | 2.00× and over | 348464 | 48843 | 0.14 |
| commodities (1105 items) | under 1.00× | 609095 | 465519 | 0.76 |
| commodities (1105 items) | 1.00–1.10× | 1031196 | 412162 | 0.40 |
| commodities (1105 items) | 1.10–1.25× | 198246 | 96400 | 0.49 |
| commodities (1105 items) | 1.25–1.50× | 148825 | 58113 | 0.39 |
| commodities (1105 items) | 1.50–2.00× | 111584 | 33769 | 0.30 |
| commodities (1105 items) | 2.00× and over | 144289 | 26274 | 0.18 |

Auctions gone within their first observed gap: the nearer to 1, the more of an item's sales the snapshots never see at all (item 36(b): a listing bought before any snapshot is invisible, so sold/day reads low).

| shape | auctions cheapest when first seen | gone in that first gap | share |
|---|---|---|---|
| gear | 3940786 | 759858 | 0.19 |
| commodities | 2333696 | 738437 | 0.32 |
