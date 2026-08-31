# AI Usage

---

## 2026-09-01 — Planning documentation

Two documents written with Claude before any implementation:

- **`docs/DECISIONS.md`** — what was decided and why. Scope calls, the
  constraints they impose, and the stack. Read at the start of any
  implementation task so a settled choice isn't quietly reopened.
- **`docs/FLOWS.md`** — what the system does. Sequence and failure paths for
  all eight request paths and background jobs. Read before implementing a flow,
  updated whenever real behaviour diverges.
