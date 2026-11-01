# Plan (version 2, after review)

The exact requirements are in [brief.md](brief.md). This file replaces the first
plan. Any rule marked **changed** was corrected after review.

Numbers marked "estimate" have not been measured yet. Real numbers go in the
README only after a real run.

## 1. Entities

The ten core entities from the brief, plus nine supporting tables.

| Table | Brief entity | Primary key | Why it exists |
|---|---|---|---|
| `product` | Product | `product_id` | Catalog parent |
| `sku` | SKU | `sku_id` | Sellable variant |
| `location` | Location | `location_id` | NDC, warehouse or store |
| `inventory_balance` | InventoryBalance | `balance_id` | OPENING and CLOSING snapshots, never updated |
| `inventory_movement` | InventoryMovement | `movement_id` | The immutable ledger |
| `reservation` | Reservation | `reservation_id` | Hold on stock for one order line |
| `orders` | Order | `order_id` | `ORDER` is a reserved word in SQL |
| `order_line` | OrderLine | `order_line_id` | One SKU on one order |
| `shipment` | Shipment | `shipment_id` | Customer delivery or stock transfer |
| `replenishment_rule` | ReplenishmentRule | `rule_id` | Safety stock and reorder settings, effective dated |
| `channel` | supporting | `channel_code` | WEB, APP, MARKETPLACE, STORE and their policy flags |
| `channel_location` | bridge | `channel_location_id` | Which channel sells from which location, and its buffer |
| `sku_location` | bridge | `(sku_id, location_id)` | Which SKU is stocked where. Also the row we lock |
| `order_allocation` | bridge | `allocation_id` | Which location fulfils how much of which line |
| `allocation_decision` | supporting | `decision_id` | Why each allocation or rejection happened |
| `order_event` | supporting | `event_id` | Order and line state history |
| `reservation_event` | supporting | `event_id` | Reservation state history |
| `shipment_event` | supporting | `event_id` | Shipment state and ETA history |
| `inventory_exception` | supporting | `exception_id` | What the detectors found |

Ground truth is a separate file and is never loaded into the database.

## 2. IDs

| Entity | Format | Example | Rule |
|---|---|---|---|
| SKU | `SKU-` + 6 digits | `SKU-000001` | Brief |
| Location | `LOC-` + 6 digits | `LOC-000001` | Brief |
| Order | `ORD-2026-` + 6 digits | `ORD-2026-000001` | Brief |
| Reservation | `RES-<order-id>-` + 2 digits | `RES-ORD-2026-000001-01` | Brief, used literally. Suffix counts per order |
| Movement | `MOV-2026-` + 8 digits | `MOV-2026-00000001` | Brief |
| Shipment | `SHP-2026-` + 6 digits | `SHP-2026-000001` | Brief |
| Product | `PRD-` + 6 digits | `PRD-000001` | Ours, same pattern |
| Order line | `<order-id>-L` + 2 digits | `ORD-2026-000001-L01` | Ours |
| Allocation | `ALC-2026-` + 7 digits | `ALC-2026-0000001` | Ours |
| Decision | `DEC-2026-` + 7 digits | `DEC-2026-0000001` | Ours |
| Balance | `BAL-` + 7 digits | `BAL-0000001` | Ours |
| Rule | `RUL-` + 7 digits | `RUL-0000001` | Ours |
| Channel-location | `CHL-` + 6 digits | `CHL-000001` | Ours |
| Exception | `EXC-` + 6 digits | `EXC-000001` | Ours |
| Event tables | Plain increasing number | `1` | Used to order events that share a timestamp |

Orders and movements are numbered only after anomalies are injected, in time order.
That way a high ID can't give away an injected row. All counters are zero-padded,
never reused and never changed.

## 3. Relationships

| From | To | Type | Through | Optional? |
|---|---|---|---|---|
| product | sku | 1:N | `sku.product_id` | Required |
| location (parent) | location (child) | 1:N | `parent_location_id` | Optional: NDC has no parent |
| sku | location | N:M | `sku_location` | |
| channel | location | N:M | `channel_location` | |
| sku_location | inventory_balance | 1:N | sku and location | Required |
| sku_location | inventory_movement | 1:N | sku and location | Required |
| sku_location | replenishment_rule | 1:N | sku and location | Required. N because rules are versioned by date |
| location | replenishment_rule | 1:N | `source_location_id` | Optional: NDC buys from suppliers |
| channel | orders | 1:N | `orders.channel_code` | Required |
| orders | order_line | 1:N | `order_line.order_id` | Required |
| sku | order_line | 1:N | `order_line.sku_id` | Required |
| order_line | location | N:M | `order_allocation` | A line can split across locations |
| order_line | reservation | 1:N | `reservation.order_line_id` | Reallocation creates `-02` |
| sku_location | reservation | 1:N | sku and location | Location optional: a REJECTED request may have no candidate |
| shipment (transfer) | reservation | 1:N | `reservation.inbound_shipment_id` | Optional: only INBOUND reservations |
| reservation | order_allocation | 1:0..1 | `order_allocation.reservation_id` | Both optional: rejected holds have no allocation, STORE allocations have no hold |
| shipment | order_allocation | 1:N | `order_allocation.shipment_id` | Optional: STORE handover has no shipment |
| orders | shipment | 1:N | `shipment.order_id` | Optional: transfers have no order |
| location | shipment | 1:N | `from_location_id`, `to_location_id` | `to_location_id` is optional: customer deliveries have none |
| sku | shipment (transfer) | 1:N | `transfer_sku_id` | Optional: transfers only |
| order_allocation | inventory_movement | 1:N | `allocation_id` | Optional: walk-in sales have none |
| shipment | inventory_movement | 1:N | `shipment_id` | Optional |
| inventory_movement (sale) | inventory_movement (return) | 1:N | `related_movement_id` | Optional |
| order_line | allocation_decision | 1:N | `order_line_id` | Required |
| orders, order_line | order_event | 1:N | FKs | Line is optional |
| reservation | reservation_event | 1:N | FK | Required |
| shipment | shipment_event | 1:N | FK | Required |
| balance, reservation, movement or shipment | inventory_exception | 1:N | Four nullable FKs | Exactly one is set, by a CHECK |

