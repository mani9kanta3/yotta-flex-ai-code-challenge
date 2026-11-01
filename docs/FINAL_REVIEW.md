# Pre-submission review — 1 November 2026

Reviewed commit: `f5c6132`. Review changes are limited to AI_USAGE.md, its two status references and this report. No application fixes, commits or pushes were made by this reviewer.

**Verdict: do not submit this version yet.** Three reproduced correctness problems affect the live order flow and detectors. Test totals and synthetic detection scores do not cover these problems.

## What was actually verified

- Read the brief, schema, constraints, README, status, checklist, disclosure, core application modules and relevant generator/evaluation/frontend code.
- Ran the starter `scripts.run_all` workflow in a temporary local clone against a new, separate database. Exit 0, about 36 seconds. Clean validation passed; final-data failures matched labels; detectors and ATP checkpoints passed; all 40 regenerated files matched.
- Recounted the existing starter and full CSV files and checked every table file against its manifest. No checksum mismatches. Full: 5,000 SKUs, 100 locations, 1,000,000 movements, 50,000 orders.
- Built the frontend successfully. The first restricted build hit a Windows process-permission error; rerunning with the required process access succeeded. This was not a source-code build failure.
- Opened the live overview, availability, orders, order trace, ledger, trucks, exceptions and evidence pages against the review database. Populated data was visible. Marketplace arithmetic presentation has the issue described below.
- Reproduced findings F1–F5 in isolated test data or a temporary clone. The working dataset was not used for destructive probes.
- Full pytest: 777 collected tests passed, no skips shown, exit 0. One third-party Starlette/AnyIO deprecation warning. Full-scale two-run comparison also passed: all 40 files identical.

**NOT VERIFIED:** a new operating-system install, fresh dependency installation, remote GitHub visibility/latest push, sustained traffic, every full-scale decision, every alternative seed, a final rerun of the historical 172 deliberate mutations, every browser action, and phone/light-theme behavior in this review. The clone workflow reused the installed Python environment and existing PostgreSQL server. It is a fresh checkout and empty database check, not a clean-machine claim.

## Part 1 — Requirement check

“Done” means implementation/evidence exists within the stated scope, not that no defect can exist.

