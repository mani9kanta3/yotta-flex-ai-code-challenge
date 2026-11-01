# Prompt 6: anomalies, ground truth and scoring

Pasted word for word.

---

Proceed with Step 5. Keep the code simple and easy for me to explain.

Add all four labelled anomalies from the brief, with valid decoys. Keep ground truth separate from detector inputs. Each injected case should cause only its intended problem, without breaking unrelated records.

Keep the final totals at exactly 1 million movements and 50,000 orders, including injected records. Show that the clean baseline passes, then report expected anomaly failures separately from unexpected failures. Don't hide failures just to make validation pass.

Measure precision and recall for each anomaly type. Repeat both scales with the same seed and compare every exported file, including ground truth.

Also fix the test command typo and clearly say that only 200 full-scale decisions were checked against SQL. Give me the test and Git commands when ready. I'll commit and push myself.
