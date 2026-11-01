# How AI was used on this project

This describes the implementation assistance and the separate review assistance. The personal
section was drafted with Codex from actions recorded in our conversation. It does not claim
that I wrote the implementation myself or independently verified every result.

## What the record shows

**Tools:** Claude Code, in the Claude desktop app, for implementation, as recorded in this
repository; and OpenAI Codex for a separate review conversation, fix prompts and this disclosure
edit. Every message to Claude Code is in [prompt-history/](prompt-history/), including the short
ones. The Codex prompts have not been added yet. Several instructions sent to the implementation assistant were drafted by Codex.

**What the AI wrote:** the first draft of the plan, and all of the code, SQL, tests and
documentation in this repository. That includes the generator, the API, the React page and
the README. Codex subsequently reviewed files, ran checks, reproduced problems and edited
this disclosure. I am not presenting AI-generated code or review suggestions as my own unaided work.

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

**How results were checked:** the AI ran the commands and quoted their output, which is what the
numbers in the README and `docs/STATUS.md` come from. It also broke the code on purpose in a
scratch copy, to see whether the tests noticed. These checks were run by the AI, so they are
evidence about the code, not evidence of what a person reviewed.

## My role and use of AI

These paragraphs were drafted with AI from the conversation record at my request. They describe
what I did, rather than claiming that I personally performed the assistants' checks.

### Where AI sped me up

AI helped turn the brief into a plan and then into the schema, generator, rules, tests, API,
UI and documentation. I used a separate Codex conversation to question the progress reports
and prepare follow-up instructions. This helped me ask for checks such as reservation expiry,
two orders competing for the last unit, matching failing record IDs instead of only counts,
and comparing repeated runs. Those technical suggestions came from AI review.

### Where I relied on it heavily

I relied heavily on AI for implementation, SQL, concurrency handling, test design and debugging.
I asked for simple code that I could explain and a polished UI, but that does not mean I wrote
the code myself. Later I asked the implementation assistant to finish the remaining work in one
go, with a final review afterwards. The full-scale performance figures, seed sweeps and deliberate
bug checks were reported by the implementation assistant. My shared terminal output does not
independently verify every one of those results.

### What I personally reviewed, changed or validated

I ran commands on my laptop and shared their terminal output in the review conversation. The
shared output includes starting Docker, generating and loading starter data, running validation,
running pytest, checking ATP and comparing two starter runs. It includes runs reporting 50,
194 and 353 passing tests, and a starter comparison where all 11 exported files matched.
These are the checks I can point to from the conversation; they are not a claim that I manually
checked every row or understood every test.

I reviewed the progress summaries, sent follow-up instructions, asked to keep the code simple,
and committed and pushed changes to GitHub myself. The code fixes identified by the review
assistant were sent back to the implementation assistant; I am not claiming them as my own
unaided code changes. I requested a final code review and help drafting this file.
The repository's later test totals should be read as separate assistant-run results unless I
provide my own run output. I have not recorded a personal browser walkthrough or a line-by-line
review of the entire codebase in this conversation.