| Requirement | Status | Evidence | What is wrong or limited |
|---|---|---|---|
| Unified inventory across stores, warehouses and online | Partial | `app/main.py:get_atp`, `app/atp.py:get_positions` | Reads work; F1 breaks live reservation correctness |
| Actors can inspect decisions, stock and exceptions | Done | `frontend/src/App.jsx:NAV`, page components, API routes | Local demonstration; not an authenticated production system |
| Masters before transactions | Done | `scripts/generate.py:generate`, `generator/export.py:TABLE_COLUMNS` | FK checks occur after COPY |
| Product | Done | `db/schema.sql:51`, `constraints.sql` | `product_id` PK; SKU N:1 product; ACTIVE/DISCONTINUED and launch date |
| SKU | Done | `db/schema.sql:61` | `sku_id` PK; product FK; effective date range and lifecycle |
| Location | Done | `db/schema.sql:73` | `location_id` PK; optional parent FK for root; open/close dates; hierarchy parent types/cycles are not fully constrained by the FK alone |
| InventoryBalance | Partial | `db/schema.sql:135`, `app/detect.py:BALANCE_SQL` | PK, stocked-pair FK, unique opening/closing pair; F3 ignores snapshot time in live reconciliation |
| InventoryMovement | Partial | `db/schema.sql:240`, `app/movements.py:add_movement` | PK and optional allocation/shipment/sale links exist; UPDATE/DELETE guarded, TRUNCATE not guarded (F5) |
| Reservation | Partial | `db/schema.sql:204`, `app/reservations.py` | PK; order-line, stocked-pair, optional inbound shipment FKs; events and expiry exist; F1 can revive an expired hold |
| Order | Partial | `db/schema.sql:151`, `app/orders.py:place_order_in_transaction` | PK and channel FK; order events; online flow exists but F1 fails last-unit protection after expiry |
| OrderLine | Done | `db/schema.sql:164` | PK, order/SKU FKs, unique order/line number; amount = quantity × price |
| Shipment | Done | `db/schema.sql:178`, `app/transfers.py`, `app/shipping.py` | PK; customer vs transfer relationships distinguished; lifecycle events; one SKU per transfer is documented |
| ReplenishmentRule | Partial | `db/schema.sql:118`, `generator/master.py:build_rules` | PK and stocked-pair/source-location FKs; derived safety/reorder values; only one generated version |
| Cardinality, optional links and bridge tables | Done | `docs/DATA_DICTIONARY.md:Relationships`, `constraints.sql` | SKU-location, channel-location, order allocations represent N:M; no customer/PO entity is claimed |
| Effective dates and lifecycle history | Partial | `atp.py:CANDIDATES_SQL`, three event tables, `rules.py` transitions | No generated mid-period policy changes; overlapping policy versions not excluded; live time bug F1 |
| Exact SKU/LOC/ORD/RES/MOV/SHP formats | Done | `generator/export.py:*_id_of`, `scripts/validate.py:CHECKS` | Year fixed at 2026; do not claim arbitrary-year support |
| Receipts, sales, returns, transfers, adjustments | Done | `generator/simulate.py:Ledger`, starter pipeline movement-type checks | Live walk-in/damage/adjustment routes absent but generation exists |
| History never overwritten | Partial | `schema.sql:360` and subsequent triggers | Event INSERT design works; privileged runtime can truncate history (F5) |
| Expiry releases stock exactly at expiry | Partial | `rules.py:holds_stock`, `atp.py:RESERVED_SQL`, `reservations.py:expire_due` | As-of formula releases it, but API can backdate confirmation (F1) |
| ATP from source records | Partial | `atp.py:STOCK_SQL`, `RESERVED_SQL`, `INBOUND_SQL` | Formula exists and starter checkpoints pass; detector uses a different inbound rule (F2) |
| Fulfillment-location links and traceability | Done | `order_allocation`, `app/trace.py:get_order_trace` | Trace badge checks consumed holds only; it is not a comprehensive proof of all relationships |
| Required full volumes | Done | Recounted full CSVs; manifest checksums matched | Full end-to-end validation not rerun in this review |
| Overselling attempts, expiry, damage, in-transit, buffers, safety | Done | `settings.py`, `order_flow.py:try_oversell`, `simulate.py`, starter validation | Selected seed has the cases; some other seeds do not |
| Four labelled edge cases and actual detection | Partial | `inject.py:try_*`, `detect.py:DETECTORS`, `eval/score.py` | All four found on starter; reservation detector misses an unlabelled policy violation (F2) |
| Baseline before injection | Done | `scripts/generate.py:generate`, separate clean directory | Clean targets reserve space so final targets include anomalies |
| Derived totals, balances, statuses | Done | `export.py:order_line_rows`, closing-balance replay; `order_flow.py` transitions | Random inputs are legitimate; measured sales concentration and artificial case placement remain limitations |
| Separate ground truth, no label input | Done | `export.py:truth_dir_of`, loader table whitelist, `eval/` readers | Aggregate scores intentionally served to UI; case-placement patterns remain, so not suitable for ML accuracy claims |
| Step 8: PK/FK/enums/date/arithmetic/lifecycle/duplicates | Partial | `validate.py:run_checks`, `required_check_names`, 229 clean starter checks | Checks exist; reuse of faulty detector SQL means two passes are not independent confirmation (F2) |
| Step 9: CSV, JSON, SQL seed | Done | 18 CSV tables; `scripts/export_seed.py:export_seed` | SQL/JSON require the separate documented export command; not produced by `run_all`; full JSON memory risk F10 |
| README, schema, dictionary, command, seed, manifest, summary | Done | Root README, dictionary, source manifests and pipeline output | README Node minimum is inaccurate (F8) |
| Same seed twice: counts and checksums | Done | `scripts/compare_runs.py`, independently rerun both scales | All 40 files identical at both scales; this covers generated files, not timings/reports/optional SQL+JSON |
| Unrelated rows preserved | Partial | `eval/check_unrelated.py:compare_datasets` | F4: proof omits several transaction/event tables and foreign-key relationships |
| Decisions, exceptions, measured outputs | Partial | `allocation_decision`, `inventory_exception`, `eval/report.py` | Only 200 normal-run decisions recalculated; REALLOCATED excluded; live false-positive issue F3 |
| Lawful source/provenance | Done | Synthetic master data, manifest source statement | No external retail source is being claimed |
| AI disclosure and prompt history | Partial | `AI_USAGE.md`, nine prompt files, conversation | Updated to include Codex; complete review transcript not archived; personal understanding not verified |

