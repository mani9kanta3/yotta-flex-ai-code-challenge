# Data dictionary

Every table and every field in the generated data. `backend/tests/test_data_dictionary.py`
compares this file with the real database and data, so a field, type or allowed value that
drifts makes a test fail. The tables themselves are in
[schema.sql](../backend/db/schema.sql) and [constraints.sql](../backend/db/constraints.sql).

## Conventions

| Topic | Rule |
|---|---|
| Time | `TIMESTAMP` with no time zone, on one business clock. The simulated period is 2026-01-01 00:00:00 to 2026-09-30 23:59:59. Nothing reads the real clock |
| Dates | `[from, to)`: a range includes its start and excludes its end |
| Quantities | Whole units of one SKU. `inventory_movement.quantity` is signed, everything else is positive or zero |
| Money | Plain numbers with 2 decimals. There is no currency column. Prices are sized like Indian rupees, because the locations are Indian cities |
| NULL | An empty cell in the CSV is NULL in the database |
| Status columns | Only a copy of the latest event. The event tables are the history |
| Derived values | Closing balances, line amounts, order totals and statuses are worked out from source records, never picked at random |

In the tables below, "Required" means the column can't be NULL. "Unique" says `Yes` when this
one field is unique, and `With ...` when it is unique only together with other fields.

## IDs

Counters are zero-padded, never reused and never changed.

| Entity | Format | Example | Comes from |
|---|---|---|---|
| SKU | `SKU-` + 6 digits | `SKU-000001` | Brief |
| Location | `LOC-` + 6 digits | `LOC-000001` | Brief |
| Order | `ORD-2026-` + 6 digits | `ORD-2026-000001` | Brief |
| Reservation | `RES-<order-id>-` + 2 digits | `RES-ORD-2026-000001-01` | Brief. The suffix counts per order |
| Movement | `MOV-2026-` + 8 digits | `MOV-2026-00000001` | Brief |
| Shipment | `SHP-2026-` + 6 digits | `SHP-2026-000001` | Brief |
| Product | `PRD-` + 6 digits | `PRD-000001` | Ours |
| Order line | `<order-id>-L` + 2 digits | `ORD-2026-000001-L01` | Ours |
| Allocation | `ALC-2026-` + 7 digits | `ALC-2026-0000001` | Ours |
| Decision | `DEC-2026-` + 7 digits | `DEC-2026-0000001` | Ours |
| Balance | `BAL-` + 7 digits | `BAL-0000001` | Ours |
| Rule | `RUL-` + 7 digits | `RUL-0000001` | Ours |
| Channel-location | `CHL-` + 6 digits | `CHL-000001` | Ours |
| Exception | `EXC-` + 6 digits | `EXC-000001` | Ours |
| Movement group | `GRP-` + 8 digits | `GRP-00000001` | Ours |
| Event tables | A plain increasing number | `1` | Orders events that share a timestamp |

## Master data

### product

| Field | Type | Required | Allowed values | Units | Unique | Validation rule |
|---|---|---|---|---|---|---|
| product_id | VARCHAR(10) | Yes | `PRD-` + 6 digits | ID | Yes | Primary key |
| product_name | VARCHAR(120) | Yes | Generated text | Text | No | Not empty |
| category | VARCHAR(30) | Yes | One of: `APPAREL`, `FOOTWEAR`, `HOME`, `ELECTRONICS`, `BEAUTY` | Code | No | CHECK and validator |
| brand | VARCHAR(40) | Yes | Invented labels such as `Brand 01` | Text | No | Not empty |
| status | VARCHAR(15) | Yes | One of: `ACTIVE`, `DISCONTINUED` | Code | No | CHECK and validator |
| launch_date | DATE | Yes | Any date | Date | No | Effective date of the product |

### sku

| Field | Type | Required | Allowed values | Units | Unique | Validation rule |
|---|---|---|---|---|---|---|
| sku_id | VARCHAR(10) | Yes | `SKU-` + 6 digits | ID | Yes | Primary key, format check |
| product_id | VARCHAR(10) | Yes | An existing product | ID | No | Foreign key to `product` |
| size | VARCHAR(10) | Yes | Generated: `S`, `M`, `L`, `XL`, `XXL` | Code | No | Not empty |
| colour | VARCHAR(20) | Yes | Generated: `Black`, `White`, `Navy`, `Olive`, `Grey`, `Maroon`, `Teal` | Text | No | Not empty |
| unit_price | NUMERIC(10, 2) | Yes | Above 0 | Money per unit | No | CHECK above 0 |
| status | VARCHAR(15) | Yes | One of: `ACTIVE`, `DISCONTINUED` | Code | No | CHECK and validator |
| effective_from | DATE | Yes | Any date | Date | No | Start of `[from, to)` |
| effective_to | DATE | No | After `effective_from` | Date | No | CHECK. No sale or allocation on or after it |

