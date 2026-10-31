# Prompt 3: validator fixes and Step 2

Pasted word for word.

---

My starter run passed. Before Step 2, fix the validator so missing closing balances, empty data and wrong row counts fail. Make the command return a failure exit code when checks fail. Reject zero and negative sale quantities too. Add tests for these cases.

Then start reservations, ATP and allocation. Test expiry at the exact time, historical reservation states and two orders trying to reserve the last unit. Keep unfinished features clearly marked.

I already pushed the first step to GitHub.
