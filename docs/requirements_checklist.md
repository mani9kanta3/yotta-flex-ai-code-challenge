# Requirements checklist

One item for every point in [brief.md](brief.md), plus the working rules. Each one is marked
done, partial or missing against the actual code and output, with its evidence.

**107 done, 5 partial (39, 92, 100, 105, 106), 0 missing.** Numbers are from seed 20261022.

## Objective

1. **Done.** Unified view across stores, warehouses, online. `GET /api/atp` network totals, Availability page, `test_api.py`.
2. **Done.** Accounts for reservations. `RESERVED_SQL`, `test_orders.py`, checkpoints.
3. **Done.** Accounts for safety stock. `rules.position`, `test_rules.py`, Availability page.
4. **Done.** Accounts for in-transit stock. `INBOUND_SQL`, `IN_TRANSIT_SQL`, `test_transfers.py`, truck arrival checkpoints.
5. **Done.** Working end-to-end solution. `run_all` and the site run from a fresh clone with an empty database.
6. **Done.** Exposes key decisions. `allocation_decision` with candidates, `/api/orders/{id}/trace`.
7. **Done.** Exposes exceptions. `inventory_exception`, `/api/exceptions`, Exceptions page.
8. **Done.** Preserves traceability. `/api/orders/{id}/trace`, `test_every_order_in_the_data_is_fully_traceable`.
9. **Done.** Produces measurable outputs. `metrics_summary.json`, `/api/metrics`, Evidence page.
10. **Done.** Multiple related entities. 19 tables in `schema.sql`.
11. **Done.** Conditional rules. `rules.py`, `test_rules.py`.
12. **Done.** At least one exception path. Rejection, late trucks, refused short pick and refused returns in tests, and four detectors.
13. **Done.** Meaningful evaluation metrics. `eval/score.py`, `eval/report.py`.

## Actors

14. **Done.** Customer. Availability and Orders pages, `POST /api/orders`.
15. **Done.** Store/warehouse operator. Stock ledger and Trucks pages.
16. **Done.** Merchandising/e-commerce analyst. Availability by channel, and oversell and decision rates on Overview and Evidence.
17. **Done.** Operations manager. Exceptions and Evidence pages.

## Workflow

18. **Done.** Master data generated first. `scripts/generate.py`, master tables before the simulation.
19. **Done.** Transactions use valid parent IDs. `constraints.sql` fails on a bad reference, and the validator counts them, 38 checks.
20. **Done.** Product/SKU catalog. `product`, `sku`.
21. **Done.** Location hierarchy. `parent_location_id`, with a CHECK that only the NDC has none.
22. **Done.** Opening balances seeded. OPENING rows, checks on missing balances.
23. **Done.** Receipts. 12,082 rows at full scale.
24. **Done.** Sales. 727,787 rows at full scale.
25. **Done.** Returns. 45,191 rows at full scale.
26. **Done.** Transfers. 82,475 out and 81,122 in at full scale, and in-transit trucks at the end.
27. **Done.** Adjustments. 18,768 rows at full scale.
28. **Done.** Movements are immutable. Triggers in `schema.sql`, `test_database.py`.
29. **Done.** Online reservations with expiry. `reservation.expires_at`, 10,219 expired holds at full scale.
30. **Done.** ATP = on-hand minus reserved/blocked plus approved inbound. `test_python_rule_and_sql_agree_at_every_second_around_each_change`, 200 of 200 checkpoints.
31. **Done.** Orders linked to fulfilment location. `order_allocation`, 100% linked in the report.
32. **Done.** State changes kept as events, no overwriting. Three event tables, status checked against the latest event.
33. **Done.** Outcomes and labels come from documented rules. `docs/PLAN.md`, `generator/inject.py`.

## Entities

