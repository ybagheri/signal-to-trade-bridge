> Moved verbatim from `HANDOFF.md` on 2026-10-16 (Phase 18: handoff shrink). Narrative history; the live handoff lives in `HANDOFF.md`.

## Phase 13 — Final architecture review

**Method.** Built the import graph, read the diagram against it, measured every
module's coverage, and looked for code nothing reaches. Three things came out of
it: one design bug, one documentation lie, and one layer rule that had never been
checked.

### The finding that mattered: a diagnostic that diagnosed nothing

`doctor` built a careful `problems` list, with a reason attached to each entry, and
then **never printed it**. `_render_doctor` drew the status block only. So a
machine with a typo in `BRIDGE_MT5_TERMINAL_PATH` exited 2 and said nothing about
the missing file — the operator got a complaint about control identifiers and a
`REFUSED` mark, neither of which is why the bridge cannot start.

This is the Phase 12 story again, one layer up. `doctor` is the command
`docs/setup.md` tells a reader to run first, so the first thing it did with a broken
configuration was conceal it. Fixed: the problems block is now rendered, and it is
rendered **first**, because the `ok` / `REFUSED` marks are corroboration for a
conclusion the operator has already been given.

The same review found that `doctor` reported the configured terminal path and
silently ignored the configured **data** path. That is the path a reader has to go
and find by hand — the hash-named folder under `MetaQuotes\Terminal`, which
`docs/setup.md` explicitly walks them through locating — so the one value most
likely to be wrong was the one not shown. It is now reported and checked, and the
absence is phrased as what it is: a lost confirmation, not a fault, since a build
can still be read from the executable.

Both branches had **zero** test coverage, which is why neither was noticed.

### The documentation was describing a different project

`docs/architecture.md` §5.1 drew five layers and said "dependency direction is
strictly inward". The code has eight layers plus two root modules. `cli/` was
called `interfaces/`. `configuration/`, `infrastructure/logging/`, `composition.py`
and `live.py` were absent entirely. And the strictness claim was not true:
`application` and `adapters` both import `infrastructure.logging`.

None of that is a criticism of the Phase 0 audit — it could not know what thirteen
phases would need. But a diagram that has drifted is worse than no diagram, because
it is what a new reader trusts. Rewritten to the architecture as built, with the
two edges that are deliberately not inward named in a table rather than left for
somebody to rediscover.

### The rule that was written down and never checked

Only one direction was enforced: `test_domain_isolation.py`, which is the important
one, since a domain import of a private upstream would break the suite on any
machine that could not clone them. Every other edge was documented and unenforced.

`tests/unit/test_layering.py` now walks the whole graph and fails on any edge not
in a permission table, with the failure message naming what was allowed. It also
fails when a **new top-level package appears on disk without being added to the
table** — otherwise a new layer's imports would be unchecked, which is the same as
having no rule.

**The most consequential direction it now guards is the one nobody would have
written a test for:** only `cli/` and the package root may import `composition` or
`live`. If `application.process_signal` imported `composition`, the pipeline would
be assembling its own collaborators — the dependency inversion this architecture
exists to prevent — and every other test in the project would still pass, because
the offending edge points outward and the domain rule does not care.

Two things about writing that test are worth recording:

- **It reported "no violations" the first time it ran, on an empty graph.** The
  module keys were built without the package prefix while the imports carried it, so
  nothing resolved. A test that cannot fail is indistinguishable from one that
  passes, and an architecture test that cannot fail is worse than none, because it
  is trusted. There is now a test whose only job is to assert the graph was built
  (>50 edges, ≥5 layers reached), and a parametrised case per edge so a failure
  names the file and line.
- **It flagged correct code twice.** `live → composition` (both are root modules)
  and `adapters/albrooks/source.py → infrastructure.logging` (the named exception).
  A test that fails on correct code gets disabled, so both were fixed in the test
  rather than worked around, and the exception is now visible in the permission
  table itself instead of hidden in a branch.

### The logging exception, kept and bounded

`application` importing `Event`, `StructuredLogger` and `get_logger` from a concrete
implementation is a real dependency-inversion violation, and `ports/` has no logging
Protocol to legitimise it.

It is being kept, and the reasoning is recorded so nobody "fixes" it by reflex:
logging is a vocabulary, not a capability. `Event` is the same category as
`RejectionReason`, which the domain is entitled to define. A Protocol with one
implementation and no plausible second is ceremony rather than inversion.

What is **not** acceptable is the same import reaching `configure_logging`, which
mutates global handler state. That is forbidden by name and checked, because
application code that reconfigures logging is exactly how this exception becomes a
doorway.

### Also found

- **`live.py` imported `composition._ledger`,** a private name across a module
  boundary. Renamed to `resolve_ledger` and made public, because it is a policy
  decision — the single place that decides whether an idempotency store is available
  — and the live side has to make exactly that decision. Depending on a private name
  is the kind of coupling that looks harmless until the function is renamed and the
  live assembly breaks with no test pointing at the seam.

- **No dead code.** Every module-level name in the package is referenced by
  something. `cli/main.py` was the only file meaningfully below par at 85%, and the
  gap was entirely the paths that refuse bad input — which is the part of a
  trading CLI most worth testing. Now 95%, with `check`'s success path, the
  malformed-signal refusals, and `doctor`'s reporting branches all covered.

- **One more refusal, found by writing a fixture.** A signal carrying a stop price
  with no `stop_basis` is refused with `NO_VALID_STOP`. That is correct behaviour
  and it is now pinned by a test, because "fill in the missing provenance" is the
  obvious tempting shortcut and it is the line that would let this project invent
  where a stop came from.

### Where it stands

**1205 passing, 98% coverage.** Lint, format, type check, domain isolation and the
full layer graph all clean, in a clean shell and in one with the real terminal paths
exported.

**What a review cannot do.** None of this closes Phase 11. The control identifiers
are still measured on build 6184 against a terminal on 6230, and that still needs a
human at the keyboard. This phase made the refusal well-reported; it did not and
could not make the identifiers valid.

---

---

