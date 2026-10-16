> Moved verbatim from `HANDOFF.md` on 2026-10-16 (Phase 18: handoff shrink). Narrative history; the live handoff lives in `HANDOFF.md`.

## Environment / Setup Notes

**Current development machine — corrected in Phase 4.** The previous version of
this section described a different machine and different paths. It is replaced
rather than appended, because a stale environment section is worse than none: it
sends the next person to a directory that does not exist.

- Terminal: `C:\Program Files\<Broker> MT5\terminal64.exe`
- Data folder:
  `C:\Users\YourUser\AppData\Roaming\MetaQuotes\Terminal\0123456789ABCDEF0123456789ABCDEF`
- Workspace: `D:\Projects\signal-to-trade-bridge`
- Python: 3.12.9, the standard install at
  `C:\Users\YourUser\AppData\Local\Programs\Python\Python312\`
- Upstream checkouts: **not present on this machine.** `albrooks` is not
  installed, which is why `tests/integration/test_albrooks_real.py` skips at
  import rather than running its nine tests.

**None of these paths may appear in application logic.** They are recorded here
because a human needs them, and in `.env` files because configuration is
per-machine by definition. A path in the domain layer is a bug.

`D:\Projects\` is the *current* development path, not a requirement. Everything
except MT5 itself must work on a laptop where nothing lives on `D:`.

> **The terminal build is 6230, confirmed from three sources that agree:** the
> running terminal's own `terminal_info().build`, the live indicator snapshot's
> `terminal_build` field, and `terminal64.exe`'s version resource.
> `auto-trade`'s control ids were originally measured on **Alpari build 6184**, so
> the 46-build gap was real and Known Issue 5 was *confirmed*, not merely suspected.
>
> **It is now closed.** `auto-trade terminal-check` re-measured every control on
> 6230 and reported 13 of 13 OK, nothing drifted, nothing missing; the report is at
> `logs/terminal_check.json` and `MEASURED_ON_BUILD` is 6230. The gap being real and
> the gate now being satisfied are both true, and the second is a measurement rather
> than an edit — `tests/unit/test_control_id_evidence.py` fails if the constant moves
> without a recorded probe to match it.

**Upstream revisions at audit time**

| Repo | Branch | Commit |
|---|---|---|
| `al-brooks-price-action-engine` | `main` | `d1fec18` |
| `auto-trade` | `main` | `b88e9e7` |
| `auto-trade` | `feat/observer-verification-and-dashboard` | `3bd3a98` |

---

## Important Repository Information

| | |
|---|---|
| This repository | `git@github.com:ybagheri/signal-to-trade-bridge.git` |
| Local path | wherever you cloned it (no path is recorded in the repository) |
| Upstream A | `git@github.com:ybagheri/al-brooks-price-action-engine.git` |
| Upstream B | `git@github.com:ybagheri/auto-trade.git` |
| Python | `>=3.11` (the higher of the two upstreams' floors) |
| Platform | Windows for MT5; the domain and its tests are platform-independent |
| Dependencies | `albrooks` and `auto-trade` as editable local paths; `MetaTrader5` and `pywinauto` in a Windows extra |

---