**Effective dates**

| Table | Columns |
|---|---|
| `product` | `launch_date` |
| `sku` | `effective_from`, `effective_to` (optional) |
| `location` | `opened_on`, `closed_on` (optional) |
| `sku_location` | `ranged_from` |
| `channel_location` | `effective_from`, `effective_to` (optional) |
| `replenishment_rule` | `effective_from`, `effective_to` (optional) |

Ranges include the start and exclude the end, written `[from, to)`.

## 4. Lifecycle states

The status column on a parent row is only a copy of its latest event. The events
are the history, and the validator checks the two agree.

| Entity | State | Can move to |
|---|---|---|
| Order | PLACED | ALLOCATED, REJECTED |
| Order | ALLOCATED | CONFIRMED, CANCELLED |
| Order | CONFIRMED | FULFILLED, CANCELLED |
| Order | REJECTED, FULFILLED, CANCELLED | Final |
| Order line | PENDING | ALLOCATED, REJECTED |
| Order line | ALLOCATED | SHIPPED, CANCELLED |
| Order line | REJECTED, SHIPPED, CANCELLED | Final |
| Reservation | (new) | ACTIVE, REJECTED |
| Reservation | ACTIVE | CONFIRMED, EXPIRED, CANCELLED |
| Reservation | CONFIRMED | CONFIRMED (new promised date only), CONSUMED, CANCELLED |
| Reservation | REJECTED, EXPIRED, CONSUMED, CANCELLED | Final |
| Shipment | CREATED | IN_TRANSIT, CANCELLED |
| Shipment | IN_TRANSIT | IN_TRANSIT (new ETA only), DELIVERED, RECEIVED |
| Shipment | DELIVERED, RECEIVED, CANCELLED | Final |
| Product, SKU | ACTIVE | DISCONTINUED |
| Location | From dates | `opened_on` and `closed_on` decide it. Closures are out of scope |

## 5. Rules

### On-hand and blocked (changed)

Each movement row changes one bucket: SELLABLE or BLOCKED.

```
sellable(t) = opening_sellable + SUM(quantity of SELLABLE rows up to t)
blocked(t)  = opening_blocked  + SUM(quantity of BLOCKED rows up to t)
on_hand(t)  = sellable(t) + blocked(t)
```

| Movement | Rows | On-hand change |
|---|---|---|
| RECEIPT | +q SELLABLE | +q |
| SALE | -q SELLABLE | -q |
| RETURN, good | +q SELLABLE | +q |
| RETURN, damaged | +q BLOCKED | +q, because it physically came back |
| TRANSFER_OUT | -q SELLABLE | -q |
| TRANSFER_IN | +q SELLABLE | +q |
| ADJUSTMENT | +q or -q SELLABLE | +q or -q |
| BLOCK (damage found) | -q SELLABLE and +q BLOCKED | **0** |
| UNBLOCK (passed inspection) | -q BLOCKED and +q SELLABLE | **0** |
| WRITE_OFF | -q BLOCKED | -q |

BLOCK and UNBLOCK always write two rows with the same `movement_group_id`, and one
helper function writes them together. The validator checks every group has exactly
two rows, the same SKU, location and time, and quantities that add to zero.

Example: sellable 95, blocked 5, on-hand 100. Block 3. Now sellable 92, blocked 8,
**on-hand still 100**. ATP falls by 3, because `blocked` rose.

### Reservations at a past time (changed)

The first version read current status and summary columns, which is wrong for a past
time. A reservation cancelled yesterday was still holding stock last week.

Now the reservation row only stores facts that never change: SKU, location, qty,
created_at, expires_at. Its state at time `t` comes from its events:

```
state_at(r, t) = to_status of the latest reservation_event of r
                 with occurred_at <= t, ties broken by event_id

reserved(t) = SUM(r.qty) where
              state_at(r, t) = CONFIRMED
              OR (state_at(r, t) = ACTIVE AND t < r.expires_at)
```

