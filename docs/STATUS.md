# What is built and what isn't

Updated after every step. "Built" means the code exists, it ran, and tests cover it.
"Not built" means no code exists yet, whatever the plan says.

## Built

| Feature | Where | Evidence |
|---|---|---|
| Docker Postgres, schema, triggers, foreign keys | `docker-compose.yml`, `backend/db/` | `test_database.py` |
| Master data and opening balances | `generator/master.py` | `test_generator.py` |
| Ledger: receipts, walk-in sales, damage, adjustments | `generator/simulate.py` | Replay test, validator |
| Exact movement and order targets, including injected rows, with no invalid sale | `generator/simulate.py`, `generator/settings.py` | `test_anomalies.py`, `test_order_generation.py`, validator |
| Validator: empty data, missing balances, row counts, missing activity, exit code | `scripts/validate.py` | `test_validator.py`, `test_order_flow.py` |
| Same seed, same files, every folder, byte for byte | `scripts/compare_runs.py` | `test_anomalies.py`, both scales run |
| Reserved stock at any past time, from events | `app/atp.py`, `app/rules.py` | `test_orders.py`, `test_order_flow.py` |
| ATP and promisable, with safety stock and channel buffers | `app/atp.py`, `app/rules.py` | `test_orders.py`, `test_rules.py` |
| Inbound counted by the policy in the plan | `app/atp.py` | `test_orders.py`, `test_transfers.py` |
| Allocation: one location, two locations, inbound, reject | `app/rules.py`, `app/orders.py` | `test_orders.py` |
| Two orders can't reserve the same last unit | `app/orders.py` | `test_orders.py` |
| Confirm, cancel and expiry, with history | `app/reservations.py` | `test_orders.py` |
| Transfers: leave the source, stay in transit, reach the destination, counted once | `app/transfers.py`, `generator/simulate.py` | `test_transfers.py`, `test_generator.py`, validator |
| Stores reorder from warehouses, warehouses from the NDC, the NDC from suppliers | `generator/simulate.py` | `test_generator.py` |
| Orders on a truck wait until it is received | `app/shipping.py`, `generator/order_flow.py` | `test_transfers.py`, `test_order_generation.py`, validator |
| Shipping and delivery of online orders | `app/shipping.py`, `generator/order_flow.py` | `test_transfers.py`, `test_late_trucks.py` |
| Returns name a real sale, and never pass what was sold, including online orders | `app/returns.py`, `generator/simulate.py` | `test_returns.py`, `test_generator.py`, validator |
| Late trucks: keep the first promise, try another location, update the promise, cancel past 3 days | `app/late_trucks.py`, `generator/order_flow.py` | `test_late_trucks.py`, `test_rules.py`, `test_order_generation.py` |
| A moved hold never reserves the same stock twice | `app/late_trucks.py`, `generator/order_flow.py` | `test_late_trucks.py`, validator replay of every line |
| Generated orders: expired holds, oversell attempts, splits, inbound, online returns | `generator/order_flow.py` | `test_order_generation.py`, validator |
| Clean baseline, written before anything is injected | `scripts/generate.py` | Passes every check at both scales |
| Four labelled anomalies, with decoys | `generator/inject.py` | `test_anomalies.py` |
| Each case causes only its own problem | `generator/inject.py` | `eval/check_unrelated.py`, run at both scales: final data less the labelled rows equals the clean baseline |
| Trucks take varied, believable times, and case trucks follow the same rules as normal ones | `generator/simulate.py`, `generator/inject.py` | `test_anomalies.py`, measured at both scales |
| Everything numbered in time order after injection | `generator/inject.py` | `test_anomalies.py` |
| Ground truth in its own folder, read only by `eval/` | `generator/export.py`, `eval/` | `test_anomalies.py` |
| Four detectors, from the ledger and events alone | `app/detect.py`, `scripts/run_detect.py` | `test_anomalies.py` |
| Precision and recall per type | `eval/score.py` | `test_anomalies.py` |
| Expected and unexpected failures sorted by comparing record IDs with labelled IDs | `eval/check_failures.py` | `test_anomalies.py`, both scales run |
| A check missing from the summary, or one that reports no IDs, counts as a failure | `eval/check_failures.py`, `scripts/validate.py` | `test_anomalies.py` |
| ATP checkpoints around expiry, truck arrival and cancellation, from a second calculation | `generator/checkpoints.py`, `eval/check_atp.py` | `test_checkpoints.py`, both scales run |
| Validator counts duplicate keys, broken references and values not allowed | `scripts/validate.py` | `test_validator.py`, lists compared with the database |
| Data files compared with the manifest by checksum | `scripts/validate.py` | `test_validator.py` |
| Data dictionary, compared with the real database and data by a test | `docs/DATA_DICTIONARY.md` | `test_data_dictionary.py` |
| Every check run on other seeds with one command | `eval/try_seeds.py` | 40 seeds run, see Known problems |
| API for ATP, orders, trace, ledger, trucks, returns, exceptions and metrics | `app/main.py`, `app/views.py`, `app/trace.py` | `test_api.py`, including 20 orders racing for one unit through the API |
| React page on real data, one view per actor | `frontend/src/` | Checked by hand in a browser, see Partly built |
| Everything in one command | `scripts/run_all.py` | Run at both scales, `test_outputs.py` |
| Every measured number in one file, with no record IDs | `eval/report.py` | `test_outputs.py`, served at `/api/metrics` |
| The same data as SQL and JSON | `scripts/export_seed.py` | `test_outputs.py` loads the SQL into an empty database |
| Starter data, ground truth and metrics ready to commit | `backend/data/starter/`, `backend/data/ground_truth/starter/` | Manifest checksums, `.gitattributes` |
| Live numbering carries on after a load | `db/constraints.sql` | `test_order_flow.py` |

