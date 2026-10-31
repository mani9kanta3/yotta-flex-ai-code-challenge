# Requirements checklist

One item for every point in [brief.md](brief.md), plus the working rules. Marked
done, partial or missing with evidence in Phase 3. Nothing is marked yet.

## Objective

1. Unified view across stores, warehouses, online. Proof: network ATP API.
2. Accounts for reservations. Proof: ATP test.
3. Accounts for safety stock. Proof: ATP test.
4. Accounts for in-transit stock. Proof: transfer test.
5. Working end-to-end solution. Proof: clean run.
6. Exposes key decisions. Proof: `allocation_decision`, API.
7. Exposes exceptions. Proof: `inventory_exception`, API.
8. Preserves traceability. Proof: order trace API.
9. Produces measurable outputs. Proof: `metrics.json`.
10. Multiple related entities. Proof: schema.
11. Conditional rules. Proof: `rules.py` tests.
12. At least one exception path. Proof: rejection, short pick, late inbound, detectors.
13. Meaningful evaluation metrics. Proof: `eval/score.py`.

## Actors

14. Customer. Proof: place order, ATP.
15. Store/warehouse operator. Proof: ledger, location exceptions.
16. Merchandising/e-commerce analyst. Proof: channel ATP, oversell rate.
17. Operations manager. Proof: exceptions, metrics.

## Workflow

18. Master data generated first. Proof: generator order.
19. Transactions use valid parent IDs. Proof: FK checks.
20. Product/SKU catalog. Proof: `product`, `sku`.
21. Location hierarchy. Proof: `parent_location_id`.
22. Opening balances seeded. Proof: OPENING rows.
23. Receipts. Proof: movement type.
24. Sales. Proof: movement type.
25. Returns. Proof: movement type.
26. Transfers. Proof: OUT and IN rows.
27. Adjustments. Proof: movement type.
28. Movements are immutable. Proof: trigger test.
29. Online reservations with expiry. Proof: `reservation`.
30. ATP = on-hand minus reserved/blocked plus approved inbound. Proof: SQL and Python agree.
31. Orders linked to fulfilment location. Proof: `order_allocation`.
32. State changes kept as events, no overwriting. Proof: event tables.
33. Outcomes and labels come from documented rules. Proof: `PLAN.md`, code.

## Entities

34. All 10 core entities exist. Proof: `test_database.py`.
35. PK on every entity. Proof: `test_database.py`.
36. FK on every relationship. Proof: `constraints.sql`.
37. Cardinality documented. Proof: `PLAN.md` section 3, dictionary.
38. Optional relationships documented. Proof: `PLAN.md` section 3, dictionary.
39. Effective dates. Proof: rule, SKU, channel, location.
40. Lifecycle states. Proof: validator replays transitions.
41. Bridge tables for N:M. Proof: `sku_location`, `channel_location`, `order_allocation`.
42. Event history retained. Proof: event tables.
43. All joins validated. Proof: anti-join checks.
44. Anomalies labelled, unrelated rows untouched. Proof: injection test.

## IDs

45. `SKU-000001`. Proof: regex check.
46. `LOC-000001`. Proof: regex check.
47. `ORD-2026-000001`. Proof: regex check.
48. `RES-<order-id>-01`. Proof: regex check.
49. `MOV-2026-00000001`. Proof: regex check.
50. `SHP-2026-000001`. Proof: regex check.

## Generation steps

51. Dictionary with type, required, allowed values, units, uniqueness, validation rule. Proof: `data_dictionary.md` and a test.
52. Parents before children. Proof: load order.
53. IDs deterministic, zero-padded, immutable, consistent. Proof: checksums, FKs.
54. 5,000 SKUs. Proof: row count.
55. 100 locations. Proof: row count.
56. 1,000,000 movements. Proof: row count.
57. 50,000 orders. Proof: row count.
58. Overselling attempts. Proof: count.
59. Expired reservations. Proof: count.
60. Damaged stock. Proof: count.
61. In-transit transfers. Proof: count.
62. Channel-specific buffers. Proof: `channel_location`.
63. Fixed seed 20261022. Proof: manifest.
64. Same generator for starter and full. Proof: `--scale`.
65. Baseline first, then labelled injection. Proof: `inject.py`.
66. Derived values calculated from source. Proof: arithmetic checks.
67. Separate ground truth file. Proof: `ground_truth/`.
68. Ground truth never leaks into features. Proof: leak test.
69. PK uniqueness check. Proof: validator.
70. FK integrity check. Proof: validator.
71. Allowed enums check. Proof: validator.
72. Date ordering check. Proof: validator.
73. Arithmetic reconciliation check. Proof: validator.
74. Lifecycle transitions check. Proof: validator.
75. Duplicate check. Proof: validator.
76. Export CSV/JSON/SQL seed. Proof: `data/`.
77. README. Proof: file.
78. Schema and data dictionary. Proof: files.
79. Generator command documented. Proof: README.
80. Fixed seed documented. Proof: README, manifest.
81. Source manifest. Proof: `source_manifest.json`.
82. Validation summary. Proof: `validation_summary.json`.
83. Regenerate twice, compare counts and checksums. Proof: `compare_runs` output.

## Edge cases and acceptance criteria

84. Balance doesn't reconcile. Proof: label and detection.
85. Reservation exceeds available stock. Proof: label and detection.
86. Order fulfilled from blocked stock. Proof: label and detection.
87. Transfer counted at both ends prematurely. Proof: label and detection.
88. Stock balances reconcile. Proof: validator.
89. ATP follows the rules. Proof: checkpoints.
90. Reservation expiry releases stock. Proof: test.
91. All order allocations are traceable. Proof: 100% trace metric.

## Evaluation focus

92. (a) Realistic distributions. Proof: validation summary.
93. (b) Consistent IDs, valid joins. Proof: validator.
94. (c) Lifecycle and dependency consistency. Proof: validator.
95. (d) Correct derived values. Proof: validator.
96. (e) Normal, boundary, failure, exception coverage. Proof: decoys, counts.
97. (f) Separate ground truth. Proof: folder, leak test.
98. (g) Reproducibility, documentation, provenance. Proof: manifest, compare.

## Public data and deliverables

99. Lawful data: fully synthetic, stated in the manifest. Proof: manifest.
100. Public GitHub repo, pushed by Manikanta. Proof: link.
101. README: problem, install, configure, run, verify, assumptions, decisions, tradeoffs, testing, limitations, how we know it works. Proof: section review.
102. Starter dataset in the repo. Proof: `data/starter`.
103. One command recreates the full dataset. Proof: run it.
104. Tests. Proof: pytest output.
105. `prompt-history/` folder. Proof: folder.
106. `AI_USAGE.md`, with personal parts left for Manikanta. Proof: file.

## Added after review

107. Reserved stock at a past time comes from events. Proof: test.
108. BLOCK and UNBLOCK don't change total stock. Proof: `test_generator.py`, `test_database.py`, validator.
109. Orders on inbound stock wait for arrival, with a promised date and late handling. Proof: test.
110. Concurrent orders lock sorted SKU-location rows and recalculate after the lock. Proof: race test.
111. Reaching the exact movement count never writes an invalid sale. Proof: top-up tests, validator.
112. Docker setup kept. Proof: `docker-compose.yml`.