A reservation with no event yet at `t` has no state, so it holds nothing. The
`t < expires_at` test means a late expiry job can't overstate reserved stock. The
EXPIRED event is written with `occurred_at = expires_at` even if the job runs later.
A confirmation at or after `expires_at` is refused.

Example, 5 units, expires_at 10:30:

| Event | Time |
|---|---|
| ACTIVE | 10:00 |
| CONFIRMED | 10:20 |
| CANCELLED | 11:00 |

| Asked at | State then | Reserved |
|---|---|---|
| 09:59 | None | 0 |
| 10:10 | ACTIVE, before expiry | 5 |
| 10:45 | CONFIRMED, so expiry no longer applies | 5 |
| 11:00 | CANCELLED | 0 |

A second one, never confirmed: ACTIVE at 10:00, EXPIRED at 10:30. Asked at 10:29
gives 5, and asked at 10:30 gives 0.

### Inbound and ATP (changed)

Shipment state and ETA at `t` also come from events.

```
inbound(t) = SUM(transfer_qty) of TRANSFER shipments to this location where
             state_at(shipment, t) = IN_TRANSIT
             AND t <= eta_at(shipment, t) <= t + 24 hours
             AND the rule active at t has allow_inbound_atp = true

ATP(t)        = on_hand(t) - reserved(t) - blocked(t) + inbound(t)
promisable(t) = max(0, ATP(t) - safety_stock(t) - channel_buffer(t))
network(t)    = SUM(promisable(t)) across eligible locations
```

An overdue shipment (ETA already passed) does not count as inbound.

| Channel | Inbound counts | Safety applies | Buffer | Hold |
|---|---|---|---|---|
| WEB | Yes | Yes | From `channel_location` | 30 min |
| APP | Yes | Yes | From `channel_location` | 30 min |
| MARKETPLACE | No | Yes | Larger | 120 min |
| STORE, walk-in | No | No | 0 | None |

```
walk_in_available(t) = on_hand(t) - blocked(t) - reserved(t)
```

Example: on-hand 100, blocked 5, reserved 12, inbound 20, safety 10. ATP = 103.
WEB with buffer 3 gets 90. MARKETPLACE with buffer 5 gets 68. Walk-in gets 83.

**Tradeoff:** `reserved` includes holds on inbound stock, which slightly understates
what is free on the shelf. That can lose a sale but can't oversell.

### Allocation order

Rank eligible locations: same region, then warehouse, then NDC, then store, then most
promisable, then lowest ID. Then try these in order:

1. One location covers the whole line from on-hand stock.
2. Two locations cover it from on-hand stock.
3. One location covers the whole line from one inbound shipment.
4. Reject the line. Keep a REJECTED reservation and a decision row.

No partial quantity on a line. **Tradeoff:** simple, but a customer asking for 5 when
4 exist gets none.

### Orders using inbound stock (changed)

A reservation has `supply_type` ON_HAND or INBOUND, and it is never a mix of both.

1. An INBOUND reservation names one shipment in `inbound_shipment_id`. The total held
   against a shipment can't exceed its `transfer_qty`.
2. **Promised date.** `promised_ship_by` is stored on the reservation:
   ON_HAND uses `placed_at + 24h`, INBOUND uses `current ETA + 24h`.
3. **Shipping waits.** An INBOUND reservation can't ship until its shipment is
   RECEIVED at the time being asked about. After a truck is received,
   `ship_waiting_orders` ships the paid orders on it, oldest first, and a SALE can't
   post before the TRANSFER_IN. All the holds of an order ship together, so one line
   on a truck holds up the whole order. **Tradeoff:** one parcel per location, but
   a customer can wait longer for the items already on the shelf.
4. **Late arrival (changed).** When the ETA passes without arrival, the shipment gets a
   new event with status IN_TRANSIT, reason ETA_MISSED and a new ETA. Then for each paid
   reservation on the truck, oldest first, `rules.late_truck_action` picks the first of:
   - The first promise can still be kept: nothing changes.
   - One location has the whole quantity on hand: the old reservation is CANCELLED with
     reason INBOUND_LATE_REALLOCATED, and a new `-02` reservation is created, already
     CONFIRMED because the customer has paid.
   - The new promise is more than 3 days after the first promise: CANCELLED with reason
     INBOUND_LATE_CANCELLED, which releases the hold. Exactly 3 days is still allowed.
   - The promise has slipped: a CONFIRMED to CONFIRMED event with reason
     INBOUND_LATE_REPROMISED carries the new date.

   The first promise stays on the reservation row and each later one is an event, so
   history shows them all. The limit counts from the first promise, so small repeated
   delays can't stretch it. A waiting hold looks for another location again every day,
   because stock may turn up after the first notice.

   **Not reserved twice:** the old hold is cancelled and the new one created at one
   timestamp in one transaction, with the same locks as a new order. So no moment shows
   both holding stock. The stock is read before the old hold lets go, so a decision can't
   count units it is about to give up, which can miss a move but can't oversell.
5. **Measured:** promises kept on time, re-promised, reallocated and cancelled.

### Concurrent allocation (changed)

