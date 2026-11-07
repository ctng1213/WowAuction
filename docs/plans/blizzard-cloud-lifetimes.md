# M33 step 1: auction lifetimes report (temporary)

22 snapshots over 21.9 h (0 refused or empty), 21 linked gaps, 440814 endings, 3001 partial fills, 453001 new listings.

## Band against hours since posting (BZ24)

Sightings of auctions whose posting gap is known; hour 0 is the snapshot that first saw them.

| band | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 | 14 | 15 | 16 | 17 | 18 | 19 | 20 | 21 | 22 | 23 | 24 | 25 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| VERY_LONG | 87423 | 52440 | 41292 | 45201 | 39601 | 38310 | 33461 | 33603 | 30063 | 25938 | 24561 | 4868 | 4 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| LONG | 455328 | 262814 | 206090 | 222102 | 192531 | 76462 | 536 | 58 | 0 | 3 | 252 | 15060 | 17344 | 16879 | 14675 | 10505 | 7965 | 6008 | 4020 | 1854 | 1922 | 0 | 0 | 0 | 0 | 0 |
| MEDIUM | 45594 | 7377 | 0 | 1 | 3692 | 111556 | 115419 | 29453 | 0 | 38 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| SHORT | 6148 | 8482 | 32 | 1 | 0 | 563 | 46959 | 35084 | 5671 | 16 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

## Rules against the data

- Band clashes with the expiry window: 12429 of 2385259 sightings of known-posting auctions (all sightings are checked; short-at-first-sight is not counted).
- Band at first sight of new listings: VERY_LONG 65177 (14.5%), LONG 343923 (76.5%), MEDIUM 39574 (8.8%), SHORT 628 (0.1%)

## Endings: sold or expired

| cohort | endings | sold (0) | expired (1) | split | mean expired share |
|---|---|---|---|---|---|
| posting known | 336070 | 137509 | 147605 | 50956 | 0.566 |
| posting unknown | 104744 | 13931 | 76119 | 14694 | 0.825 |

## Relist matches against a placebo (same key and units, new in the same gap; 3 gaps later)

| kind | left early | matched in the gap | matched 3 gaps later | estimated relisted share |
|---|---|---|---|---|
| gear | 85818 | 0.678 | 0.700 | 0.000 |
| other | 131272 | 0.763 | 0.783 | 0.000 |

| matched, by stack | endings |
|---|---|
| 1 | 86675 |
| other | 20024 |
| round | 7357 |
