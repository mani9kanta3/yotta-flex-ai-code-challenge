# What is built and what isn't

Updated after every step. "Built" means the code exists, it ran, and tests cover it.
"Not built" means no code exists yet, whatever the plan says.

## Built

| Feature | Where | Evidence |
|---|---|---|
| Docker Postgres, schema, triggers, foreign keys | `docker-compose.yml`, `backend/db/` | `test_database.py` |
| Master data and opening balances | `generator/master.py` | `test_generator.py` |
| Ledger: receipts, walk-in sales, damage, adjustments | `generator/simulate.py` | Replay test, validator |
| Exact movement target with no invalid sale | `generator/simulate.py` | Top-up tests, validator |
| Validator: empty data, missing balances, row counts, missing activity, exit code | `scripts/validate.py` | `test_validator.py` |
| Same seed, same files | `scripts/compare_runs.py` | `test_generator.py` |
| Reserved stock at any past time, from events | `app/atp.py`, `app/rules.py` | `test_orders.py` |
| ATP and promisable, with safety stock and channel buffers | `app/atp.py`, `app/rules.py` | `test_orders.py`, `test_rules.py` |
| Inbound counted by the policy in the plan | `app/atp.py` | `test_orders.py`, `test_transfers.py` |
| Allocation: one location, two locations, inbound, reject | `app/rules.py`, `app/orders.py` | `test_orders.py` |
| Two orders can't reserve the same last unit | `app/orders.py` | `test_orders.py` |
| Confirm, cancel and expiry, with history | `app/reservations.py` | `test_orders.py` |
| Transfers: leave the source, stay in transit, reach the destination, counted once | `app/transfers.py`, `generator/simulate.py` | `test_transfers.py`, `test_generator.py`, validator |
| Late trucks: new ETA recorded, first ETA kept | `app/transfers.py`, `generator/simulate.py` | `test_transfers.py`, validator |
| Stores reorder from warehouses, warehouses from the NDC, the NDC from suppliers | `generator/simulate.py` | `test_generator.py` |
| Orders on a truck wait until it is received | `app/shipping.py` | `test_transfers.py`, validator |
| Returns name a real sale, and never pass what was sold | `app/returns.py`, `generator/simulate.py` | `test_returns.py`, `test_generator.py`, validator |

## Partly built

| Feature | What works | What is missing |
|---|---|---|
| Orders | Online orders through function calls and tests | STORE orders raise `NotImplementedError`. No API yet |
| Shipping | `ship_order`: SALE movement, CONSUMED hold, FULFILLED order, one shipment per location, and refusal on a short pick | Customer shipments stop at IN_TRANSIT, with no delivery step. A short pick is only refused, nothing re-allocates it. An order ships whole, never line by line |
| Inbound promises | Hold names a truck, promise is recorded, truck capacity is enforced, shipping waits for arrival, arrival ships paid orders oldest first | A late truck leaves the order waiting with its old promised date. Re-promising, moving to another location and the 3-day cancellation aren't written |
| Replenishment | Rules are calculated, reorders move through the three levels | First stock for a late SKU or new store is a direct delivery, not a transfer. One reorder at a time per pair, instead of the plan's position formula |
| Validator | 87 checks pass on generated data | Order, reservation and customer shipment checks only ever see orders placed in tests, because the generator makes none |
| Returns | Good and damaged returns in live code and generated data | Generated returns are walk-in sales only. No return window |
| Live stock changes | Orders, transfers and returns take the SKU-location lock | Walk-in sales, BLOCK and adjustments exist only in the generator, with no live version until the API step |

## Not built

| Feature | Planned step |
|---|---|
| Orders, reservations, allocations and customer shipments in generated data | 4 |
| 50,000 orders at full scale | 4 |
| Overselling attempts and expired reservations in generated data | 4 |
| Late arrival handling: re-promise, reallocate, cancel after 3 days | 4 |
| Reallocation to another location | 4 |
| STORE orders | 4 |
| Sequence sync for orders, allocations and decisions after loading generated orders | 4 |
| Labelled anomalies, decoys, ground truth | 5 |
| Data dictionary | 6 |
| Detectors and scoring | 8 |
| API | 9 |
| React page | 10 |
| README and AI_USAGE.md | 11 |
| Starter dataset committed | After step 5, once the data is final |

## Measured so far

Full scale, seed 20261022. These will change when orders are generated in step 4.

| Movement | Rows | Share |
|---|---|---|
| SALE, all walk-in for now | 726,803 | 72.7% |
| TRANSFER_OUT | 82,773 | 8.3% |
| TRANSFER_IN | 81,769 | 8.2% |
| RETURN | 43,250 | 4.3% |
| ADJUSTMENT | 20,234 | 2.0% |
| BLOCK | 15,156 | 1.5% |
| RECEIPT | 11,094 | 1.1% |
| UNBLOCK | 10,830 | 1.1% |
| WRITE_OFF | 8,091 | 0.8% |

- 1,004 trucks carrying 25,104 units are still in transit when the data ends.
- 75,371 of the 81,769 received trucks arrived by their first ETA, which is 92.2%.
- 2,021 sales were returned in two parts.
- 52,866 walk-in sales were refused for lack of stock or because the item wasn't on sale at that store yet, and no row was written for them.
- ATP for the SKU with the most trucks takes about 430 ms, and an order holds its locks for about that long per line. Orders haven't been load-tested at full scale.