Two orders must not both take the last unit. The lock target is one row in
`sku_location` per stocked SKU at a location.

1. `BEGIN` at READ COMMITTED.
2. Find the candidate SKU-location pairs from **master data only**, for every line.
   Read no stock numbers yet.
3. Remove duplicates and sort by `sku_id`, then `location_id`.
4. Run one statement:
   `SELECT ... FROM sku_location WHERE (sku_id, location_id) IN (...)
   ORDER BY sku_id, location_id FOR UPDATE`.
5. **After** that returns, recalculate availability in new statements. At READ
   COMMITTED each statement gets a fresh snapshot, so it sees whatever the previous
   lock holder committed. A number read before the lock is never reused.
6. Decide, then insert order, lines, reservations, allocations, events and decisions.
7. `COMMIT`, which releases the locks.

Between steps 5 and 6, the order's time is moved up to the latest event on the locked
rows if it is earlier. Otherwise an order stamped one second before a hold that already
committed would look back to before that hold existed, and take the same stock.

Confirm, cancel and expiry only keep or release stock, so they lock the order row
instead. All the holds of one order move together, because they share a payment and an
expiry time.

**Why the sort matters:** without it, order A could lock X then wait for Y while order
B locks Y then waits for X, which is a deadlock. With one global order, everyone asks
for X first.

**Every writer that reduces availability takes the same lock first:** new orders,
walk-in sales, BLOCK and negative ADJUSTMENT through the API.

**Failure path:** `lock_timeout` is 5 seconds. On timeout the transaction rolls back
and the API returns 503 with "try again", and nothing is half written.

**Tradeoff:** locking every candidate of a SKU slows parallel orders for the same SKU,
but other SKUs are unaffected. Locking only the chosen location would let a candidate's
stock change while we compare them.

Tests: 20 threads racing for 1 unit gives exactly one winner, and two orders listing
the same SKUs in opposite order finish without a deadlock.

### Exactly 1,000,000 movements without invalid sales (changed)

Sales never fill a count by force. Every walk-in sale goes through one function:

```
qty <= walk_in_available(t)   otherwise it is refused and no row is written
```

Refused attempts are counted as lost sales, and the generator keeps going.

1. `rows_left = target - rows written - planned injected rows`.
2. Rows already promised (due receipts, inspections, shipments, arrivals) are counted
   in `pending_rows` before anything optional is allowed. Those rows are valid, so they
   always post.
3. Optional events (walk-in sale, adjustment, new reorder, damage) need
   `rows_left - pending_rows >= rows needed`. A BLOCK needs 2.
4. Each day's walk-in quota is `free rows x today's weight / weights left`, so shortfalls
   roll forward and the final remainder stays small.
5. **Final top-up:** after the last day's events, try more walk-in sales through the same
   check. When tries run out, post a RECEIPT at a warehouse (a real, valid event), since
   that needs no stock and creates stock for later sales.
6. If it still can't reach the target, or the clock would pass `AS_OF`, the generator
   **stops with an error** rather than write an invalid row.

The manifest reports how many rows came from the top-up. The validator replays the
ledger in order and checks that no bucket ever goes below zero, and no walk-in sale was
above availability.

### Replenishment

```
safety_stock  = max(1, ceil(forecast_daily x 3))
reorder_point = safety_stock + ceil(forecast_daily x lead_time_days)
reorder_qty   = max(minimum batch, ceil(forecast_daily x 14))
position      = on_hand - blocked - reserved + all in-transit to this location
```

Reorder when `position <= reorder_point`. Lead times: store from warehouse 2 days,
warehouse from NDC 3, NDC from supplier 7.

**As built:** the simulator keeps one yes-or-no `on_order` flag per pair in place of
adding up in-transit stock, so a pair has one reorder out at a time. A truck leaves the
moment the reorder fires. The source keeps its own safety stock back and sends what it
can spare, and if it can spare nothing, no truck leaves and the source is asked to refill
itself. A refused walk-in sale asks again, so a store can't stay empty for good.

**Changed:** the plan said to use `heapq` for an event queue. Two simpler things do the
job. A dict of days holds what is due on a later day, like a truck or a return. A sorted
list holds what is due at an exact time, like a hold expiring or a customer paying, and
the clock runs those as it passes them. So every decision reads the stock as it was at
that very moment. Actions in the list never write a movement, so they can't touch the
budget or run into each other.

### Generated orders

Randomness only decides what a customer asks for and does, and a rule decides the rest.

| Input, decided at random | What follows from a rule |
|---|---|
| Channel, region, SKUs, quantity | The allocation decision, using `rules.allocate`, the same code the live orders use |
| Pays, cancels or never responds | CONFIRMED, CANCELLED, or EXPIRED at exactly `expires_at` |
| Asks for more than the network can promise | REJECTED with reason INSUFFICIENT_ATP, or NO_CANDIDATE_LOCATION if nothing stocks it |
| Asks for one more than the best location has | SPLIT if two locations cover it |
| Asks for more than two locations have, with a truck due | INBOUND if the truck covers it |
| Truck runs late, by how long | Reallocate, re-promise or cancel, by the rule above |
| Comes back after delivery, how many | RETURN against that SALE, up to what was sold |

**Exact counts:** each order not placed yet holds back 2 rows from the budget, and a paid
order promises one row per hold. The orders per day follow the same weekly pattern as the
walk-ins. Each day takes its share of what is left, so the last day's share is all of it.
Any rows still free at the end go to the
same top-up as before, so both counts land exactly, and no sale is forced.

**Same rules, two places:** `generator/order_flow.py` follows the same steps as the live
code in `app/`, on running counts instead of the database. The validator recalculates a
sample of 200 decisions in SQL at each scale and runs the same rule on them, and the tests
do it for every decision in their dataset. A difference fails the run.

**Tradeoffs:** a truck someone is waiting on runs late more often (35%) than one nobody is
waiting on (8%), because too few orders would meet a late truck otherwise. When a paid order
is left waiting, the NDC rushes a delivery in half the time, so moving a hold doesn't depend on
luck. The late-truck rates therefore say nothing about real carriers. An order placed on the last day can still
be open when the data ends, as it would be in a real extract.

### Transfers (changed)

TRANSFER_OUT posts at `shipped_at` and reduces the source. The stock is then at neither
location. TRANSFER_IN posts at `received_at` and adds to the destination.

```
network on_hand + in_transit
    = opening + receipts - sales + returns + adjustments - write-offs