## Partly built

| Feature | What works | What is missing |
|---|---|---|
| Orders | Online orders through function calls, tests and generated data | STORE orders raise `NotImplementedError`, and the API answers 501 |
| Shipping | One parcel per location, delivery, refusal on a short pick | A short pick is only refused, nothing re-allocates it. An order ships whole, never line by line |
| Late trucks | Handled when the notice comes in, and by `handle_overdue_trucks` | `handle_overdue_trucks` runs when asked, by a button and `POST /api/trucks/handle-overdue`. Nothing runs it on a schedule |
| Replenishment | Rules are calculated, reorders move through the three levels | First stock for a late SKU or new store is a direct delivery, not a transfer. One reorder at a time per pair, instead of the plan's position formula |
| Returns | Good and damaged returns in live code and generated data | No return window |
| React page | Every page, the order flow and truck actions, checked by hand in a browser, in light and dark, and at phone width | No automated browser tests. Tested by hand on the starter data, and opened on the full data |
| Login | Nothing | There is none. Anyone who can reach the local API can write. It is a local demo of the rules |
| AI_USAGE.md | What the record shows | The personal parts are left for Manikanta to write |
| Live stock changes | Orders, transfers, shipping, returns and late handling take the SKU-location lock | Walk-in sales, BLOCK, UNBLOCK, WRITE_OFF and adjustments exist only in the generator, with no live version |
| Generated decisions agree with SQL | **At full scale, only a sample of 200 decisions was checked against SQL: 200 of 72,160 on the clean baseline and 200 of 72,360 on the final data.** Every decision was checked at starter scale: all 1,418 on the clean baseline agree, and of all 1,438 on the final data, only the 5 labelled ones differ | More than 72,000 full-scale decisions have not been recalculated. `validate --decisions` can run more, at several queries each |
| ATP checkpoints | 40 rows at starter and 200 at full, all matching SQL | Only a few moments: 5 events of each of 4 kinds at starter, 25 at full, with a row before and at each |

## Not built

| Feature | Why |
|---|---|
| STORE orders | Walk-in sales cover the store channel, and the brief doesn't name STORE orders |
| A deployed site | The brief asks for working code in a repo. It runs locally |

## Measured so far

Seed 20261022. The final data includes the labelled problems and decoys.