### location

| Field | Type | Required | Allowed values | Units | Unique | Validation rule |
|---|---|---|---|---|---|---|
| location_id | VARCHAR(10) | Yes | `LOC-` + 6 digits | ID | Yes | Primary key, format check |
| location_name | VARCHAR(80) | Yes | Generated text with a city name | Text | No | Not empty |
| location_type | VARCHAR(10) | Yes | One of: `NDC`, `WAREHOUSE`, `STORE` | Code | No | CHECK and validator |
| region | VARCHAR(10) | Yes | One of: `NORTH`, `SOUTH`, `EAST`, `WEST`, `CENTRAL` | Code | No | CHECK and validator |
| parent_location_id | VARCHAR(10) | No | An existing location | ID | No | Foreign key. Empty only for the NDC, by a CHECK |
| ships_online | BOOLEAN | Yes | true, false | Flag | No | Decides whether a store sells online |
| opened_on | DATE | Yes | Any date | Date | No | Start of `[from, to)` |
| closed_on | DATE | No | After `opened_on` | Date | No | CHECK. Closures aren't generated |

### channel

| Field | Type | Required | Allowed values | Units | Unique | Validation rule |
|---|---|---|---|---|---|---|
| channel_code | VARCHAR(15) | Yes | One of: `WEB`, `APP`, `MARKETPLACE`, `STORE` | Code | Yes | Primary key, CHECK and validator |
| is_online | BOOLEAN | Yes | true, false | Flag | No | False only for `STORE` in the data |
| counts_inbound | BOOLEAN | Yes | true, false | Flag | No | Whether stock on a truck can be promised |
| applies_safety_stock | BOOLEAN | Yes | true, false | Flag | No | Whether safety stock is taken off |
| hold_minutes | INTEGER | No | Above 0 | Minutes | No | CHECK above 0. Empty means no hold is placed |

### channel_location

Bridge: channel N:M location.

| Field | Type | Required | Allowed values | Units | Unique | Validation rule |
|---|---|---|---|---|---|---|
| channel_location_id | VARCHAR(10) | Yes | `CHL-` + 6 digits | ID | Yes | Primary key |
| channel_code | VARCHAR(15) | Yes | One of: `WEB`, `APP`, `MARKETPLACE`, `STORE` | Code | No | Foreign key to `channel`, validator |
| location_id | VARCHAR(10) | Yes | An existing location | ID | No | Foreign key to `location` |
| buffer_units | INTEGER | Yes | 0 or more | Units | No | CHECK 0 or more. Kept back from this channel here |
| effective_from | DATE | Yes | Any date | Date | No | Start of `[from, to)` |
| effective_to | DATE | No | After `effective_from` | Date | No | CHECK |

### sku_location

Bridge: SKU N:M location. Also the row an order locks, so two orders can't take the same last unit.

| Field | Type | Required | Allowed values | Units | Unique | Validation rule |
|---|---|---|---|---|---|---|
| sku_id | VARCHAR(10) | Yes | An existing SKU | ID | With `location_id` | Primary key, foreign key to `sku` |
| location_id | VARCHAR(10) | Yes | An existing location | ID | With `sku_id` | Primary key, foreign key to `location` |
| ranged_from | DATE | Yes | Any date | Date | No | No movement before it |

### replenishment_rule

| Field | Type | Required | Allowed values | Units | Unique | Validation rule |
|---|---|---|---|---|---|---|
| rule_id | VARCHAR(11) | Yes | `RUL-` + 7 digits | ID | Yes | Primary key |
| sku_id | VARCHAR(10) | Yes | A stocked pair with `location_id` | ID | No | Foreign key to `sku_location` |
| location_id | VARCHAR(10) | Yes | A stocked pair with `sku_id` | ID | No | Foreign key to `sku_location` |
| source_location_id | VARCHAR(10) | No | An existing location | ID | No | Foreign key. Empty means bought from a supplier, which is the NDC |
| safety_stock | INTEGER | Yes | 0 or more | Units | No | CHECK. Calculated from the forecast |
| reorder_point | INTEGER | Yes | At least `safety_stock` | Units | No | CHECK. Calculated from the forecast |
| reorder_qty | INTEGER | Yes | Above 0 | Units | No | CHECK |
| lead_time_days | INTEGER | Yes | Above 0 | Days | No | CHECK. 2 store, 3 warehouse, 7 NDC |
| allow_inbound_atp | BOOLEAN | Yes | true, false | Flag | No | Whether trucks here count towards ATP |
| effective_from | DATE | Yes | Any date | Date | No | Start of `[from, to)` |
| effective_to | DATE | No | After `effective_from` | Date | No | CHECK |

### inventory_balance

Snapshots, never updated, enforced by a trigger.

