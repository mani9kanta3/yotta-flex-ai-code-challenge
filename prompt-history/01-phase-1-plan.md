# Prompt 1: the working rules and Phase 1

Sent at the start of the challenge on 31 October 2026. The working rules are word
for word. The problem statement at the end of this prompt is in `docs/brief.md`,
so it is not repeated here.

---

I am Manikanta, a fresher applying for Associate AI Forward Deployed Engineer at YottaFlex AI. This is my 72-hour code challenge. I will submit it in a public GitHub repo, along with my prompt history, and I may have to walk reviewers through every line. LLM tools are allowed, so you are helping me and I must understand everything we build.

DO NOT WRITE ANY CODE YET. Follow the phases below and stop where I say stop.

=== HOW TO WRITE THE CODE ===
- Follow the style of my reference code: Match its naming, folder layout, comment density and level of abstraction.
- Keep it simple and readable. Plain functions and clear names. No clever one-liners, unnecessary design patterns, metaclasses or extra frameworks. Add a library only when it clearly earns its place, and tell me why.
- Comments explain WHY, not what. Keep them short and natural.
- If a simple way and a sophisticated way both meet the requirement, use the simple one and name the tradeoff.
- Stack: Python and PostgreSQL. Suggest the API and UI layer in the plan. I know Django, DRF, FastAPI and React best.
- Everything must be something I can explain for five minutes under questioning. If something is hard, teach it to me before writing it.
- Don't write code like Senior Level

=== PHASE 1: PLAN (then STOP and wait for my OK) ===
Give me:
1. The problem in your own words, and what a reviewer will check.
2. A requirements checklist with one numbered item for EVERY point in the problem statement. We will use it to verify the build at the end.
3. Architecture and full folder structure, with what each file does and why.
4. Database schema: every table, primary key, foreign key, cardinality, optional relationships, effective dates, lifecycle states and bridge tables if needed.
5. Exact rules and formulas: on-hand, reserved, blocked, policy-approved inbound and available-to-promise (ATP), reservation expiry, channel buffers, safety stock, in-transit transfers and overselling handling. Write down every assumption.
6. How the generator reaches 1 million movements in reasonable time on a laptop, and how scale is parameterised.
7. How outcomes and ground truth come from documented rules, and how ground truth stays out of input features.
8. What the working solution exposes: decisions, exceptions, traceability and measurable metrics.
9. Testing plan, build order and time estimate across my remaining time. Say what to cut first if time runs short. The deadline is Monday 2 November 2026, 1:14 AM IST.
10. Risks, and anything unclear in the brief.

=== PHASE 2: BUILD ===
Build one step at a time, in the agreed order. After each step:
- Explain what you built, why, and how to run and verify it.
- Tell me what I should review myself, and what an interviewer may ask about it.
- Suggest a small, meaningful commit message. I will commit myself.
- Stop for my OK before the next step.
Never claim something works unless you ran it. Show me the output.

=== PHASE 3: END-TO-END CHECK ===
- Go through the Phase 1 checklist item by item against the actual code and output, marking each as done, partial or missing with evidence. Fix or honestly report every gap.
- Run from a clean state using only the README steps.
- Run the generator twice with the same seed and compare row counts and checksums.
- Report real metrics and honest limitations. Never invent a number.

=== DELIVERABLES ===
- Working, executable code in one public GitHub repo.
- README: problem, how to install, configure, run and verify, assumptions, key decisions, tradeoffs, testing approach, limitations, and how we know it works.
- Schema and data dictionary, generator command, fixed seed, source manifest, validation summary.
- CSV, JSON or SQL seed data. A small starter dataset goes in the repo. If the full dataset is too large for Git, the same generator must recreate it with one command.
- ground_truth.csv or json, kept separate.
- Tests.
- prompt-history/ folder and AI_USAGE.md, covering where AI sped me up, where I relied on it heavily, and what I personally reviewed, changed or validated. Leave the personal review parts for me to write in my own words. Do not invent them.