| Number | Starter | Full |
|---|---|---|
| Movements, clean baseline | 19,970 | 999,700 |
| Movements, final | 20,000 | 1,000,000 |
| Orders, clean baseline | 980 | 49,800 |
| Orders, final | 1,000 | 50,000 |
| Oversell attempts | 20 | 1,513 |
| Split allocations | 53 | 2,950 |
| Lines held on inbound stock | 21 | 1,191 |
| Orders that expired | 155 | 6,988 |
| Holds moved off a late truck | 5 | 120 |
| Promises updated for a late truck | 6 | 249 |
| Holds cancelled past the limit | 1 | 177 |
| Generate, all three folders | about 2 s | 131 s |
| Load, with foreign keys and indexes | about 3 s | 51 s |
| Validate, including 200 decisions | about 9 s | 125 s |
| Detect | under 1 s | 61 s |
| Everything, with `run_all` | 36 s | 920 s |

All 777 tests pass, from a fresh clone with an empty database, using only the README's commands.

**Validation**

| Dataset | Checks passed | Failed | Unexpected | Exit code |
|---|---|---|---|---|
| Starter, clean baseline | 229 of 229 | 0 | 0 | 0 |
| Starter, final | 223 of 229 | 6 | 0 | 1 from the validator, 0 from `check_failures` |
| Full, clean baseline | 229 of 229 | 0 | 0 | 0 |
| Full, final | 224 of 229 | 5 | 0 | 1 from the validator, 0 from `check_failures` |

The failures on the final data. Each one lists its failing record IDs, and `check_failures`
compares them with the labelled IDs, 5 at starter and 50 at full. A count that happens to match
isn't enough. Nothing passed by count alone.

| Check | Explained by | IDs compared |
|---|---|---|
| closing balance does not reconcile to opening plus ledger | `BALANCE_NOT_RECONCILED` | Balance |
| network stock plus in transit differs from opening plus outside changes | `BALANCE_NOT_RECONCILED` | SKU, because it adds up a whole SKU |
| hold above what could be promised when it was placed | `RESERVATION_EXCEEDS_AVAILABLE` | Reservation |
| order sale from the blocked bucket | `FULFILLED_FROM_BLOCKED` | Movement |
| transfer received at or before it shipped | `TRANSFER_DOUBLE_COUNTED` | Shipment |

The starter run has a sixth failure: the sample of 200 decisions landed on 1 labelled order, whose
logged decision is stale on purpose. At full scale the sample landed on none, so it can't speak
for the 50 labelled ones. Those are counted by the check on holds above, which looks at every hold.

**Detection**

Every type at both scales: precision 1.0, recall 1.0, F1 1.0, no decoy flagged, and nothing
flagged on the clean baseline.

| Type | Starter labelled and found | Starter decoys | Full labelled and found | Full decoys |
|---|---|---|---|---|
| `BALANCE_NOT_RECONCILED` | 5 | 5 | 50 | 50 |
| `RESERVATION_EXCEEDS_AVAILABLE` | 5 | 5 | 50 | 50 |
| `FULFILLED_FROM_BLOCKED` | 5 | 5 | 50 | 50 |
| `TRANSFER_DOUBLE_COUNTED` | 5 | 5 | 50 | 50 |

**What these scores don't show:** the same person wrote the anomalies and the detectors, and
each detector tests the exact condition its anomaly breaks. So 1.0 shows the two are
consistent. It isn't evidence of how they would do on real data or on a kind of problem
nobody thought of. The decoys and the clean baseline run are the stronger part, because they
show the detectors don't simply flag everything suspicious.

**ATP checkpoints**

The decision recheck only asks about the second an order was decided. These ask about one second
before and the second of an event no order decision looks at. `generator/checkpoints.py` works
them out in plain Python from the generator's own lists, and `eval/check_atp.py` compares them
with what `app/atp.py` answers in SQL.

| Kind | Starter matched | Full matched |
|---|---|---|
| Hold expiring | 10 of 10 | 50 of 50 |
| Truck arriving | 10 of 10 | 50 of 50 |
| Customer cancelling | 10 of 10 | 50 of 50 |
| Late truck: hold cancelled or moved | 10 of 10 | 50 of 50 |

- All 25 full-scale arrivals leave ATP the same, because inbound stock becomes stock on hand.
  Counting it at both ends would raise ATP at that second.