```

**Why nothing is counted twice:**

| Moment | Source on-hand | Destination on-hand | Destination inbound | On the road |
|---|---|---|---|---|
| Before it ships | 10 | 0 | 0 | 0 |
| From `shipped_at` | 6 | 0 | 4 | 4 |
| From `received_at` | 6 | 4 | 0 | 0 |

The TRANSFER_IN row and the RECEIVED event share one timestamp and one transaction. Inbound
reads the event and on-hand reads the row, and both ask "up to time t", so at any t the truck
is one or the other. The validator checks the two timestamps are equal.

Inbound is a promise rule and on the road is a physical fact, so they can differ. A truck due
beyond 24 hours, or overdue, stops counting as inbound but is still on the road, so the total
above still adds up. A truck due after the data ends just stays IN_TRANSIT, with no TRANSFER_IN.

Shipping a truck takes the same lock as placing an order, because both reduce what the source
can promise. A truck can't take stock held for an order or blocked stock. Receiving locks the
shipment row, so two people receiving one truck add the stock once. A truck can't arrive at
or before its last recorded event, which is what stops the live code ever making the
`TRANSFER_DOUBLE_COUNTED` anomaly.

**Tradeoff:** the source's stock leaves at the moment of the reorder, with no picking or
loading time. That is simple, but real warehouses have a gap there.

### Returns (changed)

```
total returned for a sale <= quantity sold
```

- A RETURN row always names its SALE in `related_movement_id`. The database refuses a return
  with no link, and a link on any other movement type.
- That the linked row really is a SALE, and the total, need other rows, so a CHECK can't see
  them. `returns.py` refuses both, and the validator checks both.
- It goes back to the location that sold it, strictly after the sale.
- A good item goes to SELLABLE. A damaged one goes to BLOCKED, so on-hand rises and ATP doesn't.
  In generated data it then gets the same inspection as other damage.
- Two returns of one sale could each see room for themselves, so a return takes the
  SKU-location lock first and only then counts what has come back.

**Tradeoff:** there is no return window, so a sale could come back months later.

### Expiry

The hold covers `[created_at, expires_at)`. Expiry never writes a movement, because
nothing physically moved. Only a reservation event is written.

## 6. The four anomalies

Each one is self-contained and passes every FK. Only its business meaning is wrong.

| Issue | What gets injected | What stays untouched | How it's detected | Recommended action |
|---|---|---|---|---|
| `BALANCE_NOT_RECONCILED` | One stored CLOSING `on_hand` changed by 1 to 3 units | All movements | Stored value differs from opening + ledger | Recount. ATP keeps using the ledger |
| `RESERVATION_EXCEEDS_AVAILABLE` | New order with a hold 1 to 3 units above what could be promised. Its decision row shows a stale ATP, as if a cache was out of date. **Changed:** it expires unpaid rather than staying CONFIRMED, so it holds stock only briefly and looks like thousands of other expired orders | All other reservations | Recalculate from the ledger and events at `created_at`, without trusting the log | Cancel or reallocate |
| `FULFILLED_FROM_BLOCKED` | New order whose SALE draws from the BLOCKED bucket, as if a damaged bin was scanned | Sellable stock | SALE row with bucket BLOCKED | Quarantine and review |
| `TRANSFER_DOUBLE_COUNTED` | New transfer whose TRANSFER_IN is 1 to 6 hours before its TRANSFER_OUT | All other transfers | IN at or before OUT on the same shipment | Exclude from ATP until received |

**Decoys** are valid rows that look suspicious. They appear in ground truth with
`expected_issue = NONE`, and a detector that flags one counts as a false positive.

| Decoy for | Example |
|---|---|
| Balance | A pair with real movements that net to exactly zero |
| Reservation | Quantity exactly equal to what could be promised |
| Blocked | SALE from SELLABLE at a location that also holds blocked stock |
| Transfer | Received 1 second after shipping |

**Each case causes only its own problem.** A row added in the past changes the answer to every
later question about that SKU, so a case has to fit these rules or it isn't placed:

1. Each SKU takes one case at most, so cases can't interact.
2. A case goes after the SKU's last order activity, with no truck on the road for it, so no
   later order decision depends on the changed numbers.
3. Stock a case takes has to still be there at every later row in the ledger.
4. An order case only goes where the allocation rule would really pick that location.

If not enough SKUs fit, the generator stops with an error rather than place a bad case.

**Spreading cases over the period.** Rule 2 alone put them about 95% of the way through the
data, because ordinary orders touch almost every SKU until near the end, and the date would
have given them away. So half of the SKUs stop getting online orders at a random date, and a
case goes within 14 days after a SKU's last order.

**Tradeoff:** a labelled case is always on a SKU with no later orders. No detector uses that,
but it is a pattern a reader could spot.

**Order of work** (brief, step 5: baseline first, then edge cases):

1. Simulate the clean baseline, smaller than the target by exactly what gets injected, and
   write it to `data/<scale>_clean/`.
2. Add every case, then number all IDs and source documents again in time order, so a high
   ID can't give one away.
3. Write the final data to `data/<scale>/`, and the answers to `data/ground_truth/<scale>/`.

| Added per case | Movements | Orders |
|---|---|---|
| Balance, anomaly or decoy | 0 | 0 |
| Reservation, anomaly or decoy | 0 | 1 |
| Blocked, anomaly or decoy | 1 | 1 |
| Transfer, anomaly or decoy | 2 | 0 |

At full scale that is 300 movements and 200 orders, so the baseline is 999,700 and 49,800,
and the final data is exactly 1,000,000 and 50,000.

**Expected and unexpected failures.** The validator doesn't know what was injected, so it
reports every failure on the final data and exits 1. `eval/check_failures.py` then reads
that report next to the ground truth. A failure is expected only if labelled records
explain its exact count. Anything else is unexpected, including a labelled problem
the validator missed, and that command exits 1.

## 7. Scale

| Setting | starter | full |
|---|---|---|
| SKUs | 300 | 5,000 |
| Locations | 12 | 100 |
| Movements | 20,000 | 1,000,000 |
| Orders | 1,000 | 50,000 |
| Anomalies per type | 5 | 50 |
| Decoys per type | 5 | 50 |
| Store assortment share | 25% | 9% |

```
python -m scripts.generate --scale starter --seed 20261022
python -m scripts.generate --scale full --seed 20261022
```

`--skus`, `--locations`, `--movements` and `--orders` override a preset. Everything else
is a ratio, so both scales run the same code.

Target mix at full scale. **These are estimates.**

| Type | Approx share |
|---|---|
| Walk-in sales | 69% |
| Order sales | 11% |
| Transfers | 9% |
| Block, unblock, write-off | 4% |
| Returns | 4% |
| Adjustments | 2% |
| Receipts | 1% |

Walk-in sales have no order. 50,000 orders produce about 110,000 sales (estimate), so
most movements must be sales with no order, which is also how a real store works.

## 8. Checked against the brief

| Brief says | Where it is | How we prove it |
|---|---|---|
| 10 core entities | Section 1 | `test_database.py` checks each table exists |
| PK for every entity | Section 1 | `test_database.py` checks every table has a PK |
| FK for every relationship | Section 3, `constraints.sql` | Schema load, validator anti-joins |
| Cardinality, optional | Section 3 | This table, data dictionary |
| Effective dates | Section 3 | Validator date checks |
| Lifecycle states | Section 4 | Validator replays transitions |
| Bridge tables | `sku_location`, `channel_location`, `order_allocation` | Schema |
| Event history | Three event tables | Validator: status equals latest event |
| 6 ID formats | Section 2 | Regex check on every row |
| 5,000 / 100 / 1M / 50k | Section 7 | Row counts at full scale |
| Overselling attempts | Allocation step 4 | Count, later step |
| Expired reservations | Expiry rule | Count, later step |
| Damaged stock | BLOCK rule | Count, in the first small run |
| In-transit transfers | Transfer rule | Count, later step |
| Channel buffers | `channel_location` | Row data, in the first small run |
| 4 edge cases | Section 6 | Ground truth and detection, later step |
| Balances reconcile | Validator | First small run |
| ATP follows rules | Section 5 | SQL against simulator, later step |
| Expiry releases stock | Section 5 | Test, later step |
| Allocations traceable | `order_allocation` | Trace metric, later step |

## 9. Build order

The Docker setup and correctness tests are required, and the cut list no longer
includes them.

| Step | Build | Estimate |
|---|---|---|
| 1 | Docker, schema, setup, small generator run: master data, opening balances, receipts, walk-in sales, damage, exact count, load, validate, tests | 2.5h |
| 2 | `rules.py` in full: reserved from events, inbound, ATP, allocation, tests | 1.5h |
| 3 | Transfers, returns and shipping in live code, transfers and returns in the simulator | 2h |
| 4 | Simulator: orders, reservations, inbound promises, customer shipments, online returns. Live and simulated: late arrival handling | 3h |
| 5 | Injection, ground truth, final numbering, detectors and scoring, moved up from step 8 | 2h |
| 6 | Full validator, data dictionary, manifest | 1.5h |
| 7 | Full 1M run, tuning, reproducibility | 1h |
| 8 | Done in step 5 | 0h |
| 9 | API with locked order flow and race tests | 2h |
| 10 | React page | 1.5h |
| 11 | Phase 3 check, clean run, README, AI_USAGE | 3h |

Our own code freeze is Sunday 1 November, 10 PM. The hard deadline is Monday 2 November,
1:14 AM IST.

**Cut first if late, in order**

1. React page, leaving FastAPI `/docs` and the metrics file.
2. Mid-period rule and buffer versions.
3. Reallocation on late inbound, keeping re-promise and cancel.
4. SKU launches and discontinuations mid-period.

**Never cut:** full-scale generation, the four anomalies, ground truth, validation,
reproducibility, Docker setup, correctness tests including the race test, README,
prompt history.

## 10. Architecture and folders

```
Generator (plain Python)  ->  CSV files + ground_truth (kept apart) + manifest
        |
