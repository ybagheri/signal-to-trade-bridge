"""Turn the raw probe report into a durable, committable record.

The raw report lands in `logs/`, which is gitignored because it is runtime state
alongside the ledger and the audit trail. But the *conclusion* -- "these six
identifiers were observed in place on build 6230, on this date, by this command" --
is durable project knowledge, and the test that stops somebody editing
`MEASURED_ON_BUILD` without evidence needs it in the repository.

So this reads the raw report and writes a small, stable record: the build, the
command that produced it, the controls, and nothing else. No log paths, no pids, no
timestamps that change every run.

Run it after `auto-trade terminal-check` whenever the measured build changes.
"""

from __future__ import annotations

import json
from pathlib import Path

RAW = Path("logs/terminal_check.json")
OUT = Path("docs/measurements/control_ids.json")

CONTROLS_OF_RECORD = (
    "symbol",
    "volume",
    "stop_loss",
    "take_profit",
    "final_control_buy",
    "final_control_sell",
    "trade_grid",
)


def main() -> int:
    payload = json.loads(RAW.read_text(encoding="utf-8"))
    history = [e for e in payload.get("history", []) if isinstance(e, dict)]
    if not history:
        print("no history in the raw report")
        return 1
    latest = history[-1]

    drifted = latest.get("drifted") or []
    missing = latest.get("missing") or []
    if drifted or missing or latest.get("verdict") != "OK":
        print(
            f"REFUSING to record: verdict={latest.get('verdict')} "
            f"drifted={drifted} missing={missing}"
        )
        return 1

    rows = {r["control"]: r for r in latest.get("controls", []) if isinstance(r, dict)}
    missing_from_report = [c for c in CONTROLS_OF_RECORD if c not in rows]
    if missing_from_report:
        print(f"REFUSING to record: the probe did not report {missing_from_report}")
        return 1

    record = {
        "_comment": [
            "Durable record of a control-identifier measurement. Produced by:",
            "  auto-trade terminal-check",
            "which is upstream's read-only probe: it opens the order dialog, reads the",
            "control tree, reports each expected identifier, and closes the dialog. No",
            "field is written and no final control is clicked.",
            "",
            "tests/unit/test_control_id_evidence.py reads this file, so MEASURED_ON_BUILD",
            "in src/signal_to_trade_bridge/live.py cannot be changed without a record",
            "here that says the same build was measured and nothing was found drifted or",
            "missing. Regenerate it with scripts/record_control_ids.py after any MT5",
            "update -- and only if the probe reports OK for every control.",
        ],
        "measured_on_build": (latest.get("terminal") or {}).get("build"),
        "observed_at": latest.get("observed_at"),
        "command": "auto-trade terminal-check",
        "verdict": latest.get("verdict"),
        "checked": latest.get("checked"),
        "drifted": drifted,
        "missing": missing,
        "account_type": latest.get("account"),
        "controls": {
            # **Keyed by upstream's own label, verbatim.** The record is meant to be
            # compared against `control_probe.EXPECTED_FIELDS` and
            # `EXPECTED_FINAL_CONTROLS` mechanically, so the keys are the labels
            # upstream uses -- `symbol`, `buy` -- rather than a friendlier renaming
            # of them. The first version called them `final_control_buy` to match the
            # probe's report rows, which meant the comparison had to be written twice
            # and got it wrong once.
            **{
                label: {"identifier": rows[label]["expected"], "status": rows[label]["status"]}
                for label in ("symbol", "volume", "stop_loss", "take_profit", "trade_grid")
            },
            **{
                label: {
                    "identifier": rows[f"final_control_{label}"]["expected"],
                    "status": rows[f"final_control_{label}"]["status"],
                }
                for label in ("buy", "sell")
            },
        },
        "also_present": sorted(
            r["control"]
            for r in latest.get("controls", [])
            if isinstance(r, dict) and r["control"] not in CONTROLS_OF_RECORD
        ),
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {OUT} for build {record['measured_on_build']}")
    for name, value in record["controls"].items():
        print(f"  {name:22} {value['identifier']:>6}  {value['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
