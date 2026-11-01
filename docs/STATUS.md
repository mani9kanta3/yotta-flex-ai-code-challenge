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
| Each case causes only its own problem | `generator/inject.py` | Final data less the labelled rows equals the clean baseline, `test_anomalies.py` |
| Everything numbered in time order after injection | `generator/inject.py` | `test_anomalies.py` |
| Ground truth in its own folder, read only by `eval/` | `generator/export.py`, `eval/` | `test_anomalies.py` |
| Four detectors, from the ledger and events alone | `app/detect.py`, `scripts/run_detect.py` | `test_anomalies.py` |
| Precision and recall per type | `eval/score.py` | `test_anomalies.py` |
| Expected and unexpected validation failures reported apart | `eval/check_failures.py` | `test_anomalies.py` |
| Starter data, ground truth and metrics ready to commit | `backend/data/starter/`, `backend/data/ground_truth/starter/` | Manifest checksums, `.gitattributes` |
| Live numbering carries on after a load | `db/constraints.sql` | `test_order_flow.py` |

## Partly built

| Feature | What works | What is missing |
|---|---|---|
| Orders | Online orders through function calls, tests and generated data | STORE orders raise `NotImplementedError`. No API yet |
| Shipping | One parcel per location, delivery, refusal on a short pick | A short pick is only refused, nothing re-allocates it. An order ships whole, never line by line |
| Late trucks | Handled when the notice comes in, and by `handle_overdue_trucks` | Nothing runs `handle_overdue_trucks` on a schedule yet. That arrives with the API step |
| Replenishment | Rules are calculated, reorders move through the three levels | First stock for a late SKU or new store is a direct delivery, not a transfer. One reorder at a time per pair, instead of the plan's position formula |
| Returns | Good and damaged returns in live code and generated data | No return window |
| Live stock changes | Orders, transfers, shipping, returns and late handling take the SKU-location lock | Walk-in sales, BLOCK and adjustments exist only in the generator, with no live version until the API step |
| Generated decisions agree with SQL | **At full scale, only a sample of 200 decisions was checked against SQL: 200 of 72,139 on the clean baseline and 200 of 72,339 on the final data.** Every decision was checked at starter scale: all 1,443 on the clean baseline agree, and of all 1,463 on the final data, only the 5 labelled ones differ | More than 72,000 full-scale decisions have not been recalculated. `validate --decisions` can run more, at several queries each |
| Ground truth | Anomalies, decoys and oversell attempts | The ATP checkpoints the plan mentioned aren't written. The decision recheck does the same job |

## Not built

| Feature | Planned step |
|---|---|
| Data dictionary | 6 |
| Full validator review and manifest review | 6 |
| Tuning, if the movement mix needs it | 7 |
| API | 9 |
| React page | 10 |
| `run_all` command, README and AI_USAGE.md | 11 |
| STORE orders | No step. Walk-in sales cover the store channel, and the brief doesn't name STORE orders. Build only if time is left |

## Measured so far

Seed 20261022. The final data includes the labelled problems and decoys.

| Number | Starter | Full |
|---|---|---|
| Movements, clean baseline | 19,970 | 999,700 |
| Movements, final | 20,000 | 1,000,000 |
| Orders, clean baseline | 980 | 49,800 |
| Orders, final | 1,000 | 50,000 |
| Oversell attempts | 25 | 1,559 |
| Split allocations | 61 | 2,897 |
| Lines held on inbound stock | 38 | 1,182 |
| Orders that expired | 154 | 7,002 |
| Holds moved off a late truck | 2 | 159 |
| Promises updated for a late truck | 11 | 289 |
| Holds cancelled past the limit | 6 | 156 |
| Generate, all three folders | about 2 s | about 2 min |
| Load, with foreign keys and indexes | about 3 s | about 45 s |
| Validate, including 200 decisions | about 8 s | about 2 min |
| Detect | under 1 s | about 55 s |

**Validation**

| Dataset | Checks passed | Failed | Unexpected | Exit code |
|---|---|---|---|---|
| Starter, clean baseline | 127 of 127 | 0 | 0 | 0 |
| Starter, final | 121 of 127 | 6 | 0 | 1 from the validator, 0 from `check_failures` |
| Full, clean baseline | 127 of 127 | 0 | 0 | 0 |
| Full, final | 122 of 127 | 5 | 0 | 1 from the validator, 0 from `check_failures` |

The failures on the final data, with the same counts as what is labelled (5 at starter, 50 at full):

| Check | Explained by |
|---|---|
| closing balance does not reconcile to opening plus ledger | `BALANCE_NOT_RECONCILED` |
| network stock plus in transit differs from opening plus outside changes | `BALANCE_NOT_RECONCILED` |
| hold above what could be promised when it was placed | `RESERVATION_EXCEEDS_AVAILABLE` |
| order sale from the blocked bucket | `FULFILLED_FROM_BLOCKED` |
| transfer received at or before it shipped | `TRANSFER_DOUBLE_COUNTED` |

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

**Full-scale movements**

| Movement | Rows | Share |
|---|---|---|
| SALE, walk-in | 674,702 | 67.5% |
| SALE, online order | 52,544 | 5.3% |
| TRANSFER_OUT | 82,562 | 8.3% |
| TRANSFER_IN | 81,177 | 8.1% |
| RETURN | 45,485 | 4.5% |
| ADJUSTMENT | 18,825 | 1.9% |
| BLOCK | 14,134 | 1.4% |
| RECEIPT | 12,090 | 1.2% |
| UNBLOCK | 10,620 | 1.1% |
| WRITE_OFF | 7,861 | 0.8% |

- Online returns at full scale: 5,333 rows, 8,549 units.
- On-hand holds shipped by their first promise: 51,641 of 51,885, which is 99.5%.
- Holds on trucks shipped by their first promise: 613 of 659, which is 93.0%. This says nothing
  about real carriers, because late trucks and rush deliveries are set on purpose.

**Where the cases fall.** How far through the data each group sits, as a median at full scale:
order anomalies 0.45, order decoys 0.41, transfer anomalies 0.36, transfer decoys 0.40. With only 5
per type at starter, the groups can differ a lot by chance: transfer anomalies sit at 0.04 there
and transfer decoys at 0.89. So at starter scale, don't read anything into their dates.

**Other limits**

- A labelled case is always on a SKU with no later online orders, because no later decision may
  depend on it. No detector uses that, but it is a pattern a reader could spot.
- To make room for cases, half of the SKUs stop getting online orders at a random date, and opening
  damaged stock went from 1% to 8% of pairs. Both change the data for that reason alone.
- A labelled reservation expires unpaid, so it holds stock only briefly. The recommended action
  is what should have happened when it was found.
- Comparing failure counts alone can't tell a labelled failure from an unlabelled one of the same
  kind. The detector scores and the clean-versus-final test cover that.
- Other seeds: 19 of 20 tried at starter placed every case. The other one stopped with an error
  rather than place a bad case, and 1 of the 19 had no late cancellation, which the validator
  reports as a failure. Only seed 20261022 was run at full scale.
- ATP for the SKU with the most trucks took about 610 ms when last measured, in Step 4, and an order
  holds its locks about that long per line. Orders haven't been load-tested at full scale.