- All 25 expiries and all 25 customer cancels give back exactly the units held. A late truck is
  handled once for all its holds, so 10 of those 25 gave back more than the one hold checked,
  because other holds on that truck ended in the same second.
- ATP went below zero on 25 of the 200 rows at full scale, as low as -15. A paid hold on an
  overdue truck still counts as reserved, but the truck no longer counts as inbound. `promisable`
  stays at 0, so nothing can be promised from it.
- **What this doesn't show:** these are a small sample, picked evenly, and only on SKUs with no
  labelled case. Both calculations were written by the same person from the same formula, so they
  would share a misunderstanding of the rule.

**Manifest review**

| Finding | What happened |
|---|---|
| Only row counts were compared with the manifest, so a file with changed contents would pass | Fixed. The validator now compares every checksum |
| The summary didn't say which data it was about, or how many decisions it covered | Fixed. It now has dataset, seed, scale, decisions checked and decisions total |
| The checkpoints had no checksum anywhere | Fixed. The final manifest has one, with no answers in it |
| Counters in the manifest could drift from the data | Tested. A test compares 10 of them with the tables |
| `python_version` is in the manifest, so another Python gives a different manifest even if the CSV files match | Left in, because it is true and useful. Only Python 3.12.3 has been run |
| Nothing records which code version wrote the data | Left out. The commit doesn't exist until after the data is generated. The generator command and seed are what recreate it |
| The summary lists failing IDs beside the data | Left there. They come from SQL on the data, not from the labels, and only `check_failures` reads the file. The loader opens the 18 table files by name |

## Known problems

**Other seeds.** Only seed 20261022 has run at full scale. `python -m eval.try_seeds` runs everything
on other seeds at starter scale: both datasets, the ID comparison, the detectors and the checkpoints.
40 seeds were tried, 1 to 20 and 20261023 to 20261042.

| Result | Seeds | What happens |
|---|---|---|
| No problem | The other 31 | Every check passes or is explained, with scores of 1.0 and every checkpoint matching |
| Generator refuses to run | 1, 16, 20261023 | `could not place a blocked stock case, no SKU left that it fits`. It stops and writes no final data, rather than place a bad case |
| Validation fails | 8, 10, 13, 15, 19 | No hold was moved off a late truck, so `generated data includes holds moved off a late truck` fails on both datasets. Seed 15 also has no late-truck checkpoint |
| Validation fails | 20261025 | No hold was cancelled past the limit, so `generated data includes holds cancelled past the late limit` fails on both datasets |

**This got worse with the new truck times:** 36 of the same 40 seeds had no problem before, and 31 do
now. Different seeds fail, so these rare cases seem to depend on chance at starter size, but the exact
cause wasn't isolated. The default seed passes with little room: it has only 1 hold cancelled past
the limit at starter scale. The validator is right to fail the others, since a rule can't be shown to
work on rows that don't exist. Nothing was changed to make them pass.

**Truck times.** Every truck now follows the same rules, in `order_from_source` and `truck_times`:

| | Normal trucks | Anomalies | Decoys |
|---|---|---|---|
| Leaves | Store hours | Store hours | Store hours |
| Due | Noon, 2 or 3 days later | Same | Same |
| Comes in | 00:00 to 09:00, evenly spread | Same | Same |
| Comes in the day before it is due | 13.9% | 12.0% | 100%, on purpose |
| Hours on the road at full scale | 3.0 to 191.3, median 39.1 | 31.3 to 70.8, median 57.7 | 4.5 to 44.7, median 35.1 |
| TRANSFER_OUT | When it leaves | During store hours, on or after the day it arrives | When it leaves |

The anomaly is still that the stock reaches the destination before it leaves the source. A decoy is a
short trip that beats its ETA, which looks premature but is valid. It no longer tests the one-second
edge, so two tests with hand-written rows check the detector there.

**The anomaly pattern.** Each case has to cause only its own problem, so it goes where nothing depends
on it. That makes the cases look a little different from normal data. Measured at full scale on the
final data, including how often a record with that pattern really is a case:

| Pattern | Cases | Everything else | Records like this that are cases |
|---|---|---|---|
| Truck on the road for 1 second or less | 0 of 100 | 0 of 81,022 | Gone. It was 100 of 100 before |
| First ETA exactly 2 days out, to the second | 0 of 100 | 4 of 82,375 | Gone. It was 100 of 100 before |
| Left after the SKU's last order activity, with no other truck of that SKU on the road for 5 days | 100 of 100 | 2,368 of 82,375, 2.9% | 4.05%, the strongest truck pattern left |
| No other truck of that SKU on the road for 5 days | 100 of 100 | 6,124 of 82,375, 7.4% | 1.61% |
| Came in the day before it was due | 56 of 100 | 11,280 of 82,375, 13.7% | 0.49% |
| First ETA 3 days out | 74 of 100 | 19,660 of 82,375, 23.9% | 0.37% |
| 5 units or fewer | 66 of 100 | 32,497 of 82,375, 39.5% | 0.20% |
| Never missed its ETA | 100 of 100 | 75,641 of 82,375, 91.8% | 0.13% |
| Order is the last one on its SKU, with one line | 200 of 200 order cases | 2,056 of 49,800 orders | 8.87%, the strongest order pattern |
| Order has exactly one line | 200 of 200 order cases | 32,404 of 49,800 orders, 65.1% | 0.61% |
| Order is for exactly 1 unit | 100 of 100 blocked cases | 20,502 of 49,800 orders, 41.2% | 0.49% |
| Order expired unpaid | 100 of 100 reservation cases | 6,888 of 49,800 orders, 13.8% | 1.43% |
| TRANSFER_OUT posted later than the truck left | 50 of 50 anomalies, 0 of 50 decoys | 0 of 82,375 | 100%, but this is the anomaly itself, not a side effect |

What that means:

- **Nothing outside the anomaly itself picks out the cases any more.** The best truck filter finds them
  among about 24 times as many normal trucks. Before this change, road time alone found all 100.
- **Case trucks still differ a little.** They carry fewer units, because they are on quieter SKUs. They
  go to warehouses more often, which is why more are due in 3 days. None of them misses its ETA.
- **The four detectors use none of this.** They test the rule each anomaly breaks. So their scores
  aren't inflated by it, and a new bad row with no such pattern is still found, which a test shows.
- **Don't train a model on this data to find anomalies.** It could still learn these patterns and not
  the problem, and it would score better than it should.

**Settings chosen to fit the cases.** To make room, half of the SKUs stop getting online orders at a
random date, and opening damaged stock went from 1% to 8% of pairs. Both change the data for that
reason alone. Late-truck rates and the 15% of trucks that come in early are also set on purpose, so they say nothing
about real carriers.

**Full-scale movements**

| Movement | Rows | Share |
|---|---|---|
| SALE, walk-in | 675,323 | 67.5% |
| SALE, online order | 52,464 | 5.2% |
| TRANSFER_OUT | 82,475 | 8.2% |
| TRANSFER_IN | 81,122 | 8.1% |
| RETURN | 45,191 | 4.5% |
| ADJUSTMENT | 18,768 | 1.9% |
| BLOCK | 14,140 | 1.4% |
| RECEIPT | 12,082 | 1.2% |
| UNBLOCK | 10,540 | 1.1% |
| WRITE_OFF | 7,895 | 0.8% |

- Online returns at full scale: 5,245 rows, 8,421 units.
- On-hand holds shipped by their first promise: 51,677 of 51,810, which is 99.7%.
- Holds on trucks shipped by their first promise: 607 of 654, which is 92.8%. This says nothing
  about real carriers, because late trucks and rush deliveries are set on purpose.

**Where the cases fall.** How far through the data each group sits, as a median at full scale:
order anomalies 0.52, order decoys 0.41, transfer anomalies 0.37, transfer decoys 0.39. With only 5
per type at starter, the groups can differ a lot by chance: order anomalies sit at 0.27 there and
order decoys at 0.05. So at starter scale, don't read anything into their dates.

**Other limits**

- A labelled reservation expires unpaid, so it holds stock only briefly. The recommended action
  is what should have happened when it was found.
- ATP for the SKU with the most trucks took about 610 ms when last measured, in Step 4, and an order
  holds its locks about that long per line. Orders haven't been load-tested at full scale.