| Field | Type | Required | Allowed values | Units | Unique | Validation rule |
|---|---|---|---|---|---|---|
| balance_id | VARCHAR(11) | Yes | `BAL-` + 7 digits | ID | Yes | Primary key |
| sku_id | VARCHAR(10) | Yes | A stocked pair with `location_id` | ID | With `location_id`, `snapshot_type` | Foreign key to `sku_location` |
| location_id | VARCHAR(10) | Yes | A stocked pair with `sku_id` | ID | With `sku_id`, `snapshot_type` | Foreign key to `sku_location` |
| snapshot_type | VARCHAR(10) | Yes | One of: `OPENING`, `CLOSING` | Code | With `sku_id`, `location_id` | CHECK and validator. Every pair has one of each |
| as_of_at | TIMESTAMP | Yes | Start of the period for OPENING, end for CLOSING | Date and time | No | Validator |
| on_hand | INTEGER | Yes | 0 or more | Units | No | CHECK. CLOSING has to equal OPENING plus the ledger |
| blocked | INTEGER | Yes | 0 up to `on_hand` | Units | No | CHECK. Blocked units are part of on-hand |

## Orders

### orders

| Field | Type | Required | Allowed values | Units | Unique | Validation rule |
|---|---|---|---|---|---|---|
| order_id | VARCHAR(15) | Yes | `ORD-2026-` + 6 digits | ID | Yes | Primary key, format check. Numbered in time order |
| channel_code | VARCHAR(15) | Yes | One of: `WEB`, `APP`, `MARKETPLACE`, `STORE` | Code | No | Foreign key. `STORE` orders aren't generated |
| customer_ref | VARCHAR(15) | Yes | `CUST-` + 6 digits | Code | No | An opaque code. There is no customer table or personal data |
| ship_to_region | VARCHAR(10) | Yes | One of: `NORTH`, `SOUTH`, `EAST`, `WEST`, `CENTRAL` | Code | No | CHECK and validator |
| placed_at | TIMESTAMP | Yes | Inside the period | Date and time | No | No decision comes before it |
| status | VARCHAR(15) | Yes | One of: `PLACED`, `ALLOCATED`, `CONFIRMED`, `FULFILLED`, `CANCELLED`, `REJECTED` | Code | No | Has to equal the latest order event |
| order_total | NUMERIC(12, 2) | Yes | 0 or more | Money | No | Has to equal the sum of lines that weren't rejected |

### order_line

| Field | Type | Required | Allowed values | Units | Unique | Validation rule |
|---|---|---|---|---|---|---|
| order_line_id | VARCHAR(20) | Yes | `<order-id>-L` + 2 digits | ID | Yes | Primary key |
| order_id | VARCHAR(15) | Yes | An existing order | ID | With `line_no` | Foreign key to `orders` |
| line_no | INTEGER | Yes | Above 0 | Count | With `order_id` | CHECK |
| sku_id | VARCHAR(10) | Yes | An existing SKU | ID | No | Foreign key to `sku` |
| qty | INTEGER | Yes | Above 0 | Units | No | CHECK. Allocated in full or not at all |
| unit_price | NUMERIC(10, 2) | Yes | Above 0 | Money per unit | No | CHECK |
| line_amount | NUMERIC(12, 2) | Yes | `qty` times `unit_price` | Money | No | CHECK |
| status | VARCHAR(15) | Yes | One of: `PENDING`, `ALLOCATED`, `SHIPPED`, `CANCELLED`, `REJECTED` | Code | No | Has to equal the latest line event |

### shipment

A customer delivery or a stock transfer, told apart by `shipment_type`.

| Field | Type | Required | Allowed values | Units | Unique | Validation rule |
|---|---|---|---|---|---|---|
| shipment_id | VARCHAR(15) | Yes | `SHP-2026-` + 6 digits | ID | Yes | Primary key, format check |
| shipment_type | VARCHAR(10) | Yes | One of: `CUSTOMER`, `TRANSFER` | Code | No | CHECK and validator |
| status | VARCHAR(15) | Yes | One of: `CREATED`, `IN_TRANSIT`, `DELIVERED`, `RECEIVED`, `CANCELLED` | Code | No | Has to equal the latest shipment event |
| order_id | VARCHAR(15) | No | An existing order | ID | No | Foreign key. Filled for CUSTOMER only, by a CHECK |
| from_location_id | VARCHAR(10) | Yes | An existing location | ID | No | Foreign key, differs from `to_location_id` |
| to_location_id | VARCHAR(10) | No | An existing location | ID | No | Foreign key. Filled for TRANSFER only, by a CHECK |
| created_at | TIMESTAMP | Yes | Inside the period | Date and time | No | Numbered in this order |
| original_eta_at | TIMESTAMP | No | Not before `created_at` | Date and time | No | Validator. The first ETA. A later one is an event |
| transfer_sku_id | VARCHAR(10) | No | An existing SKU | ID | No | Foreign key. Filled for TRANSFER only, by a CHECK |
| transfer_qty | INTEGER | No | Above 0 | Units | No | CHECK. Filled for TRANSFER only |

