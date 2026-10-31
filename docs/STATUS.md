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
| Validator: empty data, missing balances, row counts, exit code | `scripts/validate.py` | `test_validator.py` |
| Same seed, same files | `scripts/compare_runs.py` | `test_generator.py` |
| Reserved stock at any past time, from events | `app/atp.py`, `app/rules.py` | `test_orders.py` |
| ATP and promisable, with safety stock and channel buffers | `app/atp.py`, `app/rules.py` | `test_orders.py`, `test_rules.py` |
| Inbound counted by the policy in the plan | `app/atp.py` | `test_orders.py` |
| Allocation: one location, two locations, inbound, reject | `app/rules.py`, `app/orders.py` | `test_orders.py` |
| Two orders can't reserve the same last unit | `app/orders.py` | `test_orders.py` |
| Confirm, cancel and expiry, with history | `app/reservations.py` | `test_orders.py` |

## Partly built

| Feature | What works | What is missing |
|---|---|---|
| Orders | Online orders through function calls and tests | STORE orders raise `NotImplementedError`. No API yet |
| Inbound promises | Hold names a truck, promise is recorded, truck capacity is enforced | Nothing ships yet, so waiting for arrival isn't enforced. Late arrival handling isn't written |
| Replenishment | Rules are calculated, reorders happen | Stores and warehouses get direct deliveries, not transfers |
| Validator | 54 checks pass on generated data | The order checks only ever see orders placed in tests, because the generator makes none |

## Not built

| Feature | Planned step |
|---|---|
| Orders, reservations, allocations and shipments in generated data | 4 |
| 50,000 orders at full scale | 4 |
| Overselling attempts and expired reservations in generated data | 4 |
| Shipping: SALE movement, CONSUMED hold, FULFILLED order | 4 |
| Waiting for inbound arrival and late arrival handling | 4 |
| Reallocation to another location | 4 |
| STORE orders | 4 |
| Transfers and in-transit stock in generated data | 3 |
| Returns | 3 |
| Sequence sync after loading generated orders | 4 |
| Labelled anomalies, decoys, ground truth | 5 |
| Data dictionary | 6 |
| Detectors and scoring | 8 |
| API | 9 |
| React page | 10 |
| README and AI_USAGE.md | 11 |
| Starter dataset committed | After step 5, once the data is final |