## Part 2 — Findings by file

### F1 — Critical: confirmation resurrects expired stock

Evidence: `backend/app/main.py:196–203`, `views.py:39–43`, `reservations.py:confirm_order`.

`next_order_time` uses the order's last event plus one minute, rather than the current simulated time. An old ALLOCATED order can be confirmed in the past even after another order has taken its expired stock. The order lock cannot prevent this because the new order uses the stock-pair lock and the timestamps are wrong.

Reproduced: stock = 1; place A; place B at A's expiry + one minute without running expiry; API-confirm A. Response was HTTP 200 and reserved stock became **2 for one physical unit**. B was also ALLOCATED.

Fix: use a coherent monotonic simulated clock; after the relevant locks are held, verify current time against expiry and refuse a stale confirmation. Audit cancellation timestamps too. Do not repair this by silently stealing B's hold. Add a regression at the API boundary, including concurrent confirmation/order placement.

### F2 — High: reservation detector counts disallowed inbound

Evidence: `backend/app/detect.py:66–79` versus `backend/app/atp.py:INBOUND_SQL`; `scripts/validate.py:414` reuses detector SQL.

The detector sums all in-transit trucks for an inbound hold. It does not reproduce the latest ETA/horizon test, channel `counts_inbound`, or rule `allow_inbound_atp`. Channel-location effective dates also are not filtered there. Therefore an invalid reservation can pass both detector and validator.

Reproduced with a historical-data fixture: zero on-hand, ten inbound units, a one-unit hold, and an original ETA seven days away. ATP counted **0 inbound**, while `RESERVATION_SQL` returned **no violation**. The fixture first created the hold under a valid ETA then changed the input ETA to construct the invalid record; this is a detector test, not a claimed live ETA-edit exploit.

Fix: use the same documented inbound eligibility rules as ATP, evaluated at creation, while excluding the hold under inspection. Test overdue, beyond-horizon, marketplace and inbound-disabled cases, plus valid boundary decoys.

### F3 — High: live movements create false balance exceptions

Evidence: `backend/app/detect.py:BALANCE_SQL` (lines 13–26), `views.py:get_ledger` (lines 163–194).

Both compare an old closing snapshot with all ledger movements, including movements after that snapshot's `as_of_at`. A valid live sale appears to corrupt the closing balance. The ledger page does acknowledge that live activity also shows as a difference, but the detector still classifies it as an exception. The UI explicitly permits live sales/shipping and rerunning detectors, so this affects the intended demonstration.

Reproduced: closing snapshot = 2; sell one unit after the snapshot. Detector returned `('BAL-CLOSE', 1, 2)` and the ledger returned `reconciles=False`. The historical closing value 2 was correct.

Fix: reconcile each snapshot only against movements in its applicable opening-to-closing interval. Show current balance separately; never rewrite the historical snapshot to hide the problem.

### F4 — High: “nothing unrelated changed” is not fully checked

Evidence: `backend/eval/check_unrelated.py:80–101`, `MOVEMENT_FIELDS`, `ORDER_FIELDS`, `SHIPMENT_FIELDS`.

The helper checks seven masters, selected movement/order/shipment fields and balances. It omits order lines, reservations, allocations, decisions and all three event tables. It also removes linking IDs without verifying their remapped relationships. A successful result does not establish the broad README claim.

Reproduced in the temporary clone: change the first existing reservation event's reason code; `compare_datasets` still returns `[]`. Original bytes were restored. Other validators may detect some changes, but this specific proof does not.

Fix: compare all exported tables through explicit old-to-new ID mappings, including FK relationships and both directions of balance-key presence. Until then narrow the claim to the tables/fields actually compared.

### F5 — Medium: append-only protection excludes TRUNCATE

Evidence: `backend/db/schema.sql:360–371`, `backend/app/config.py:18`.

Triggers reject UPDATE/DELETE only. With the configured database role, `TRUNCATE inventory_movement CASCADE` succeeds. Tested in the isolated database and rolled back. Plain TRUNCATE alone was refused by the referencing FK, not by immutability protection.

Fix: add statement-level BEFORE TRUNCATE guards and test them. Prefer a separate restricted application role, with schema ownership reserved for setup. Do not promise protection against a database administrator who can disable safeguards.

