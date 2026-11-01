# How AI was used on this project

The first part is what the record shows. The second part is for Manikanta to write, and it is
left empty on purpose, so nothing here claims an experience nobody has written down.

## What the record shows

**Tool:** Claude Code, in the Claude desktop app. Every prompt that shaped the work is in
[prompt-history/](prompt-history/), word for word, apart from short messages such as asking
whether a run had finished.

**What the AI wrote:** the first draft of the plan, and all of the code, SQL, tests and
documentation in this repository. That includes the generator, the API, the React page, this
file's first part and the README.

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

**How results were checked:** the AI ran the commands and quoted their output, which is what the
numbers in the README and `docs/STATUS.md` come from. It also broke the code on purpose in a
scratch copy, to see whether the tests noticed. These checks were run by the AI, so they are
evidence about the code, not evidence of what a person reviewed.

## For Manikanta to write

Nothing below this line has been written yet. It has to be in your own words.

### Where AI sped me up

*To be written by Manikanta.*

### Where I relied on it heavily

*To be written by Manikanta.*

### What I personally reviewed, changed or validated

*To be written by Manikanta.*
