"""A synthetic MetaTrader terminal, built in a temporary directory.

Shared by every test that needs ``live.check_control_ids`` to read something. The
alternative -- pointing at a real ``terminal64.exe`` on the author's machine -- made
those tests fail everywhere else for a reason unrelated to the code under test, and
leaked one person's install paths into the repository.

A real PE would be a large binary fixture for one string. What the build reader
needs is the *shape* of the version block, and this is that shape -- stated here
because a test that fakes a file format is only honest about the format it fakes.
"""

from __future__ import annotations

from pathlib import Path

from signal_to_trade_bridge.live import MEASURED_ON_BUILD

__all__ = ["Executable", "matching_build", "snapshot_folder"]


class Executable:
    """A file whose bytes contain a version block, so the build reads as expected."""

    def __init__(self, build: int) -> None:
        self.build = build

    def write(self, path: Path) -> None:
        path.write_bytes(("x" * 64 + f"5.0.0.{self.build}" + "y" * 64).encode("utf-16-le"))


def snapshot_folder(tmp_path: Path, build: int) -> Path:
    """A terminal data folder holding one published position snapshot."""
    files = tmp_path / "MQL5" / "Files"
    files.mkdir(parents=True)
    (files / "auto_trade_positions_a.json").write_text(
        f'{{"schema": 1, "terminal_build": {build}}}', encoding="utf-8"
    )
    return tmp_path


def matching_build(tmp_path: Path, build: int | None = None) -> tuple[Path, Path]:
    """A terminal and a snapshot that agree on a build.

    **Defaults to ``live.MEASURED_ON_BUILD`` rather than to a literal.** It was
    hard-coded once, which meant that when a re-measurement legitimately moved the
    constant every assembly test failed -- reported as ``BuildMismatch`` on a *fake*
    terminal, which reads like a real safety refusal and is not one. A test double
    that tracks the thing it stands in for does not have to be edited every time the
    thing moves.
    """
    if build is None:
        build = MEASURED_ON_BUILD
    terminal = tmp_path / "terminal64.exe"
    Executable(build).write(terminal)
    return terminal, snapshot_folder(tmp_path / "data", build)