### F6 — Medium: Marketplace display presents the wrong arithmetic

Evidence: `frontend/src/pages/Availability.jsx`, formula block and row rendering; `backend/app/rules.py:position`.

Observed for SKU-000259 at LOC-000002: displayed ATP 158, safety 25, buffer 8, promisable 11. The displayed formula says ATP − safety − buffer, which would be 125. The backend correctly removes 114 inbound units for Marketplace first; the page hides that `channel_atp` step.

Fix: show channel ATP or explicitly show inbound excluded before the final subtraction, and show the zero floor. Backend allocation should not be changed to match the misleading display.

### F7 — Medium: database is exposed more widely than the local-demo wording implies

Evidence: `docker-compose.yml:12`, `backend/app/config.py:18–19`.

The port mapping is `5434:5432`, with published development PostgreSQL superuser credentials. It is not explicitly bound to loopback. External reachability depends on Docker/network/firewall settings and was NOT VERIFIED.

Fix: bind `127.0.0.1:5434:5432` for the local demo. Keep the lack of login/deployment documented. Do not treat the published demo password as a leaked personal secret.

### F8 — Low: README accepts unsupported Node versions

Evidence: `README.md:30` says Node 20 or later. Installed Vite package metadata requires `^20.19.0 || >=22.12.0`.

Fix: state a supported exact minimum/range, preferably the version actually used, and use `npm ci` for lockfile-based verification. Node 20.0 satisfies the README but not the toolchain. No internet/version assumption was needed: this came from the installed package.

### F9 — Medium: loading is destructive and not atomic through constraint validation

Evidence: `scripts/load_data.py:load`, `scripts/setup_database.py:main`, `schema.sql:DROP TABLE`.

Schema reset commits before COPY; COPY commits before foreign keys are added. A missing CSV or failed constraint can leave the target database empty or populated without its intended FKs. This is documented as resetting data, but “safe to run again” is too broad.

Fix: preflight files/checksums before resetting, and make schema creation, COPY and constraints atomic after database creation, or use a disposable staging database. At minimum warn that a failure requires reloading and the prior dataset is not retained.

### F10 — Medium performance risk: optional exports load everything in memory

Evidence: `scripts/export_seed.py:read_table`, `write_sql`, `write_json`.

SQL batching occurs only after the complete CSV is read. JSON retains every table as lists of dictionaries before serialization. This can use far more memory than the CSV size at a million rows. Full export peak memory was NOT VERIFIED; no OOM is claimed.

Fix: stream SQL CSV batches and stream JSON one table/row at a time if full export is needed. This is less urgent than F1–F4.

### Other scoped observations

- `app/atp.py:get_positions` performs several queries per candidate; `views.py:get_row_counts` counts nine tables and is used to infer dataset scale. Scale inference by SKU count cannot distinguish clean/final or different seeds. Persist dataset identity if making stronger provenance claims.
- `views.py:get_ledger` fetches all pair movements and emits all chart points despite a row limit. Large hot-SKU histories remain unbounded. Sustained-load behavior is NOT VERIFIED.
- `trace.py:is_fully_traceable` checks only that consumed holds have a movement with a shipment ID. It does not verify type, quantity, matching SKU/location or all unconsumed allocations. The validator supplies some additional checks; label this badge narrowly.
- `detect.py:write_exceptions` deletes/replaces the last results and stamps them with fixed AS_OF. That is a latest-result view, not an exception audit history. Keep that distinction explicit.
- `generator/settings.py`, `master.py`, `inject.py`: distributions and case slots are deliberately engineered. Other-seed failures and concentration are already disclosed. Do not hide failures or claim these are real retail statistics.
- `generator/order_flow.py` and live application code duplicate orchestration. Shared pure rules help, but decision sampling excludes REALLOCATED and is not complete full-scale equivalence.
- `tests/conftest.py:require_postgres` skips database tests when PostgreSQL is absent. A green run with skips is not equivalent to the reported full test run.
- `backend/requirements.txt` pins direct packages but is not a complete transitive lock. Cross-version byte-for-byte reproducibility is not established.

