"""Configuration loading.

Settings come from three places, in a fixed order of precedence: explicit
arguments, then environment variables, then a ``.env`` file, then the defaults
below. The order matters more than it looks: the defaults are the *safe* ones, so
a misconfigured or absent environment produces a dry-run bridge that cannot
execute, rather than a live one that can.

The naming convention is a single ``BRIDGE_`` prefix. That prefix is what keeps
this project from colliding with the execution project's 23 ``AUTO_TRADE_*``
variables, several of which default to absolute paths on another machine. The
bridge reads none of them, and the prefix is what makes that legible in a process
environment listing.

Secrets are never logged, never printed by a diagnostic command, and never
written to a decision record. ``BridgeConfig.__repr__`` is defined explicitly for
that reason: a frozen dataclass's default repr would dump every field, and the
configuration is where a broker password would live if someone put one there.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

from signal_to_trade_bridge.domain.enums import TakeProfitSource
from signal_to_trade_bridge.domain.errors import ConfigurationError
from signal_to_trade_bridge.domain.models import RiskParameters

__all__ = [
    "BRIDGE_ENV_PREFIX",
    "BridgeConfig",
    "config_from_env",
    "load_dotenv",
]

#: Every environment variable this project reads carries this prefix.
BRIDGE_ENV_PREFIX = "BRIDGE_"

#: Values accepted as ``true``. Matches the execution project's own convention, so
#: one muscle memory works across both.
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})

#: Default location of the ``.env`` file, relative to the working directory.
DEFAULT_ENV_FILE = ".env"


def _flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    text = raw.strip().lower()
    if text in _TRUE_VALUES:
        return True
    if text in _FALSE_VALUES:
        return False
    raise ConfigurationError(
        f"{name}={raw!r} is not a boolean; use one of "
        f"{sorted(_TRUE_VALUES)} or {sorted(_FALSE_VALUES)}"
    )


def _decimal(name: str, default: str | None) -> Decimal | None:
    raw = os.getenv(name)
    if raw is None:
        return Decimal(default) if default is not None else None
    text = raw.strip()
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise ConfigurationError(f"{name}={raw!r} is not a number") from exc


def _float(name: str, default: float | None) -> float | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name}={raw!r} is not a number") from exc


def _int(name: str, default: int | None) -> int | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name}={raw!r} is not an integer") from exc


def _require_int(name: str, default: int) -> int:
    """An integer setting where ``0`` is a mistake rather than a value.

    Separate from :func:`_int` because the two call sites want different
    behaviour for a missing variable, and because the ``or default`` idiom that
    would collapse a configured ``0`` into the default is precisely the bug this
    exists to prevent. ``BAR_COUNT=0`` means "ask for no bars", which is never
    what anyone meant, and it has to reach validation rather than being silently
    replaced by 300.
    """
    value = _int(name, default)
    assert value is not None  # A non-None default guarantees this.
    return value


def _symbol_set(name: str) -> frozenset[str]:
    raw = os.getenv(name, "")
    return frozenset(item.strip().upper() for item in raw.split(",") if item.strip())


def _path(name: str, default: Path | None) -> Path | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return Path(raw.strip())


def load_dotenv(
    path: Path | None = None, *, environ: Mapping[str, str] | None = None
) -> dict[str, str]:
    """Read a ``.env`` file into a mapping, without touching the environment.

    Returned rather than applied, and ``environ`` is injectable, so a test can
    exercise the parser without mutating global process state. Applying the result
    to ``os.environ`` is the caller's decision, made once, at startup.

    Deliberately minimal: ``KEY=VALUE`` lines, ``#`` comments, optional ``export``
    prefix, optional surrounding quotes. No variable expansion, no nested quoting,
    no multi-line values. A dotenv parser that grows to handle those becomes a
    source of behaviour nobody can predict, and this project's configuration is
    fifteen scalars.

    Malformed lines raise rather than being skipped. A typo in a risk percentage
    that is silently ignored is worse than a startup failure, because the process
    would run with a default nobody chose.
    """
    target = environ if environ is not None else os.environ
    del target  # The file is read, not merged; `environ` is accepted for symmetry.

    values: dict[str, str] = {}
    if path is None or not path.is_file():
        return values

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigurationError(f"could not read the environment file at {path}: {exc}") from exc

    for number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ConfigurationError(f"{path}:{number} is not a KEY=VALUE assignment: {raw_line!r}")
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            raise ConfigurationError(f"{path}:{number} has an empty key: {raw_line!r}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def _apply_env_file(values: Mapping[str, str]) -> None:
    """Seed ``os.environ`` from a ``.env`` file without overwriting real settings.

    Real environment variables win, because someone who exported a variable in
    their shell meant it, and a file on disk silently beating that would make the
    shell unusable for overrides.
    """
    for key, value in values.items():
        os.environ.setdefault(key, value)


@dataclass(frozen=True, slots=True)
class BridgeConfig:
    """Everything the bridge can be configured with.

    Grouped into four concerns that are deliberately not mixed:

    * **risk** -- the money rules. What fraction, what ratio, which stop policy.
    * **policy** -- what the bridge is permitted to do. Direction, symbols,
      concurrency, spread.
    * **execution** -- whether an order may be sent, and to where.
    * **environment** -- machine-specific paths and per-machine facts.

    They are separate because they change for different reasons and are owned by
    different people. A risk percentage is a trading decision; a terminal path is a
    fact about one laptop. Flattening them into one settings blob is how a
    terminal path ends up in a risk calculation.
    """

    risk: RiskParameters = field(default_factory=RiskParameters)

    # -- policy ----------------------------------------------------------
    #: Refuse a tradable signal above this spread, in price units. Also a policy
    #: setting, not a risk one: it is about market conditions rather than money.
    max_spread: Decimal | None = None

    # -- execution -------------------------------------------------------
    #: Master switch. Defaults to ``False``, so a fresh checkout cannot trade.
    #: This is checked in the bridge's own decision path, before the executor is
    #: reached, and is independent of any switch the execution project has.
    execution_enabled: bool = False
    #: Produce the full decision, log it, and stop before the executor.
    dry_run: bool = True
    #: The terminal must already be running and logged in. The bridge never
    #: launches it, because a process that starts a trading terminal on import is
    #: a process that can start it by accident.
    require_running_terminal: bool = True
    #: Where the execution project's audit log and ledger live.
    log_directory: Path = Path("logs")

    # -- environment -----------------------------------------------------
    #: Path to ``terminal64.exe``. ``None`` means "let the bindings find it",
    #: which is the right default on a machine with one terminal and the wrong
    #: one on a machine with several.
    mt5_terminal_path: Path | None = None
    #: The terminal's data directory, where the MQL5 file bridge and the
    #: position-snapshot indicator write.
    mt5_data_path: Path | None = None
    #: Bars to request per analysis.
    bar_count: int = 300
    #: Log level for the bridge's own structured events.
    log_level: str = "INFO"
    #: Emit one JSON object per event rather than human-readable lines.
    log_json: bool = False

    def __post_init__(self) -> None:
        if self.bar_count <= 0:
            raise ConfigurationError(f"bar_count must be positive, got {self.bar_count}")
        if self.log_level.upper() not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ConfigurationError(
                f"log_level={self.log_level!r} is not one of DEBUG, INFO, WARNING, ERROR, CRITICAL"
            )

    @property
    def can_execute(self) -> bool:
        """Whether this configuration is permitted to send an order.

        Both conditions, and the ordering is deliberate: dry run overrides the
        enable flag rather than the other way round. A configuration with
        ``execution_enabled=True`` and ``dry_run=True`` is a dry run, and that has
        to be true regardless of which value a caller changed last.
        """
        return self.execution_enabled and not self.dry_run

    def __repr__(self) -> str:
        # Explicit, because the dataclass default would print every field. This
        # is the configuration object, which is where a broker credential would
        # live if anyone put one here, and a stray ``print(config)`` in a
        # diagnostic is exactly the kind of thing that ends up in a bug report.
        return (
            f"BridgeConfig(execution_enabled={self.execution_enabled}, "
            f"dry_run={self.dry_run}, "
            f"risk_percent={self.risk_percent}, "
            f"reward_risk_ratio={self.reward_risk_ratio}, "
            f"take_profit_source={self.take_profit_source.value}, "
            f"bar_count={self.bar_count}, "
            f"mt5_terminal_path={'set' if self.mt5_terminal_path else 'unset'}, "
            f"mt5_data_path={'set' if self.mt5_data_path else 'unset'})"
        )

    @property
    def risk_percent(self) -> Decimal:
        return self.risk.risk_percent

    @property
    def reward_risk_ratio(self) -> Decimal:
        return self.risk.reward_risk_ratio

    @property
    def take_profit_source(self) -> TakeProfitSource:
        return self.risk.take_profit_source


def config_from_env(*, env_file: Path | None = None, apply: bool = True) -> BridgeConfig:
    """Build a :class:`BridgeConfig` from the environment.

    With ``apply=True`` a ``.env`` file seeds ``os.environ`` first, without
    overwriting anything already set. With ``apply=False`` only the process
    environment is read, which is what a test wants: it can set the variables it
    cares about and know nothing else leaked in from a file on the developer's
    disk.

    Raises :class:`ConfigurationError` for an unparsable value rather than falling
    back to a default. A risk percentage that silently reverted to 0.5 because the
    configured value was a typo would be a system trading at a risk level nobody
    chose, and the only defence is to refuse to start.
    """
    if apply:
        _apply_env_file(load_dotenv(env_file or Path(DEFAULT_ENV_FILE)))

    p = BRIDGE_ENV_PREFIX
    take_profit_raw = os.getenv(f"{p}TAKE_PROFIT_SOURCE", "").strip().upper()
    if take_profit_raw:
        try:
            take_profit_source = TakeProfitSource(take_profit_raw)
        except ValueError as exc:
            valid = ", ".join(sorted(member.value for member in TakeProfitSource))
            raise ConfigurationError(
                f"{p}TAKE_PROFIT_SOURCE={take_profit_raw!r} is not one of: {valid}"
            ) from exc
    else:
        take_profit_source = TakeProfitSource.RR_FALLBACK

    try:
        risk = RiskParameters(
            risk_percent=_decimal(f"{p}RISK_PERCENT", "0.5") or Decimal("0.5"),
            reward_risk_ratio=_decimal(f"{p}REWARD_RISK_RATIO", "1.0") or Decimal("1.0"),
            take_profit_source=take_profit_source,
            allow_volatility_fallback_stop=_flag(f"{p}ALLOW_VOLATILITY_FALLBACK_STOP", False),
            minimum_evidence_score=_float(f"{p}MINIMUM_EVIDENCE_SCORE", None),
            allow_buy=_flag(f"{p}ALLOW_BUY", True),
            allow_sell=_flag(f"{p}ALLOW_SELL", True),
            allowed_symbols=_symbol_set(f"{p}ALLOWED_SYMBOLS"),
            max_spread=_decimal(f"{p}MAX_SPREAD", None),
            max_open_positions=_int(f"{p}MAX_OPEN_POSITIONS", None),
        )
    except ValueError as exc:
        # RiskParameters raises ValueError from its own validation, inside this
        # block. Converting it means a caller handles one exception type for any
        # configuration problem, rather than two depending on which layer
        # happened to notice first.
        raise ConfigurationError(str(exc)) from exc

    try:
        return BridgeConfig(
            risk=risk,
            execution_enabled=_flag(f"{p}EXECUTION_ENABLED", False),
            dry_run=_flag(f"{p}DRY_RUN", True),
            require_running_terminal=_flag(f"{p}REQUIRE_RUNNING_TERMINAL", True),
            log_directory=_path(f"{p}LOG_DIR", Path("logs")) or Path("logs"),
            mt5_terminal_path=_path(f"{p}MT5_TERMINAL_PATH", None),
            mt5_data_path=_path(f"{p}MT5_DATA_PATH", None),
            # `or 300` would be wrong here: a configured 0 is falsy, so the
            # `or` would silently replace the caller's value with the default and
            # the validation below would never see the mistake. A person who set
            # BAR_COUNT=0 meant it, and the right answer is to tell them no.
            bar_count=_require_int(f"{p}BAR_COUNT", 300),
            log_level=os.getenv(f"{p}LOG_LEVEL", "INFO").strip().upper(),
            log_json=_flag(f"{p}LOG_JSON", False),
        )
    except ValueError as exc:
        # RiskParameters and BridgeConfig both raise ValueError from their own
        # validation. Converting here means a caller has one exception type to
        # handle for any configuration problem, rather than two depending on
        # which layer happened to notice.
        raise ConfigurationError(str(exc)) from exc
