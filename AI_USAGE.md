# How AI was used on this project

This describes the implementation assistance and the separate review assistance. The personal
section was drafted with Codex from actions recorded in our conversation. It does not claim
that I wrote the implementation myself or independently verified every result.

## What the record shows

**Tools:** Claude Code, in the Claude desktop app, for implementation, as recorded in this
repository; and OpenAI Codex for a separate review conversation, fix prompts and this disclosure
edit. Every message to Claude Code is in [prompt-history/](prompt-history/), including the short
ones. The [Codex prompt archive](prompt-history/codex/README.md) contains all 26 user messages in the saved review conversation through the Render hosting selection on 2 November 2026, including all nine pasted text attachments. Several instructions sent to the implementation assistant were drafted by Codex.

**What the AI wrote:** the first draft of the plan, and all of the code, SQL, tests and
documentation in this repository. That includes the generator, the API, the React page and
the README. Codex subsequently reviewed files, ran checks, reproduced problems and edited
this disclosure. I am not presenting AI-generated code or review suggestions as my own unaided work.

After the challenge review, I also asked Codex to prepare a normal public deployment on Render.
Codex added the Docker build, Render configuration, web entry point and first-start database
initialization, and tested them with a disposable local database. This deployment code is also
AI-written. Local container checks do not mean the site has already been published on Render.

**What the prompts decided:**

| Prompt | What it asked for |
|---|---|
| [1](prompt-history/01-phase-1-plan.md) | The working rules: match the reference projects, keep the code simple and explainable, show real output, never invent a number. The phases and the deliverables |
| [2](prompt-history/02-plan-review.md) | Corrections to the first plan |
| [3](prompt-history/03-validator-and-step-2.md) | Hardening the validator before moving on, then reservations and ATP |
| [4](prompt-history/04-transfers-and-returns.md) | Transfers and returns, and the cases they had to be tested on |
| [5](prompt-history/05-orders-and-late-trucks.md) | Generated orders and late-truck handling, with the rules for each |
| [6](prompt-history/06-anomalies-and-ground-truth.md) | The labelled anomalies, a separate ground truth, and reporting failures honestly |
| [7](prompt-history/07-failure-check-checkpoints-and-dictionary.md) | Comparing record IDs rather than counts, ATP checkpoints, and documenting the seed failures and the anomaly pattern |
| [8](prompt-history/08-believable-truck-times.md) | Removing the one-second truck pattern without adding another |
| [9](prompt-history/09-finish-the-project.md) | Finishing the remaining steps in one go |
| [10](prompt-history/10-fix-review-findings.md) | Fixing the findings in `docs/FINAL_REVIEW.md`, each with a test written first |
| [12](prompt-history/12-balance-dates.md) | Comparing every balance's date after a changed date passed the check. File 11 holds the short messages |

**How results were checked:** the AI ran the commands and quoted their output, which is what the
numbers in the README and `docs/STATUS.md` come from. It also broke the code on purpose in a
scratch copy, to see whether the tests noticed. These checks were run by the AI, so they are
evidence about the code, not evidence of what a person reviewed.

## My role and use of AI

I used AI to help draft this section from the actions recorded in my conversations.

### Where AI sped me up

Claude Code helped me turn the brief into a plan and build the schema, generator, rules,
tests, API, UI and documentation. I requested AI-assisted reviews and passed the findings
back to Claude Code. These covered reservation expiry, two orders asking for the last unit,
checking failing record IDs instead of only counts, and comparing repeated runs.

### Where I relied on it heavily

I relied heavily on Claude Code for writing the code, SQL, tests and fixes, including handling
orders at the same time. I asked for simple code that I could explain and a polished UI.
I also relied on AI review to find problems and suggest checks. The full-scale performance
figures, runs with other seeds and deliberate-bug checks were reported by the assistants;
my own shared terminal output does not verify all of them.

### What I personally reviewed, changed or validated

I requested reviews, passed the findings to Claude Code, ran the checks I have documented,
and committed and pushed the changes myself. I read the progress summaries and sent follow-up
instructions about the problems to fix. The findings and code fixes were AI-assisted; I am
not claiming that I found or wrote them on my own.

On my laptop, I started Docker, generated and loaded starter data, ran validation and pytest,
checked ATP, and compared two starter runs. I shared terminal output showing 50, 194 and 353
passing tests at different stages, and a comparison where all 11 starter files matched.
These are my documented checks, not a claim that I checked every row or every part of the
code. Later assistant-run test results are separate from these personal runs.