Files reviewed without a separate confirmed defect in this pass include `app/returns.py` (counts after pair lock), `transfers.py` (receipt transaction), `shipping.py` (waits for inbound), `rules.py` (allocation and expiry predicates), `late_trucks.py` (atomic move), `movements.py`, `db.py`, `generator/checkpoints.py`, `generator/export.py`, `eval/check_failures.py`, `eval/check_atp.py`, `eval/score.py`, and `scripts/compare_runs.py`. This is not a proof that every path is correct. Individual test bodies, all CSS/accessibility states, and every generator line were not exhaustively audited; those remain NOT VERIFIED at that level. No claim of “every possible bug found” is made.

## Part 3 — Break scenarios to keep as regression tests

1. **Expired last unit:** stock 1, A at T, B at T+31 minutes, expiry job not run, confirm A through API. Expect 409 and one active hold. Reproduced failure F1.
2. **Concurrent expiry/payment/new order:** coordinate locks so confirmation waits while another order claims released stock. Expect at most one valid commitment; no backdated event.
3. **Inbound beyond horizon:** zero shelf stock; truck ETA T+7 days; insert an inbound hold at T. Expect reservation detector failure. Reproduced failure F2.
4. **Inbound channel/rule restriction:** repeat with Marketplace, or allow_inbound_atp=false, or overdue ETA. Expect detection even if the truck is in transit.
5. **Post-snapshot sale:** snapshot 2 at T; valid one-unit sale T+1 minute; rerun detectors and ledger. Expect closing snapshot still reconciles at T, current stock 1. Reproduced failure F3.
6. **Unrelated history alteration:** modify a clean reservation event in final CSVs. Expect unrelated-record comparison to fail. Reproduced failure F4.
7. **Truncate history:** only in a disposable database, begin transaction, TRUNCATE movement CASCADE, then rollback. Expect refusal once guarded. Current code allows it.
8. **Return without a sale/cancelled unshipped order:** no SALE exists; call returns endpoint with a nonexistent or non-SALE movement. Expect refusal and no ledger change. Existing return tests cover related refusals; separately test the exact cancelled-order flow.
9. **Two returns of the last returnable unit:** sale 3; return 2; concurrently request two returns of 1. Expect one success, total returns 3.
10. **Transfer receipt twice/late order:** receive once, retry, then ship a waiting paid order. Expect one TRANSFER_IN and one SALE; no shipment before arrival.
11. **Effective-date boundary:** adjacent channel/rule versions; query one second before and at change. Expect exactly one policy. Reject overlapping ranges rather than summing duplicated candidates.
12. **API request retry:** repeat an identical successful POST after a simulated lost response. Current API has no idempotency key; document that it creates another order or add a simple key if required.

## Part 4 — Reviewer run instructions

Use PowerShell. From the repository root:

```powershell
docker compose up -d --wait
cd backend
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\venv\Scripts\python.exe -m scripts.run_all --scale starter
.\venv\Scripts\python.exe -m pytest -ra
```

Expected starter pipeline: 20,000 movements, 1,000 orders; clean 229/229; final 223/229 with intentional exit 1; failure classification exit 0; 40/40 checkpoints; all four detectors precision/recall 1.0; 40 files identical. The wrapper exits 0. Numbers are seed-specific.

From `frontend`:

```powershell
npm ci
npm run build
npm run dev
```

Start the API separately from `backend`:

```powershell
.\venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
```

Full-scale verification and optional seed formats, from `backend`:

```powershell
.\venv\Scripts\python.exe -m scripts.run_all --scale full
.\venv\Scripts\python.exe -m scripts.export_seed --scale full
```

Expected full: 5,000/100/1,000,000/50,000; final validator intentionally fails on labels, classification passes. Full pipeline overwrites the target database. Do not run it against data you need to preserve. Check `$LASTEXITCODE` immediately after a command if running individual steps; PowerShell does not automatically stop just because a native command failed.

README changes needed: Node requirement (F8); wait for Docker health; state exact working directory for the full command; clarify optional exports and destructive reset. A healthy Docker container does not prove the generator/API/database application is correct.

## Part 5 — AI usage and honesty

Updated AI_USAGE.md using the conversation, not invented personal experience. It now names Claude Code and Codex; says the implementation was AI-written; distinguishes candidate terminal runs from assistant-run measurements; and states that the personal section itself was AI-assisted. The candidate provided 50-, 194- and 353-test terminal runs and starter comparison output in this conversation and repeatedly reported making their own commits/pushes. Later totals are not attributed to the candidate without their output.