### reservation

Only facts that never change, plus status as a copy of the latest event.

| Field | Type | Required | Allowed values | Units | Unique | Validation rule |
|---|---|---|---|---|---|---|
| reservation_id | VARCHAR(25) | Yes | `RES-<order-id>-` + 2 digits | ID | Yes | Primary key, format check against its order |
| order_line_id | VARCHAR(20) | Yes | An existing line | ID | No | Foreign key to `order_line` |
| sku_id | VARCHAR(10) | Yes | An existing SKU | ID | No | Foreign key to `sku` |
| location_id | VARCHAR(10) | No | A stocked pair with `sku_id` | ID | No | Foreign key. Empty only on a REJECTED request with no candidate |
| qty | INTEGER | Yes | Above 0 | Units | No | CHECK. Agrees with its allocation |
| supply_type | VARCHAR(10) | Yes | One of: `ON_HAND`, `INBOUND` | Code | No | CHECK and validator. Never a mix of both |
| inbound_shipment_id | VARCHAR(15) | No | An existing transfer | ID | No | Foreign key. Filled only for INBOUND, by a CHECK |
| created_at | TIMESTAMP | Yes | Inside the period | Date and time | No | Has to equal its first event |
| expires_at | TIMESTAMP | Yes | After `created_at`, equal on a REJECTED request | Date and time | No | Validator. An EXPIRED event is dated exactly here |
| promised_ship_by | TIMESTAMP | No | Any time | Date and time | No | The first promise. Has to equal its first event |
| status | VARCHAR(15) | Yes | One of: `ACTIVE`, `CONFIRMED`, `CONSUMED`, `EXPIRED`, `CANCELLED`, `REJECTED` | Code | No | Has to equal the latest reservation event |

### order_allocation

Bridge: order line N:M location. Which location fulfils how much of which line.

| Field | Type | Required | Allowed values | Units | Unique | Validation rule |
|---|---|---|---|---|---|---|
| allocation_id | VARCHAR(16) | Yes | `ALC-2026-` + 7 digits | ID | Yes | Primary key |
| order_line_id | VARCHAR(20) | Yes | An existing line | ID | No | Foreign key to `order_line` |
| location_id | VARCHAR(10) | Yes | An existing location | ID | No | Foreign key. Agrees with its reservation |
| qty | INTEGER | Yes | Above 0 | Units | No | CHECK. Agrees with its reservation |
| reservation_id | VARCHAR(25) | No | An existing reservation | ID | Yes | Foreign key. Empty only for a STORE order, which isn't generated |
| shipment_id | VARCHAR(15) | No | An existing shipment | ID | No | Foreign key. Empty until shipped |
| allocated_at | TIMESTAMP | Yes | Inside the period | Date and time | No | No sale comes before it |

## The ledger

### inventory_movement

Append-only, enforced by a trigger. Each row changes one bucket, so on-hand at any time is the
opening balance plus the rows up to that time.

| Field | Type | Required | Allowed values | Units | Unique | Validation rule |
|---|---|---|---|---|---|---|
| movement_id | VARCHAR(17) | Yes | `MOV-2026-` + 8 digits | ID | Yes | Primary key, format check. Numbered in time order |
| sku_id | VARCHAR(10) | Yes | A stocked pair with `location_id` | ID | No | Foreign key to `sku_location` |
| location_id | VARCHAR(10) | Yes | A stocked pair with `sku_id` | ID | No | Foreign key to `sku_location` |
| movement_type | VARCHAR(15) | Yes | One of: `RECEIPT`, `SALE`, `RETURN`, `TRANSFER_OUT`, `TRANSFER_IN`, `ADJUSTMENT`, `BLOCK`, `UNBLOCK`, `WRITE_OFF` | Code | No | CHECK ties it to a bucket and a sign |
| stock_bucket | VARCHAR(10) | Yes | One of: `SELLABLE`, `BLOCKED` | Code | No | CHECK. A SALE from BLOCKED can load, so a detector can find it |
| quantity | INTEGER | Yes | Not 0 | Signed units | No | CHECK. The change to this one bucket |
| occurred_at | TIMESTAMP | Yes | Inside the period | Date and time | No | Validator. Not before the pair went live |
| reason_code | VARCHAR(30) | Yes | See reason codes | Code | No | Not empty |
| source_doc | VARCHAR(25) | Yes | See document prefixes | Text | No | Each document appears once, except the two rows of a BLOCK or UNBLOCK |
| movement_group_id | VARCHAR(15) | No | `GRP-` + 8 digits | ID | No | Filled only for BLOCK and UNBLOCK, by a CHECK. Exactly two rows that add to zero |
| allocation_id | VARCHAR(16) | No | An existing allocation | ID | No | Foreign key. Filled on an order SALE, empty on a walk-in sale |
| shipment_id | VARCHAR(15) | No | An existing shipment | ID | No | Foreign key. Required on a transfer, by a CHECK |
| related_movement_id | VARCHAR(17) | No | An existing SALE | ID | No | Foreign key. Filled only on a RETURN, by a CHECK. Never more than was sold |

