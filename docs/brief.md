# The brief

Copied word for word from the challenge, so anyone can check the plan and the
code against the exact requirements.

## Business Context / Problem

Unify inventory across stores, warehouses and online channels while accounting
for reservations, safety stock and in-transit stock.

## Objective and Scope

Build a working solution for this end-to-end workflow: Unify inventory across
stores, warehouses and online channels while accounting for reservations, safety
stock and in-transit stock. The solution should expose key decisions and
exceptions, preserve traceability, and produce measurable outputs. Medium
complexity means multiple related entities, conditional rules, at least one
exception path and meaningful evaluation metrics.

## Primary Actors / Systems

Customer, store/warehouse operator, merchandising/e-commerce analyst and
operations manager

## Business Workflow and Rules

1. Generate/reference master data first: Product, SKU, Location,
   InventoryBalance, InventoryMovement.
2. Generate dependent transaction/event records using valid parent IDs.
3. Apply business logic: Create product/SKU catalog and location hierarchy; seed
   opening balances; generate receipts, sales, returns, transfers and adjustments
   as immutable movements; create online reservations with expiry; calculate
   available-to-promise as on-hand minus reserved/blocked plus policy-approved
   inbound; link orders to fulfillment location.
4. Persist state changes as explicit records/events where relevant; do not
   overwrite history.
5. Generate outcomes and ground-truth labels from documented rules, not
   independent random assignment.

## Entities and Relationships

Core entities: Product, SKU, Location, InventoryBalance, InventoryMovement,
Reservation, Order, OrderLine, Shipment, ReplenishmentRule. Define a primary key
for every entity and foreign keys for every relationship. Document cardinality
(1:1, 1:N, N:M), optional relationships, effective dates and lifecycle states.
Use bridge tables for many-to-many relationships; retain event history for state
changes; validate all joins. For deliberate anomaly cases, keep an
issue_type/ground_truth label rather than silently corrupting unrelated rows.

## ID / Nomenclature Conventions

SKU-000001; LOC-000001; ORD-2026-000001; RES-<order-id>-01; MOV-2026-00000001;
SHP-2026-000001.

## Synthetic Data Generation Instructions

- Step 1: Write a data dictionary for: Product, SKU, Location, InventoryBalance,
  InventoryMovement, Reservation, Order, OrderLine, Shipment, ReplenishmentRule.
  For each field specify type, required/optional, allowed values, units,
  uniqueness and validation rule.
- Step 2: Create parent/master tables first, then child and event tables. Apply
  the domain-specific generation logic in Business Workflow rule 3.
- Step 3: Apply deterministic IDs and naming as above. Keep counters zero-padded,
  IDs immutable, and referenced IDs exactly consistent across files.
- Step 4: Generate the baseline volume: 5,000 SKUs, 100 locations, 1 million
  movements and 50,000 orders. Include overselling attempts, expired
  reservations, damaged stock, in-transit transfers and channel-specific buffers.
  Use a fixed seed (e.g. 20261022) and parameterize scale so starter and full
  datasets use the same generator.
- Step 5: Generate valid baseline records first, then inject labelled edge cases:
  inventory balance does not reconcile to movements; reservation exceeds available
  stock; order fulfilled from blocked stock; transfer counted at both ends
  prematurely.
- Step 6: Calculate derived values (totals, balances, scores, statuses and dates)
  from source records rather than generating them independently.
- Step 7: Produce a separate ground_truth.csv/json for known anomalies, expected
  decisions or labels; never leak it into input features.
- Step 8: Run validation: primary-key uniqueness, foreign-key integrity, allowed
  enums, date ordering, arithmetic reconciliation, lifecycle transitions and
  duplicate checks.
- Step 9: Export CSV/JSON/SQL seed data plus README, schema/data dictionary,
  generator command, fixed seed, source manifest and validation summary.
- Step 10: Regenerate twice with the same seed and compare row counts/checksums to
  demonstrate reproducibility.

## Required Edge Cases / Negative Scenarios

Inventory balance does not reconcile to movements; reservation exceeds available
stock; order fulfilled from blocked stock; transfer counted at both ends
prematurely.

## Validation Rules / Acceptance Criteria

Stock balances reconcile; ATP calculation follows rules; reservation expiry
releases stock; all order allocations are traceable.

## Evaluation Focus for Generated Data

(a) realistic distributions and meaningful variation; (b) consistent IDs and valid
joins; (c) dependency/lifecycle consistency across entities; (d) correct derived
values and reconciliation; (e) normal, boundary, failure and exception coverage;
(f) separate ground truth; (g) fixed-seed reproducibility, documentation and lawful
provenance.

## Public Data Guidance / Lawful Use

External data is optional. If used, record source URL, dataset/version, retrieval
date, license/terms, attribution, transformations and fields discarded. Use only
data you are legally permitted to access and redistribute; do not bypass access
controls or collect confidential/personal information. If licensing or suitability
is unclear, use synthetic data and cite public reference data only for schema
inspiration.