Local commit history includes the final implementation commit `f5c6132`; the pasted “nothing is committed” status is stale for this checkout. Remote GitHub status was NOT VERIFIED. The nine prompt files are not a complete transcript of both assistants. Commit timestamps/messages do not prove personal authorship or understanding.

Questions for the candidate to answer personally:

- Which function can you explain without asking AI to explain it first?
- Which command did you run yourself after the final implementation, and what did it print?
- Which suggestion did you reject or change, and why?
- Have you personally opened the UI and followed an order through confirmation/shipping?
- Do you agree with every first-person statement in AI_USAGE.md? Correct anything that does not describe your experience.

## Part 6 — Interview grill (questions only)

1. Walk through the expired-last-unit bug in F1. Why do the existing locks not prevent it?
2. What should simulated “now” mean across two concurrent API requests?
3. Why use READ COMMITTED for allocation, and when must the availability read happen?
4. Which rows does a split order lock, and which lock order do shipping and reallocation follow?
5. Explain the difference between ATP, channel ATP, promisable and stock available on the shelf.
6. Why does a confirmed hold on an overdue truck remain reserved while inbound becomes zero?
7. Why does the current reservation detector disagree with ATP on a truck due in seven days?
8. How do you reconcile a historical closing balance after a valid live sale?
9. What can UPDATE/DELETE triggers prevent, and what can a privileged database role still do?
10. How does the generator reach exactly one million movements without inventing an invalid sale?
11. What does the row budget reserve for an order, and what happens at the end of the period?
12. How do you prove that renumbering preserved every foreign-key relationship after injection?
13. Why are 100% precision/recall and 777 passing tests insufficient evidence of correctness here?
14. Why are reallocation decisions excluded from SQL rechecking, and how could you test them independently?
15. Which code and design decisions were yours, which came from AI, and which parts can you defend unaided?

## Part 7 — Scores and fix order

These are provisional engineering-review scores, not a prediction of the company's actual judging rubric.

| Category | /10 | Reason |
|---|---:|---|
| Requirements coverage | 8 | All entities and baseline scale exist; lifecycle, detection and evidence have gaps |
| Correctness | 4 | Reproduced expired-stock overcommitment and detector errors |
| Code quality | 7 | Plain functions and parameterized queries; duplicated flows and incomplete time handling |
| Testing | 7 | Broad suite and pipeline, but central scenarios above escape it |
| Documentation | 8 | Detailed limits; some broad claims and setup details need correction |
| Reproducibility | 8 | Both scales independently reproduced; full files verified; version and seed limits disclosed |
| AI honesty | 8 | Explicit heavy assistance and limitations; updated disclosure still needs the candidate's truthful read-through |

**Move to next round: No, on this version.** The expired-hold confirmation bug violates the central no-overselling goal. This is a correctness decision, not a penalty for being a fresher or using AI. Reassess after the blockers are fixed and independently tested; the current evidence does not establish personal understanding.

Top five fixes, estimates for simple targeted changes plus regressions:

1. F1: monotonic confirmation/cancellation time and locking — **45–90 minutes**.
2. F2: policy-correct reservation detector and matching validator cases — **45–75 minutes**.
3. F3: snapshot-time reconciliation and current-vs-historical UI separation — **30–60 minutes**.
4. F4: complete unrelated-record comparison or explicitly narrow unsupported claims — **60–120 minutes** for proof, **10–15 minutes** for honest wording only.
5. F5/F7: truncate guard, loopback binding and accurate immutability wording — **20–40 minutes**; a full runtime-role split may take longer.

Quick fixes after those: Marketplace formula and Node documentation, about 15–25 minutes. Leave sustained load testing, a full export streaming refactor, richer policy version examples and distribution tuning as explicit limits if time is short. Do not spend the remaining time adding frameworks, login or deployment ahead of correctness.

## Final execution results

- Existing suite: 777 tests passed, exit 0; no skips. Collection counts checked separately. The extra review reproductions are separate probes, not additions to that total.
- Starter pipeline: exit 0 in the temporary clone against a separate database; 40 files identical.
- Frontend build: passed. Read-only browser smoke checks covered all eight views.
- Full-scale reproducibility: exit 0, all 40 files identical; final movement SHA-256 prefix `5cac73c08d04`. Both runs generated 1,000,000 final movement rows and 50,000 final order rows (comparison line counts include the header).