## Decisions and history

All four tables are append-only, enforced by a trigger.

### allocation_decision

| Field | Type | Required | Allowed values | Units | Unique | Validation rule |
|---|---|---|---|---|---|---|
| decision_id | VARCHAR(16) | Yes | `DEC-2026-` + 7 digits | ID | Yes | Primary key |
| order_line_id | VARCHAR(20) | Yes | An existing line | ID | No | Foreign key to `order_line` |
| decided_at | TIMESTAMP | Yes | Inside the period | Date and time | No | Not before its order was placed |
| requested_qty | INTEGER | Yes | Above 0 | Units | No | CHECK |
| decision_code | VARCHAR(20) | Yes | One of: `SINGLE`, `SPLIT`, `INBOUND`, `REJECTED`, `REALLOCATED` | Code | No | CHECK and validator |
| reason_code | VARCHAR(40) | Yes | See reason codes | Code | No | Not empty |
| chosen_location_id | VARCHAR(10) | No | An existing location | ID | No | Foreign key. Empty when no location was chosen |
| atp_seen | INTEGER | No | Any whole number | Units | No | Can be below 0, see the note under ATP |
| promisable_seen | INTEGER | No | 0 or more | Units | No | What the chosen location could promise |
| candidates | JSONB | No | The top three locations and their numbers | JSON | No | Evidence for the decision. Recalculated from SQL for a sample |

### order_event

| Field | Type | Required | Allowed values | Units | Unique | Validation rule |
|---|---|---|---|---|---|---|
| event_id | BIGINT | Yes | A plain increasing number | ID | Yes | Primary key. Breaks ties between events in one second |
| order_id | VARCHAR(15) | Yes | An existing order | ID | No | Foreign key to `orders` |
| order_line_id | VARCHAR(20) | No | An existing line | ID | No | Foreign key. Empty when the event is about the whole order |
| from_status | VARCHAR(15) | No | An order or line status | Code | No | Empty on the first event. Transitions are checked |
| to_status | VARCHAR(15) | Yes | One of: `ALLOCATED`, `CANCELLED`, `CONFIRMED`, `FULFILLED`, `PENDING`, `PLACED`, `REJECTED`, `SHIPPED` | Code | No | Validator, against the allowed transitions |
| occurred_at | TIMESTAMP | Yes | Inside the period | Date and time | No | Events on one record run in time order |
| reason_code | VARCHAR(40) | Yes | See reason codes | Code | No | Not empty |

### reservation_event

| Field | Type | Required | Allowed values | Units | Unique | Validation rule |
|---|---|---|---|---|---|---|
| event_id | BIGINT | Yes | A plain increasing number | ID | Yes | Primary key. Breaks ties between events in one second |
| reservation_id | VARCHAR(25) | Yes | An existing reservation | ID | No | Foreign key to `reservation` |
| from_status | VARCHAR(15) | No | A reservation status | Code | No | Empty on the first event. Has to chain from the event before |
| to_status | VARCHAR(15) | Yes | One of: `ACTIVE`, `CONFIRMED`, `CONSUMED`, `EXPIRED`, `CANCELLED`, `REJECTED` | Code | No | CHECK and validator, against the allowed transitions |
| occurred_at | TIMESTAMP | Yes | Inside the period | Date and time | No | Used to work out what was held at any past time |
| reason_code | VARCHAR(40) | Yes | See reason codes | Code | No | Not empty |
| promised_ship_by | TIMESTAMP | No | Later than the promise before | Date and time | No | Filled only when this event changes the promise |

### shipment_event

| Field | Type | Required | Allowed values | Units | Unique | Validation rule |
|---|---|---|---|---|---|---|
| event_id | BIGINT | Yes | A plain increasing number | ID | Yes | Primary key. Breaks ties between events in one second |
| shipment_id | VARCHAR(15) | Yes | An existing shipment | ID | No | Foreign key to `shipment` |
| from_status | VARCHAR(15) | No | A shipment status | Code | No | Empty on the first event. Has to chain from the event before |
| to_status | VARCHAR(15) | Yes | One of: `CREATED`, `IN_TRANSIT`, `DELIVERED`, `RECEIVED`, `CANCELLED` | Code | No | CHECK and validator, against the allowed transitions |
| occurred_at | TIMESTAMP | Yes | Inside the period | Date and time | No | RECEIVED has to match its TRANSFER_IN to the second |
| reason_code | VARCHAR(40) | Yes | See reason codes | Code | No | Not empty |
| eta_at | TIMESTAMP | No | Any time | Date and time | No | Filled only when this event changes the ETA |

