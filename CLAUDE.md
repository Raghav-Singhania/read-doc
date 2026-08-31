# read-doc

## AI_USAGE.md is part of "done"

`AI_USAGE.md` is a graded deliverable. After each implementation chunk, append
an entry before treating the chunk as finished. Each entry covers:

- What was built, and the files touched
- The actual prompt or template, where the prompt is the interesting part
- Prompt iterations — what v1 got wrong, what v2 changed
- Where Claude was corrected: wrong assumptions, rejected designs, hallucinated
  APIs, over-engineering that got cut. Record these honestly — an entry with no
  course corrections reads as incomplete
- Where AI removed manual work

Version shipped prompts in the codebase and reference the version from the entry.

## Docs

- `docs/DECISIONS.md` — settled decisions and rationale. Read at the start of
  any implementation task; add new decisions in the same format.
- `docs/FLOWS.md` — behavioural spec for every request path and background job.
  Read before implementing a flow; update it in the same commit whenever
  implemented behaviour diverges. A stale flow doc is worse than none.
- `README.md` — setup, architecture, assumptions, known limitations.