Load via COPY  ->  PostgreSQL  ->  Validator  ->  validation_summary
                       |
                  Detector (never sees ground truth)  ->  inventory_exception
                       |                                       |
                  FastAPI  ->  React page              eval/score.py compares with ground truth
```

FastAPI with raw SQL through psycopg2, and React with Bootstrap on one page, the same
setup as the SQL Agent project. Bulk COPY and heavy SQL gain little from an ORM, so no
Django.

| Path | What it does | Status |
|---|---|---|
| `docker-compose.yml` | Postgres only, on port 5434 | Built |
| `docs/brief.md` | The brief, word for word | Built |
| `docs/PLAN.md` | This plan | Built |
| `docs/requirements_checklist.md` | Every requirement, marked in Phase 3 | Built |
| `docs/data_dictionary.md` | Every field | Later |
| `prompt-history/` | Prompts, by phase | Started |
| `backend/db/schema.sql` | Tables, PKs, CHECKs, immutability triggers | Built |
| `backend/db/constraints.sql` | FKs and indexes, added after load | Built |
| `backend/app/config.py`, `db.py` | Settings and connections | Built |
| `docs/STATUS.md` | What is built and what isn't | Built |
| `backend/app/rules.py` | Pure stock, ATP and allocation rules | Built |
| `backend/app/atp.py` | ATP in SQL, as of any time | Built |
| `backend/app/orders.py` | Place an order with the locking in section 5 | Online orders built, STORE not built |
| `backend/app/reservations.py` | Confirm, cancel, expire | Built |
| `backend/app/movements.py` | Writes one ledger row | Built |
| `backend/app/transfers.py` | Ship, delay and receive a truck | Built |
| `backend/app/shipping.py` | Ship a paid order, wait for its truck, record delivery | Built |
| `backend/app/late_trucks.py` | Move, re-promise or cancel holds on a late truck | Built |
| `backend/app/returns.py` | A return that names its sale | Built |
| `backend/app/detect.py` | The four detectors, SQL only | Built |
| `backend/app/trace.py` | Order to movement chain | Later |
| `backend/app/main.py` | The API | Later |
| `backend/generator/settings.py` | Scale presets and ratios | Built |
| `backend/generator/master.py` | Products to opening balances | Built |
| `backend/generator/simulate.py` | The ledger, day by day | Built |
| `backend/generator/order_flow.py` | Generated online orders, same rules as `app/` | Built |
| `backend/generator/inject.py` | Labelled anomalies and decoys, final numbering, ground truth rows | Built |
| `backend/generator/export.py` | CSV, checksums, manifest | Built |
| `backend/scripts/generate.py` | The generator command | Built |
| `backend/scripts/setup_database.py` | Create database and tables | Built |
| `backend/scripts/load_data.py` | COPY in parent-first order | Built |
| `backend/scripts/validate.py` | SQL checks, writes the summary, exits 1 on failure | Built for what is generated so far |
| `backend/scripts/show_atp.py` | Prints ATP numbers until the API exists | Built |
| `backend/scripts/compare_runs.py` | Two runs, same seed, compare | Built |
| `backend/scripts/run_detect.py` | Runs the detectors, fills `inventory_exception` | Built |
| `backend/scripts/run_all.py` | Everything in one command | Later |
| `backend/eval/score.py` | Precision, recall, `metrics.json` | Built |
| `backend/eval/check_failures.py` | Sorts validator failures into expected and unexpected | Built |
| `backend/tests/` | Tests | Started |
| `backend/data/starter/` | Final starter data, committed | Built |
| `backend/data/ground_truth/starter/` | Starter answers and metrics, committed | Built |
| `backend/data/full/`, `*_clean/`, `ground_truth/full/` | Gitignored, regenerated by command | Works |
| `frontend/src/` | One page | Later |

Libraries are `psycopg2-binary`, `python-dotenv` and `pytest` so far. FastAPI, uvicorn,
pydantic and httpx join in the API step. No pandas, numpy or Faker, because the standard
library's `random` and `csv` are enough and easier to explain.

## 11. Outcomes and ground truth

Randomness only decides inputs, such as whether a customer pays and when. The status
then follows from a rule. No payment before `expires_at` means EXPIRED.

`ground_truth.csv` holds the injected anomalies with expected and actual values and the
expected decision, the decoys, and the oversell attempts with their expected rejection.

**Changed:** the ATP checkpoints planned here aren't written. The validator recalculates order
decisions in SQL instead, which is the same comparison of two separate calculations. At full
scale it checks a sample of only 200 decisions. Every decision was checked at starter scale.

How it stays out of the input data:

- It is in a separate folder and is never loaded into Postgres.
- Only `eval/` reads it, and a test fails if `ground_truth` appears in `app/` or in the
  loader, the validator or the detector command.
- No input table has an `issue_type` column. `inventory_exception` has one, but that is
  detector output.
- Orders, shipments, allocations, movements and source documents are numbered after
  injection, in time order, so an ID can't give a row away.
- Reason codes on injected rows look normal.

**Honest limit:** the same person writes both the anomalies and the detectors, so 100%
recall shows consistency rather than real-world accuracy. ATP checkpoints are stronger
evidence, because two separate calculations must agree.

## 12. What the solution exposes

| Endpoint | For | Shows |
|---|---|---|
| `GET /api/atp` | Customer, analyst | Every term of the formula per location, plus network total |
| `POST /api/orders` | Customer | Decision and reservation, or rejection with reason |
| `POST /api/reservations/expire` | Operations | Holds released, with ATP before and after |
| `GET /api/orders/{id}/trace` | Everyone | Order, line, decision, allocation, reservation, shipment, movement, events |
| `GET /api/movements` | Operator | Ledger with running balance for a SKU and location |
| `GET /api/exceptions` | Ops manager, operator | Expected, actual and recommended action |
| `GET /api/metrics` | Ops manager, analyst | The last measured results |
| `GET /api/health` | Reviewer | Database and row counts |

Metrics, all measured: pairs reconciled, ATP checkpoint match rate, single, split and
rejected rates, oversell attempts blocked, short picks, expiry rate, promises kept,
re-promised and cancelled, allocation and shipment traceability, detector precision,
recall and F1, false positives on decoys, movement and channel mix, top SKU concentration,
generate, load and query times, and checksum comparison.

## 13. Testing

| Layer | Examples | Database? |
|---|---|---|
| Rules | ATP example, reserved at past times, expiry boundary, inbound gate, allocation ranking | No |
| Generator | Exact counts, same seed same files, no negative stock, top-up refuses bad sales, no leak | No |
| Database | Triggers refuse changes, SQL ATP matches rules, expiry releases stock, trace complete | Skips if down |
| Validator | Breaks data on purpose in a transaction, then checks it is caught | Skips if down |
| Concurrency | 20 threads for 1 unit gives one winner, opposite line order gives no deadlock | Skips if down |
| API | Each endpoint with TestClient | Skips if down |

Database tests use their own `inventory_test` database and can't touch a real load.

## 14. Risks and unclear points

| Risk | What we do |
|---|---|
| Less time than the "72 hours" suggests | Sunday 10 PM freeze and the cut list |
| The simulator is the hardest part to explain | No event queue, only a dict of what is due each day and a sorted list of what is due at an exact time |
| Injected rows damage later rows | Replay safety check; the validator must find only labelled issues |
| Results look too perfect | Decoys and plain limits in the README |

| # | Unclear in the brief | Decision |
|---|---|---|
| 1 | 1M movements but only 50k orders | Walk-in sales have no order |
| 2 | InventoryBalance is "master data" but must not overwrite history | Append-only snapshots |
| 3 | Formula ignores safety stock and buffers | ATP as written, plus `promisable` |
| 4 | "Policy-approved inbound" isn't defined | In transit, ETA within 24h, rule allows, channel counts it |
| 5 | Does 1M include anomalies? | Yes, totals are exact including them |
| 6 | "Both ends prematurely" is vague | IN recorded before OUT |
| 7 | `RES-<order-id>-01` | Literal: `RES-ORD-2026-000001-01` |
| 8 | Year in IDs | All data sits inside 2026 |
| 9 | No Customer entity | Opaque `customer_ref`, no personal data |
| 10 | UI not specified | One simple React page, first to be cut |
| 11 | The role says AI but the brief has no LLM part | No LLM. It would add randomness with no requirement behind it |
| 12 | Public data is optional | Fully synthetic, with invented brand labels |
| 13 | Are store purchases orders? | Walk-in sales cover the store channel in the ledger, with no order. The 50,000 orders are all online. STORE orders with no hold are not built, and the brief doesn't ask for them by name |

Other assumptions: whole units only, one currency (INR), suppliers appear only as
`source_doc` on RECEIPT rows, returns go back to the original location, walk-in sales
respect reservations (a real till may not), and a transfer shipment carries one SKU.