### inventory_exception

What the detectors found. Empty until `scripts.run_detect` runs, and not part of the generated files.

| Field | Type | Required | Allowed values | Units | Unique | Validation rule |
|---|---|---|---|---|---|---|
| exception_id | VARCHAR(10) | Yes | `EXC-` + 6 digits | ID | Yes | Primary key |
| issue_type | VARCHAR(40) | Yes | The four issue types | Code | No | Written only by `app/detect.py` |
| balance_id | VARCHAR(11) | No | An existing balance | ID | No | Foreign key. Exactly one of the four IDs is filled, by a CHECK |
| reservation_id | VARCHAR(25) | No | An existing reservation | ID | No | Foreign key. Exactly one of the four IDs is filled, by a CHECK |
| movement_id | VARCHAR(17) | No | An existing movement | ID | No | Foreign key. Exactly one of the four IDs is filled, by a CHECK |
| shipment_id | VARCHAR(15) | No | An existing shipment | ID | No | Foreign key. Exactly one of the four IDs is filled, by a CHECK |
| expected_value | INTEGER | No | Any whole number | Units | No | What the ledger and events say |
| actual_value | INTEGER | No | Any whole number | Units | No | What the stored record says |
| recommended_action | VARCHAR(60) | Yes | Set per issue type | Code | No | Not empty |
| status | VARCHAR(10) | Yes | `OPEN` or `RESOLVED` | Code | No | CHECK |
| detected_at | TIMESTAMP | Yes | The end of the period | Date and time | No | Fixed, so a run gives the same answer every time |

## Reason codes and document prefixes

Reason codes are plain text with no CHECK, because live code may add more. This is every code
the generator writes.

| Table | Reason codes |
|---|---|
| `inventory_movement` | `CYCLE_COUNT`, `DAMAGE`, `FAILED_INSPECTION`, `FIRST_STOCK`, `ORDER_SHIPPED`, `PASSED_INSPECTION`, `REPLENISHMENT`, `RETURN_DAMAGED`, `RETURN_GOOD`, `SUPPLIER_DELIVERY`, `WALK_IN` |
| `allocation_decision` | `COVERED_FROM_INBOUND`, `COVERED_FROM_ON_HAND`, `INBOUND_LATE_REALLOCATED`, `INSUFFICIENT_ATP`, `NO_CANDIDATE_LOCATION`, `SPLIT_ACROSS_TWO_LOCATIONS` |
| `order_event` | `ALLOCATED`, `COVERED_FROM_INBOUND`, `COVERED_FROM_ON_HAND`, `CUSTOMER_CANCELLED`, `HOLD_EXPIRED`, `INBOUND_LATE_CANCELLED`, `INSUFFICIENT_ATP`, `NO_CANDIDATE_LOCATION`, `NOTHING_AVAILABLE`, `ORDER_RECEIVED`, `PAYMENT_RECEIVED`, `SHIPPED`, `SPLIT_ACROSS_TWO_LOCATIONS` |
| `reservation_event` | `ALLOCATED`, `CUSTOMER_CANCELLED`, `HOLD_EXPIRED`, `INBOUND_LATE_CANCELLED`, `INBOUND_LATE_REALLOCATED`, `INBOUND_LATE_REPROMISED`, `INSUFFICIENT_ATP`, `NO_CANDIDATE_LOCATION`, `PAYMENT_RECEIVED`, `SHIPPED` |
| `shipment_event` | `ARRIVED`, `DELIVERED`, `DISPATCHED`, `ETA_MISSED`, `PACKED`, `TRANSFER_PLANNED` |

| Document prefix | Written by |
|---|---|
| `POS-2026-` | Walk-in sale |
| `SALE-2026-` | Order sale |
| `RCV-2026-` | Receipt |
| `RET-2026-` | Return |
| `TRO-2026-`, `TRI-2026-` | Transfer out, transfer in |
| `DMG-2026-` | Damage found, a BLOCK |
| `INS-2026-` | Inspection result, an UNBLOCK or WRITE_OFF |
| `CC-2026-` | Cycle count, an ADJUSTMENT |

## ATP

```
on_hand(t)    = opening on_hand + all movement rows up to t
blocked(t)    = opening blocked + BLOCKED movement rows up to t
reserved(t)   = holds whose latest event up to t is CONFIRMED, or ACTIVE and t < expires_at
inbound(t)    = trucks on the road at t and due from t to t + 24 hours, where the rule allows it
ATP(t)        = on_hand(t) - reserved(t) - blocked(t) + inbound(t)
promisable(t) = max(0, ATP counted for this channel - safety stock - channel buffer)
```