34. **Done.** All 10 core entities exist. `test_database.py`.
35. **Done.** PK on every entity. `test_primary_key_list_is_the_same_as_the_database`.
36. **Done.** FK on every relationship. `constraints.sql`, `test_foreign_key_list_is_the_same_as_the_database`.
37. **Done.** Cardinality documented. `docs/DATA_DICTIONARY.md`, Relationships.
38. **Done.** Optional relationships documented. `docs/DATA_DICTIONARY.md`, Relationships.
39. **Partial.** Effective dates. Columns exist on SKU, location, channel-location and rule, and SKU launches and stops are generated. Rules and buffers have one version each, with no mid-period change. That was first on the plan's cut list.
40. **Done.** Lifecycle states. `rules.py` transitions, validator replays every event.
41. **Done.** Bridge tables for N:M. `sku_location`, `channel_location`, `order_allocation`.
42. **Done.** Event history retained. `order_event`, `reservation_event`, `shipment_event`.
43. **Done.** All joins validated. Validator counts broken references for every foreign key.
44. **Done.** Anomalies labelled, unrelated rows untouched. `eval/check_unrelated.py` compares all 18 tables through the new IDs, at both scales: 0 clean rows changed, every new row labelled, and every balance keeps its date and ID.

## IDs

45. **Done.** `SKU-000001`. Format check in the validator.
46. **Done.** `LOC-000001`. Format check in the validator.
47. **Done.** `ORD-2026-000001`. Format check in the validator.
48. **Done.** `RES-<order-id>-01`. Format check in the validator, against its own order.
49. **Done.** `MOV-2026-00000001`. Format check in the validator.
50. **Done.** `SHP-2026-000001`. Format check in the validator.

## Generation steps

51. **Done.** Dictionary with type, required, allowed values, units, uniqueness, validation rule. `docs/DATA_DICTIONARY.md`, `test_data_dictionary.py`.
52. **Done.** Parents before children. `TABLE_COLUMNS` order, foreign keys added after.
53. **Done.** IDs deterministic, zero-padded, immutable, consistent. Same checksums from the same seed, foreign keys pass.
54. **Done.** 5,000 SKUs. Manifest and validator at full scale.
55. **Done.** 100 locations. Manifest and validator at full scale.
56. **Done.** 1,000,000 movements. Exactly 1,000,000, including injected rows.
57. **Done.** 50,000 orders. Exactly 50,000, including injected rows.
58. **Done.** Overselling attempts. 1,513 at full scale, all refused.
59. **Done.** Expired reservations. 10,219 expired holds at full scale.
60. **Done.** Damaged stock. 7,070 damage events at full scale.
61. **Done.** In-transit transfers. 1,353 trucks on the road at the end at full scale.
62. **Done.** Channel-specific buffers. `channel_location.buffer_units`, with different values by channel and location type.
63. **Done.** Fixed seed 20261022. Manifest, 20261022.
64. **Done.** Same generator for starter and full. `--scale`, same code at both.
65. **Done.** Baseline first, then labelled injection. `scripts/generate.py`, the clean baseline is written first.
66. **Done.** Derived values calculated from source. Closing balances, totals and statuses come from source rows, with checks.
67. **Done.** Separate ground truth file. `data/ground_truth/`.
68. **Done.** Ground truth never leaks into features. `test_detectors_and_inputs_never_read_the_answers`, `test_no_input_file_has_a_label_column`.
69. **Done.** PK uniqueness check. Validator: duplicate primary key, per table.
70. **Done.** FK integrity check. Validator: reference points at nothing, per foreign key.
71. **Done.** Allowed enums check. Validator: value not allowed, 22 columns.
72. **Done.** Date ordering check. Validator: 5 date checks plus time-order checks. Effective ranges are CHECKs.
73. **Done.** Arithmetic reconciliation check. Validator: closing balance and network reconciliation.
74. **Done.** Lifecycle transitions check. Validator: illegal transition and event chain checks.
75. **Done.** Duplicate check. Validator: duplicate documents, and groups that aren't a matching pair.
76. **Done.** Export CSV/JSON/SQL seed. CSV committed. SQL and JSON come from `scripts/export_seed.py`, which a test loads.
77. **Done.** README. `README.md`.
78. **Done.** Schema and data dictionary. `schema.sql`, `constraints.sql`, `docs/DATA_DICTIONARY.md`.
79. **Done.** Generator command documented. README, manifest.
80. **Done.** Fixed seed documented. README, manifest.
81. **Done.** Source manifest. `source_manifest.json` beside each dataset, checksums compared by the validator.
82. **Done.** Validation summary. `validation_summary.json`.
83. **Done.** Regenerate twice, compare counts and checksums. `compare_runs`: all 40 files identical at both scales.

