# Prompt 7: comparing record IDs, ATP checkpoints and the data dictionary

Pasted word for word.

---

Before Step 6, fix check_failures. Matching counts alone isn't enough. Compare the actual failing record IDs with the labelled IDs. A missed labelled problem and an unrelated failure must not cancel each other out. Missing record IDs or missing required checks should fail too. Add tests for these cases.

Add a few ATP checkpoints around expiry, truck arrival and cancellation. Decision checks don't cover all those times.

Keep the known seed failures and the anomaly pattern clearly documented. Then continue with the data dictionary and manifest review. Keep the code simple.

Give me normal PowerShell Git commands. I'm okay with one honest combined commit for Steps 4 and 5 if splitting them needs complicated snapshot commands. I'll commit and push myself.