**ATP can be below zero.** A paid hold on a truck that has passed its ETA still counts as
reserved, but the overdue truck no longer counts as inbound. So on-hand 2 with 15 units held
gives ATP -13 until the truck arrives or the hold is cancelled. `promisable` never goes below
0, so nothing can be promised from it.

## Relationships

Every relationship has a foreign key in `constraints.sql`. `scripts/validate.py` also counts
broken references itself, so the validation summary reports them.

| From | To | Type | Through | Optional? |
|---|---|---|---|---|
| product | sku | 1:N | `sku.product_id` | Required |
| location (parent) | location (child) | 1:N | `parent_location_id` | Optional: the NDC has no parent |
| sku | location | N:M | `sku_location` | Bridge |
| channel | location | N:M | `channel_location` | Bridge |
| sku_location | inventory_balance | 1:N | sku and location | Required. One OPENING and one CLOSING |
| sku_location | inventory_movement | 1:N | sku and location | Required |
| sku_location | replenishment_rule | 1:N | sku and location | Required. N because a rule can be versioned by date |
| location | replenishment_rule | 1:N | `source_location_id` | Optional: the NDC buys from suppliers |
| channel | orders | 1:N | `orders.channel_code` | Required |
| orders | order_line | 1:N | `order_line.order_id` | Required |
| sku | order_line | 1:N | `order_line.sku_id` | Required |
| order_line | location | N:M | `order_allocation` | Bridge. A line can split across locations |
| order_line | reservation | 1:N | `reservation.order_line_id` | Required. Moving a hold creates `-02` |
| sku_location | reservation | 1:N | sku and location | Location optional: a REJECTED request may have no candidate |
| shipment (transfer) | reservation | 1:N | `reservation.inbound_shipment_id` | Optional: INBOUND holds only |
| reservation | order_allocation | 1:0..1 | `order_allocation.reservation_id` | Optional: a rejected hold has no allocation |
| shipment | order_allocation | 1:N | `order_allocation.shipment_id` | Optional: empty until shipped |
| orders | shipment | 1:N | `shipment.order_id` | Optional: transfers have no order |
| location | shipment | 1:N | `from_location_id`, `to_location_id` | `to_location_id` is optional: customer deliveries have none |
| sku | shipment (transfer) | 1:N | `transfer_sku_id` | Optional: transfers only |
| order_allocation | inventory_movement | 1:N | `allocation_id` | Optional: walk-in sales have none |
| shipment | inventory_movement | 1:N | `shipment_id` | Optional |
| inventory_movement (sale) | inventory_movement (return) | 1:N | `related_movement_id` | Optional |
| order_line | allocation_decision | 1:N | `order_line_id` | Required |
| orders, order_line | order_event | 1:N | Two foreign keys | The line is optional |
| reservation | reservation_event | 1:N | Foreign key | Required |
| shipment | shipment_event | 1:N | Foreign key | Required |
| balance, reservation, movement or shipment | inventory_exception | 1:N | Four optional foreign keys | Exactly one is filled, by a CHECK |

## Lifecycle states

The allowed moves are in `app/rules.py`, and the validator checks every event against them.

| Entity | State | Can move to |
|---|---|---|
| Order | (new) | PLACED |
| Order | PLACED | ALLOCATED, REJECTED |
| Order | ALLOCATED | CONFIRMED, CANCELLED |
| Order | CONFIRMED | FULFILLED, CANCELLED |
| Order | REJECTED, FULFILLED, CANCELLED | Final |
| Order line | (new) | PENDING |
| Order line | PENDING | ALLOCATED, REJECTED |
| Order line | ALLOCATED | SHIPPED, CANCELLED |
| Order line | REJECTED, SHIPPED, CANCELLED | Final |
| Reservation | (new) | ACTIVE, REJECTED |
| Reservation | ACTIVE | CONFIRMED, EXPIRED, CANCELLED |
| Reservation | CONFIRMED | CONFIRMED (new promise only), CONSUMED, CANCELLED |
| Reservation | REJECTED, EXPIRED, CONSUMED, CANCELLED | Final |
| Shipment | (new) | CREATED |
| Shipment | CREATED | IN_TRANSIT, CANCELLED |
| Shipment | IN_TRANSIT | IN_TRANSIT (new ETA only), DELIVERED, RECEIVED |
| Shipment | DELIVERED, RECEIVED, CANCELLED | Final |
| Product, SKU | ACTIVE | DISCONTINUED. A plain status, with no event table |
| Location | Dates | `opened_on` and `closed_on` decide it. Closures aren't generated |

**Effective dates:** `product.launch_date`, `sku.effective_from` and `effective_to`,
`location.opened_on` and `closed_on`, `sku_location.ranged_from`, and `effective_from` and
`effective_to` on `channel_location` and `replenishment_rule`.

