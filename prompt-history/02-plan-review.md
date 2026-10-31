# Prompt 2: review of the Phase 1 plan

Sent after the first version of the plan. Pasted word for word.

---

The plan is incomplete. Sections including "IDs", "Cardinality", "Lifecycle states", "The four anomalies", and "Scale" lack the actual tables/details. Supply the full brief so exact requirements can be checked.
Fix historical reservations. The reserved(t) formula uses current status. Calculate reservation state from events at time t, including cancellation, confirmation and expiry.
Clarify blocked movements. The formula sums "all rows" into on_hand. Moving existing stock into blocked stock must not increase total stock.
Clarify inbound promises. If an order uses inbound stock, shipping must wait for arrival. Record the promised date and handle late arrivals.
Define concurrent allocation. "A row lock" is too vague. Specify which SKU-location rows are locked, lock them in a consistent order, then recalculate availability inside the transaction.
Don't force counts with invalid sales. The final-day top-up must respect available stock and reservations.

---

Before starting, fill the missing sections for entities, IDs, relationships, lifecycle states, scale and the four anomalies. Check them against the full brief.

Explain how reservations are calculated for a past time, how damaged stock gets blocked without increasing total stock, and how orders using incoming stock wait for arrival. Also explain which rows are locked so two orders can't reserve the same last unit.

Make sure reaching exactly 1 million movements doesn't create sales without stock. Keep Docker setup and correctness tests. Start with the schema and a small generator run after these points are clear.

Give me the exact Git commands when needed and tell me which folder to run them from. I'll commit and push to GitHub myself. Don't do it automatically.