## Edge cases and acceptance criteria

84. **Done.** Balance doesn't reconcile. 50 labelled and 50 found at full scale.
85. **Done.** Reservation exceeds available stock. 50 labelled and 50 found at full scale.
86. **Done.** Order fulfilled from blocked stock. 50 labelled and 50 found at full scale.
87. **Done.** Transfer counted at both ends prematurely. 50 labelled and 50 found at full scale, with believable travel times.
88. **Done.** Stock balances reconcile. Validator on the clean baseline. On the final data, only the labelled ones fail.
89. **Done.** ATP follows the rules. 200 of 200 checkpoints at full scale. Only 200 of 72,360 decisions were recalculated at full scale.
90. **Done.** Reservation expiry releases stock. `test_orders.py`, `test_expiry_releases_the_hold_and_shows_atp_before_and_after`, expiry checkpoints.
91. **Done.** All order allocations are traceable. 68,362 of 68,362 allocations, 52,464 of 52,464 used holds.

## Evaluation focus

92. **Partial.** (a) Realistic distributions. Weekend and July patterns, varied trips and quantities exist. Sales are too concentrated: the top tenth of SKUs takes 91% of units. Case patterns are measured in `docs/STATUS.md`.
93. **Done.** (b) Consistent IDs, valid joins. Validator format and reference checks.
94. **Done.** (c) Lifecycle and dependency consistency. Validator event checks.
95. **Done.** (d) Correct derived values. Validator, `test_generator.py`.
96. **Done.** (e) Normal, boundary, failure, exception coverage. Decoys, oversell attempts, late trucks and refusals, with counts in the report.
97. **Done.** (f) Separate ground truth. `data/ground_truth/`, leak tests.
98. **Done.** (g) Reproducibility, documentation, provenance. Manifest, `compare_runs`, README, data dictionary.

## Public data and deliverables

99. **Done.** Lawful data: fully synthetic, stated in the manifest. Manifest `data_source`, no external data.
100. **Partial.** Public GitHub repo, pushed by Manikanta. Pushed by Manikanta during the build. The final commit is theirs to push.
101. **Done.** README: problem, install, configure, run, verify, assumptions, decisions, tradeoffs, testing, limitations, how we know it works. `README.md`, every section.
102. **Done.** Starter dataset in the repo. `backend/data/starter/`.
103. **Done.** One command recreates the full dataset. `python -m scripts.run_all --scale full`, run. Also `scripts.generate` on its own.
104. **Done.** Tests. `backend/tests/`, all passing.
105. **Done for the recorded conversations through their stated cutoffs.** `prompt-history/` folder. Claude Code messages are in files 01 to 13. `prompt-history/codex/` archives all 26 user messages in the saved Codex review conversation through the Render hosting selection on 2 November 2026, with all nine pasted text attachments. Later or unprovided conversations are outside this archive.
106. **Partial.** `AI_USAGE.md` now includes a Codex-assisted personal section based on the conversation record, and discloses both implementation and review assistance. Manikanta still needs to read it and correct anything that does not describe his experience.

## Added after review

107. **Done.** Reserved stock at a past time comes from events. `test_orders.py`, `test_rules.py`.
108. **Done.** BLOCK and UNBLOCK don't change total stock. `test_generator.py`, `test_database.py`, validator.
109. **Done.** Orders on inbound stock wait for arrival, with a promised date and late handling. `test_transfers.py`, `test_late_trucks.py`, `test_order_on_a_truck_waits_then_ships_when_it_arrives`.
110. **Done.** Concurrent orders lock sorted SKU-location rows and recalculate after the lock. `test_orders.py`, `test_twenty_orders_for_one_unit_through_the_api_gives_one_winner`.
111. **Done.** Reaching the exact movement count never writes an invalid sale. `test_generator.py`, validator.
112. **Done.** Docker setup kept. `docker-compose.yml`, used in the clean run.