## Files that aren't loaded

These live outside what the loader reads. The loader reads only the 18 table files by name.

### ground_truth.csv

In `data/ground_truth/<scale>/`. Read only by `backend/eval/`.

| Field | Type | Required | Allowed values | Units | Unique | Validation rule |
|---|---|---|---|---|---|---|
| expected_issue | Text | Yes | The four issue types, or `NONE` for a decoy or an oversell attempt | Code | No | Comes from the rule that made the case |
| looks_like | Text | Yes | The four issue types, or `OVERSELL_ATTEMPT` | Code | No | Says which detector a decoy might fool |
| record_type | Text | Yes | `balance`, `reservation`, `movement`, `shipment` | Code | No | Says which table `record_id` is in |
| record_id | Text | Yes | An ID in that table | ID | Yes | Compared with what the validator and detectors report |
| sku_id | Text | Yes | An existing SKU | ID | No | Compared with the network stock check, which reports SKUs |
| order_id | Text | No | An existing order | ID | No | Empty on balance and transfer cases |
| expected_value | Whole number | Yes | See `app/detect.py` | Units | No | What should have been true |
| actual_value | Whole number | Yes | See `app/detect.py` | Units | No | What the record says |
| expected_decision | Text | Yes | A recommended action, `NO_ACTION` or `REJECT` | Code | No | Compared with the detector's recommendation |

### atp_checkpoints.csv

In `data/ground_truth/<scale>/`. Read only by `backend/eval/`. Two rows per event: one second
before, and the second it happens. SKUs with a labelled case are left out.

| Field | Type | Required | Allowed values | Units | Unique | Validation rule |
|---|---|---|---|---|---|---|
| checkpoint_id | Text | Yes | `CHK-` + 5 digits | ID | Yes | Numbered in the file |
| kind | Text | Yes | `EXPIRY`, `TRUCK_ARRIVAL`, `CUSTOMER_CANCEL`, `LATE_TRUCK_CANCEL` | Code | No | Every kind has to be present |
| moment | Text | Yes | `BEFORE`, `AT` | Code | No | One second apart |
| record_id | Text | Yes | A reservation or transfer shipment | ID | No | The record whose event this is |
| reason_code | Text | Yes | The reason code of that event | Code | No | Not empty |
| event_qty | Whole number | Yes | Above 0 | Units | No | The units on that hold or truck |
| sku_id | Text | Yes | An existing SKU | ID | No | Asked about |
| location_id | Text | Yes | An existing location | ID | No | Asked about |
| channel_code | Text | Yes | An online channel | Code | No | Asked about |
| as_of_at | Date and time | Yes | Inside the period | Date and time | No | Asked about |
| on_hand | Whole number | Yes | 0 or more | Units | No | Has to match `app/atp.py` |
| blocked | Whole number | Yes | 0 or more | Units | No | Has to match `app/atp.py` |
| reserved | Whole number | Yes | 0 or more | Units | No | Has to match `app/atp.py` |
| inbound | Whole number | Yes | 0 or more | Units | No | Has to match `app/atp.py` |
| atp | Whole number | Yes | Any whole number | Units | No | Has to match `app/atp.py` |
| promisable | Whole number | Yes | 0 or more | Units | No | Has to match `app/atp.py` |

### source_manifest.json

One beside each dataset. Written by `generator/export.py`.

| Field | What it holds |
|---|---|
| `generator_command` | The command that recreates this data |
| `seed` | The seed used |
| `dataset` | `final` or `clean baseline` |
| `scale` | Every size setting, including anomalies, decoys and checkpoints per type |
| `period` | Start and as-of time of the simulation |
| `python_version` | The Python that wrote it |
| `data_source` | Says the data is synthetic and no external data was used |
| `generation_counters` | What happened during the run, such as sales refused and orders expired |
| `not_generated_yet` | What the brief allows that this data doesn't contain |
| `files` | Rows and SHA-256 of each table file |
| `ground_truth_file` | Rows and SHA-256 of the answers, final data only. No answers are in the manifest |
| `atp_checkpoints_file` | Rows and SHA-256 of the checkpoints, final data only |

### validation_summary.json

Written beside a dataset by `scripts.validate`.

| Field | What it holds |
|---|---|
| `dataset`, `seed`, `scale` | Which data was checked |
| `failed` | How many checks failed |
| `decisions_checked`, `decisions_total` | How many order decisions were recalculated in SQL, out of how many |
| `not_generated_yet` | Copied from the manifest |
| `checks` | Each check's name, violation count and whether it passed. Five checks and the decision sample also list the IDs that failed |

**These IDs are findings, not answers.** They come from running SQL on the data, and the
validator never reads the ground truth. Nothing reads this file except `eval/check_failures.py`.
