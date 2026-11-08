# Sales-rate measurements before M33b (temporary)

31 snapshots over 30.9 h (0 refused or empty); 219954 early leaves (gone within 2 h of a LONG or VERY_LONG sighting: bought or cancelled, never expired), 4613 partial fills; 752 commodity items.

## 1. Do commodity purchases take the cheapest units first?

A partial fill while a cheaper auction of the same item survived the gap unchanged contradicts cheapest-first.

| kind | partial fills | with a cheaper auction kept | share |
|---|---|---|---|
| commodities | 4583 | 9 | 0.2% |
| regular | 30 | 0 | 0.0% |

## 2. The commodity cancel floor

Early-leaving commodity units whose item kept a cheaper auction unchanged through the gap. If cheapest-first holds (1), these were cancelled, not bought.

| item | early-leaving units | with a cheaper auction kept | share | units per day |
|---|---|---|---|---|
| all commodities | 1681428 | 181244 | 10.8% | 1305224 |
| Sharp Arrow | 301214 | 0 | 0.0% | 233820 |
| Rough Arrow | 98948 | 0 | 0.0% | 76809 |
| Crafted Light Shot | 60533 | 2400 | 4.0% | 46989 |
| Heavy Shot | 47320 | 2738 | 5.8% | 36733 |
| Linen Cloth | 46849 | 3814 | 8.1% | 36367 |
| Feathered Arrow | 39258 | 693 | 1.8% | 30474 |
| Light Leather | 36582 | 2509 | 6.9% | 28397 |
| Crafted Heavy Shot | 34932 | 1442 | 4.1% | 27116 |
| Rough Stone | 31701 | 872 | 2.8% | 24608 |
| Ruined Leather Scraps | 30997 | 562 | 1.8% | 24062 |
| Copper Ore | 30666 | 3364 | 11.0% | 23805 |
| Copper Bar | 28891 | 2040 | 7.1% | 22427 |
| Silk Cloth | 16411 | 3796 | 23.1% | 12739 |

## 3. Early leaves by listing age

Auctions whose posting gap is known, at risk in a gap when LONG or VERY_LONG; the share that left early.

| age h | commodities at risk | left | regular at risk | left |
|---|---|---|---|---|
| 0 | 252923 | 26.1% | 495185 | 14.0% |
| 1 | 145317 | 13.7% | 330873 | 4.1% |
| 2 | 110082 | 5.5% | 276449 | 2.6% |
| 3 | 110341 | 3.1% | 281471 | 1.9% |
| 4 | 96106 | 2.8% | 247771 | 1.6% |
| 5 | 46138 | 2.7% | 134609 | 1.2% |
| 6 | 11531 | 2.4% | 37252 | 1.1% |
| 7 | 11110 | 2.2% | 36070 | 1.2% |
| 8 | 10155 | 2.5% | 32943 | 1.0% |
| 9 | 9313 | 2.0% | 29967 | 1.2% |
| 10 | 9422 | 2.3% | 29995 | 1.2% |
| 11 | 8458 | 1.9% | 27051 | 1.1% |
| 12 | 8022 | 1.4% | 25585 | 1.1% |
| 13 | 7906 | 1.5% | 25983 | 0.9% |
| 14 | 7443 | 1.3% | 24913 | 0.8% |
| 15 | 6609 | 1.1% | 22039 | 1.1% |
| 16 | 6037 | 1.5% | 20378 | 1.1% |
| 17 | 5330 | 0.8% | 18566 | 0.6% |
| 18 | 4581 | 1.0% | 16583 | 0.5% |
| 19 | 3922 | 1.4% | 14809 | 1.0% |
| 20 | 3820 | 0.4% | 14619 | 0.5% |
| 21 | 402 | 1.2% | 1510 | 1.3% |
| 22 | 0 | — | 0 | — |
| 23 | 0 | — | 0 | — |
| 24+ | 0 | — | 0 | — |

## 4. Items with 30 endings per cell

Projected from this span's early leaves; hour-of-week cells assume an even spread over the week.

| weeks of data | items with early leaves | item level | item × tier (some tier) | item × hour × tier (some tier) |
|---|---|---|---|---|
| 1 | 11102 | 3765 | 3570 | 8 |
| 4 | 11102 | 8113 | 8019 | 98 |

## 5. Early leavers' price against the hourly ask close

Units-weighted median price of early leavers (commodity leaves with a cheaper auction kept left out) against the median of the item's cheapest listing per snapshot; items with 10 or more early leaves.

| sold median against ask median | items |
|---|---|
| sold below ask by over 20% | 525 |
| within ±20% | 1723 |
| sold above ask by over 20% | 28 |

| item | kind | early leaves | sold median (c) | ask median (c) | sold ÷ ask |
|---|---|---|---|---|---|
| Green Hills of Stranglethorn - Page 8 | commodity | 19 | 7500 | 1999 | 3.75 |
| Soldier's Girdle | regular | 10 | 300 | 10000 | 0.03 |
| Primal Wraps | regular | 11 | 200 | 3000 | 0.07 |
| Greenweave Bracers | regular | 14 | 700 | 7000 | 0.10 |
| Raider's Belt | regular | 11 | 400 | 3000 | 0.13 |
| Pioneer Tunic | regular | 10 | 300 | 2000 | 0.15 |
| Bard's Belt | regular | 10 | 300 | 2000 | 0.15 |
| Waylaid Crate: Expert Textiles | commodity | 19 | 548 | 3598 | 0.15 |
| Pioneer Trousers | regular | 10 | 200 | 1300 | 0.15 |
| Buccaneer's Bracers | regular | 10 | 400 | 2500 | 0.16 |

## 6. Noise of a 6 h activity count

| item | 6 h blocks | early leaves per block | observed CV | Poisson CV |
|---|---|---|---|---|
| Red Linen Bag | 5 | 648 | 0.36 | 0.04 |
| Fine Leather Belt | 5 | 388 | 0.57 | 0.05 |
| Light Feather | 5 | 368 | 0.16 | 0.05 |
| Copper Chain Pants | 5 | 368 | 0.77 | 0.05 |
| Embossed Leather Gloves | 5 | 352 | 0.92 | 0.05 |
| Malachite | 5 | 346 | 0.30 | 0.05 |
| Tigerseye | 5 | 330 | 0.23 | 0.06 |
| Hillman's Shoulders | 5 | 329 | 0.22 | 0.06 |
