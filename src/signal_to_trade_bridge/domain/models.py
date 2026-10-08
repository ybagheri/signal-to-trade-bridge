"""Domain value objects.

Every model here is frozen and validated at construction. That is a deliberate
trade: validation cannot be forgotten, because there is no way to build an
invalid one. The cost is that construction can raise, which is why the
application layer wraps construction in a controlled path rather than letting a
``ValidationError`` escape into a trading loop.

Prices and volumes are :class:`~decimal.Decimal`, never ``float``. The upstream
price-action engine uses ``float`` throughout and the execution project uses
``Decimal``; ``Decimal`` is chosen here because position sizing is arithmetic
where drift is a monetary error. A 0.01 lot error on a gold contract is real
money, and a float cannot represent it reliably.

Every model also carries a ``to_dict()`` that produces plain JSON-compatible
types. That is not decoration: a traceable decision is a requirement, and a value
object that cannot be serialised cannot be logged, journalled or replayed.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from signal_to_trade_bridge.domain.enums import (
    DecisionAction,
    Direction,
    SignalAction,
    StopSource,
    TakeProfitSource,
)

__all__ = [
    "AccountBalance",
    "ExecutionRequest",
    "ExecutionResult",
    "PositionSize",
    "PreSubmitDelay",
    "RiskBudget",
    "RiskParameters",
    "Signal",
    "StopLoss",
    "SymbolSpec",
    "TakeProfit",
    "TradeDecision",
    "TradeIntent",
    "achieved_ratio",
    "canonical_ratio",
    "utc_now",
]

#: Prices and volumes below this are treated as zero. MT5 instruments quote in
#: 2 to 8 decimal places, so the smallest meaningful increment is 1e-8; anything
#: below that is a rounding artefact, not a price.
PRICE_EPSILON = Decimal("1e-8")


def utc_now() -> datetime:
    """Timezone-aware current time.

    Exists so that a test can reason about "now" without a clock seam in every
    model. Note that it is a *convenience*, not the project's time source: the
    application layer takes an injected ``now`` callable, and a test injects a
    fixed one.
    """
    return datetime.now(UTC)


def _positive(value: Decimal, name: str) -> Decimal:
    if value <= 0:
        raise ValueError(f"{name} must be positive, got {value}")
    return value


@dataclass(frozen=True, slots=True)
class AccountBalance:
    """What the bridge knows about the account, for sizing purposes.

    Balance rather than equity is the default basis, because the brief specifies a
    percentage of *balance* and because equity moves with open positions, which
    would make the risk of a new trade depend on trades already running.

    ``open_positions`` is carried because a maximum-concurrent-positions policy
    needs it, and because the upstream execution project exposes it even though
    its real adapter never populates it -- which is why the bridge's MT5 adapter
    has to read it itself.
    """

    balance: Decimal
    currency: str
    equity: Decimal | None = None
    open_positions: int = 0
    #: The account the terminal is actually logged into, when known. Recorded for
    #: traceability. The bridge does not use it to authorise anything, because
    #: the execution project owns the demo-only policy and the bridge must not
    #: become a second, weaker copy of it.
    account_login: int | None = None
    server: str | None = None

    def __post_init__(self) -> None:
        _positive(self.balance, "balance")
        if not self.currency.strip():
            raise ValueError("currency is required")
        if self.equity is not None and self.equity <= 0:
            raise ValueError(f"equity must be positive when present, got {self.equity}")
        if self.open_positions < 0:
            raise ValueError(f"open_positions cannot be negative, got {self.open_positions}")

    @property
    def effective_balance(self) -> Decimal:
        """The balance sizing is based on.

        Always the balance, never the equity. Documented rather than
        configurable in Phase 1 because the brief is explicit, and because a
        silent switch between the two would make the risk of a trade depend on
        positions that are already open.
        """
        return self.balance

    def to_dict(self) -> dict[str, Any]:
        return {
            "balance": str(self.balance),
            "currency": self.currency,
            "equity": str(self.equity) if self.equity is not None else None,
            "open_positions": self.open_positions,
            "account_login": self.account_login,
            "server": self.server,
        }


@dataclass(frozen=True, slots=True)
class SymbolSpec:
    """A symbol's trading contract.

    Neither upstream project has this, and it is the reason the bridge cannot
    delegate position sizing. ``albrooks`` treats a symbol as an opaque string;
    the execution project drives the MT5 order dialog by clicking on it and can
    therefore only read what a human could see on screen.

    The fields are the ones position sizing needs, and they are named after the
    MT5 fields they come from so the mapping is obvious at the adapter boundary.

    ``tick_value_profit`` and ``tick_value_loss`` are both present because they
    can differ. On a hedging account and on some CFDs they do, and the *larger* of
    the two is what a risk calculation must use: it is an upper bound on the money
    one tick can cost, so a position sized from it cannot exceed its budget when
    the stop is hit in either direction. See :attr:`conservative_tick_value` for
    why this is the maximum rather than the minimum.
    """

    symbol: str
    #: Contract size, in units of the base asset per lot. MT5's
    #: ``trade_contract_size``.
    contract_size: Decimal
    #: Smallest price increment. MT5's ``trade_tick_size``.
    tick_size: Decimal
    #: Money per ``tick_size`` move, per lot, in the *account* currency, when
    #: price rises. MT5's ``trade_tick_value_profit``.
    tick_value_profit: Decimal
    #: Money per ``tick_size`` move, per lot, in the account currency, when price
    #: falls. MT5's ``trade_tick_value_loss``.
    tick_value_loss: Decimal
    volume_min: Decimal
    volume_max: Decimal
    volume_step: Decimal
    #: Decimal places in a quote. MT5's ``digits``.
    digits: int
    #: Smallest price increment as the broker displays it. MT5's ``point``.
    #: Distinct from ``tick_size``: a 5-digit EURUSD has a point of 0.00001 and a
    #: tick size that is often the same, but for other instruments they differ,
    #: and confusing them is a classic sizing bug.
    point: Decimal
    currency: str = ""
    currency_profit: str = ""
    currency_margin: str = ""

    def __post_init__(self) -> None:
        if not self.symbol.strip():
            raise ValueError("symbol is required")
        _positive(self.contract_size, "contract_size")
        _positive(self.tick_size, "tick_size")
        if self.tick_value_profit < 0 or self.tick_value_loss < 0:
            raise ValueError("tick values cannot be negative")
        _positive(self.volume_min, "volume_min")
        _positive(self.volume_max, "volume_max")
        _positive(self.volume_step, "volume_step")
        if self.volume_min > self.volume_max:
            raise ValueError(
                f"volume_min {self.volume_min} cannot exceed volume_max {self.volume_max}"
            )
        if self.digits < 0:
            raise ValueError(f"digits cannot be negative, got {self.digits}")
        _positive(self.point, "point")

    @property
    def symbol_normalised(self) -> str:
        """Upper-cased symbol, matching the execution project's own convention."""
        return self.symbol.strip().upper()

    @property
    def conservative_tick_value(self) -> Decimal:
        """The **larger** of the two tick values, i.e. an upper bound on the loss.

        Corrected in Phase 4. Phase 1 implemented this as ``min``, on the reasoning
        that "a size that is safe on paper has to be safe on the losing side".
        The reasoning was right and the implementation was its mirror image, and
        it under-sized every position on an instrument where the two values
        differ.

        Why the larger one is the conservative one, in the arithmetic rather than
        in adjectives::

            risk_per_unit = ticks * tick_value
            volume        = risk_amount / risk_per_unit

        ``volume`` is inversely proportional to ``tick_value``, so the *smaller*
        tick value produces the *larger* position. Taking the minimum therefore
        sizes for the cheapest possible tick and is the least conservative choice
        available. On a hedging symbol reporting $1 a tick in profit and $2 a tick
        in loss, a $50 budget over a 300-tick stop came out at 0.16 lots -- and if
        that stop were hit, the loss would be ``300 * $2 * 0.16 = $96``, twice the
        budget. The arithmetic downstream was correct throughout; the divisor was
        wrong.

        Taking the maximum bounds the loss from above in **both** directions, so a
        long whose stop is hit on a falling price and a short whose stop is hit on
        a rising one are both covered. It can under-size a position whose real
        tick value happens to be the smaller of the two, and that is the correct
        direction to err: an under-spent budget is a disappointment, an over-spent
        one is a loss.

        The exact per-direction value is knowable -- ``tick_value_loss`` for a long,
        ``tick_value_profit`` for a short -- and using it would size both sides
        optimally. It is not used, because it makes the size depend on the
        direction, which means two more code paths, two more combinations to test,
        and a way for a long to be sized with a short's number. A single bound
        that is safe for both is worth more here than the tightness it gives up.
        """
        return max(self.tick_value_profit, self.tick_value_loss)

    def risk_per_unit(self, price_distance: Decimal) -> Decimal:
        """Money at risk per lot over a price move of ``price_distance``.

        ``price_distance / tick_size`` ticks, times
        :attr:`conservative_tick_value`. This is the quantity that makes sizing
        instrument-agnostic: it works for a 5-digit forex pair, for gold quoted in
        dollars per ounce, and for an index CFD, because none of them needs a
        special case.

        An upper bound rather than the exact figure, for the reason
        :attr:`conservative_tick_value` gives. Callers must therefore treat it as
        the worst case, not as a prediction.
        """
        if price_distance <= 0:
            return Decimal(0)
        ticks = price_distance / self.tick_size
        return ticks * self.conservative_tick_value

    def round_volume(self, volume: Decimal, *, round_down: bool = True) -> Decimal:
        """Round a volume to the broker's step.

        ``round_down`` defaults to ``True`` and that default is a safety property,
        not a formatting choice: rounding a volume *up* to reach the next step can
        push the position's risk above the configured budget, while rounding down
        leaves it fractionally under. Under-spending a risk budget is a
        disappointment; over-spending it is a loss.
        """
        if self.volume_step <= 0:
            return volume
        steps = volume / self.volume_step
        steps = steps.to_integral_value(rounding="ROUND_FLOOR" if round_down else "ROUND_CEILING")
        return steps * self.volume_step

    def clamp_volume(self, volume: Decimal) -> Decimal:
        """Clamp a volume into the broker's permitted range.

        Clamping *up* to ``volume_min`` is deliberately refused, and this method
        will not do it however it is called. A volume below the broker minimum is
        a refusal, not something to round up: flooring up would place a position
        whose risk exceeds the budget by an unbounded amount, and it is the single
        most dangerous line in any position sizer.

        So a below-minimum volume is clamped **down to zero**, which is
        unambiguously invalid and therefore forces the caller to notice. Returning
        the sub-minimum value unchanged would be worse: it looks like a usable
        volume, and a caller that only checked "is it within the range" would pass
        it to the broker.
        """
        if volume < self.volume_min:
            return Decimal(0)
        return min(volume, self.volume_max)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol_normalised,
            "contract_size": str(self.contract_size),
            "tick_size": str(self.tick_size),
            "tick_value_profit": str(self.tick_value_profit),
            "tick_value_loss": str(self.tick_value_loss),
            "conservative_tick_value": str(self.conservative_tick_value),
            "volume_min": str(self.volume_min),
            "volume_max": str(self.volume_max),
            "volume_step": str(self.volume_step),
            "digits": self.digits,
            "point": str(self.point),
            "currency": self.currency,
            "currency_profit": self.currency_profit,
            "currency_margin": self.currency_margin,
        }


@dataclass(frozen=True, slots=True)
class Signal:
    """A normalised trading signal, independent of where it came from.

    This is the anti-corruption layer's destination. The upstream engine produces
    a ``dict``; the execution project consumes a different model again. Neither
    shape leaks past the adapters, so a change in either leaves this untouched.

    ``source_metadata`` carries the upstream reading verbatim -- the plan, the
    evidence, the vetoes, the explanation. It is not decorative: it is what makes
    a trade decision explainable afterwards, and without it a refusal could say
    only "no stop" and not "the setup that produced no stop was H2 on EURUSD H1
    at bar 412".
    """

    #: Stable, deterministic identity derived from signal content. NOT a UUID: a
    #: fresh UUID per call would defeat the downstream dedup ledger, which is
    #: keyed on this value and has to survive a process restart to be useful.
    signal_id: str
    symbol: str
    timeframe: str
    action: SignalAction
    direction: Direction
    #: The last close of the analysed bar. Required for a tradable signal, and
    #: optional for an abstention.
    #:
    #: Optional rather than defaulted, because the two cases are genuinely
    #: different things and a single mandatory field would force one of them to
    #: carry a placeholder. The upstream engine returns ``plan: None`` on every
    #: abstention, so "the engine found nothing to trade" -- the commonest outcome
    #: in the system -- has no entry price at all. Requiring one would mean
    #: substituting the last close for an entry, which is precisely the
    #: substitution this project refuses to make.
    entry: Decimal | None = None
    stop_loss: Decimal | None = None
    take_profit: Decimal | None = None
    #: Where the stop came from upstream, when the source said. An
    #: ``ATR_FALLBACK`` stop is structurally empty by the upstream engine's own
    #: definition, and the stop policy needs to know that.
    stop_basis: str = "NONE"
    take_profit_basis: str = "NONE"
    #: The upstream engine's evidence score, 0..1. **Not a probability** -- the
    #: engine says so in three separate places, and treating it as a win rate
    #: would be the most consequential misreading available in this design.
    evidence_score: float | None = None
    #: The upstream candidate identifier, e.g. ``"pullback_h#0"``. Carried so a
    #: decision can name the detector that produced it.
    setup_id: str = ""
    #: The newest bar the analysis was allowed to read. Part of the identity,
    #: because "the same reading" means "the same reading on the same bar".
    bar_index: int = -1
    #: The close time of that bar, as a Unix epoch in seconds. The upstream
    #: engine timestamps bars as floats in seconds and has no datetime type, so
    #: this stays in the upstream's own representation rather than guessing a
    #: timezone.
    bar_time: float | None = None
    observed_at: datetime = field(default_factory=utc_now)
    source: str = ""
    source_metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.signal_id.strip():
            raise ValueError("signal_id is required")
        if not self.symbol.strip():
            raise ValueError("symbol is required")
        if self.action.is_tradable:
            # A tradable action with no entry is a signal nobody can act on, and
            # allowing it to be constructed would push the failure into the risk
            # service as a confusing arithmetic failure rather than a clear
            # validation error here.
            if not self.direction.is_tradable:
                raise ValueError(
                    f"action {self.action.value} requires a tradable direction, "
                    f"got {self.direction.value}"
                )
            if self.entry is None:
                raise ValueError(
                    f"action {self.action.value} requires an entry price; "
                    "an abstention is the only signal that may omit one"
                )
            _positive(self.entry, "entry")
        if self.bar_index < -1:
            raise ValueError(f"bar_index cannot be below -1, got {self.bar_index}")
        if self.evidence_score is not None and not 0.0 <= self.evidence_score <= 1.0:
            raise ValueError(f"evidence_score must be between 0 and 1, got {self.evidence_score}")
        if self.bar_index < -1:
            raise ValueError(f"bar_index cannot be below -1, got {self.bar_index}")
        if self.evidence_score is not None and not 0.0 <= self.evidence_score <= 1.0:
            raise ValueError(f"evidence_score must be between 0 and 1, got {self.evidence_score}")

    @property
    def symbol_normalised(self) -> str:
        return self.symbol.strip().upper()

    @property
    def is_tradable(self) -> bool:
        """Whether this signal asks for a trade at all."""
        return self.action.is_tradable and self.direction.is_tradable

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "symbol": self.symbol_normalised,
            "timeframe": self.timeframe,
            "action": self.action.value,
            "direction": self.direction.value,
            "entry": str(self.entry),
            "stop_loss": str(self.stop_loss) if self.stop_loss is not None else None,
            "take_profit": str(self.take_profit) if self.take_profit is not None else None,
            "stop_basis": self.stop_basis,
            "take_profit_basis": self.take_profit_basis,
            "evidence_score": self.evidence_score,
            "setup_id": self.setup_id,
            "bar_index": self.bar_index,
            "bar_time": self.bar_time,
            "observed_at": self.observed_at.isoformat(),
            "source": self.source,
        }


@dataclass(frozen=True, slots=True)
class StopLoss:
    """A validated stop loss, with the distance that sizing will use."""

    price: Decimal
    #: Distance from entry, always positive. Stored as a magnitude rather than
    #: recomputed from a sign, because every consumer needs the distance and none
    #: of them should be re-deriving it and risking a sign error.
    distance: Decimal
    source: StopSource = StopSource.SIGNAL
    basis: str = ""

    def __post_init__(self) -> None:
        _positive(self.price, "stop price")
        _positive(self.distance, "stop distance")

    def to_dict(self) -> dict[str, Any]:
        return {
            "price": str(self.price),
            "distance": str(self.distance),
            "source": self.source.value,
            "basis": self.basis,
        }


@dataclass(frozen=True, slots=True)
class TakeProfit:
    """A validated take profit, with the distance and where it came from."""

    price: Decimal
    distance: Decimal
    source: TakeProfitSource = TakeProfitSource.RR_DERIVED
    basis: str = ""

    def __post_init__(self) -> None:
        _positive(self.price, "take profit price")
        _positive(self.distance, "take profit distance")

    def to_dict(self) -> dict[str, Any]:
        return {
            "price": str(self.price),
            "distance": str(self.distance),
            "source": self.source.value,
            "basis": self.basis,
        }


@dataclass(frozen=True, slots=True)
class RiskParameters:
    """How much to risk, and what reward to aim for.

    Every field is configurable and none is hard-coded. The defaults are the
    brief's starting configuration -- 0.5% per trade at 1:1 -- and changing them
    to 0.25% or 1.00% is a configuration change, not a code change.
    """

    #: Percentage of account balance to risk, e.g. ``0.5`` for half a percent.
    risk_percent: Decimal = Decimal("0.5")
    #: Target reward divided by risk. ``1.0`` is 1:1.
    reward_risk_ratio: Decimal = Decimal("1.0")
    #: The floor the achieved reward:risk must clear for a **signal's own** target
    #: to be used. ``None`` (the default) disables the check, which means the
    #: configured ratio decides only the *fallback* distance and any structurally
    #: sound target is taken as the engine measured it.
    #:
    #: Added in Phase 5, and off by default on purpose. ``RR_FALLBACK`` has always
    #: documented itself as "I want 1:1 as the floor", but nothing compared the
    #: signal's implied ratio against anything, so a structurally sound target at
    #: 0.2:1 was accepted silently under the default policy. Turning the floor on
    #: is a trading decision -- it starts replacing engine targets with the
    #: configured distance -- so it is opt-in rather than a change to what
    #: ``RR_FALLBACK`` means for an existing deployment.
    #:
    #: It filters only the signal's target. Under ``RR_DERIVED`` there is no
    #: candidate to filter and the configured ratio always wins, which is what
    #: that policy is for.
    minimum_reward_risk_ratio: Decimal | None = None
    #: Whether a signal's own target may be used, or the ratio always wins.
    take_profit_source: TakeProfitSource = TakeProfitSource.RR_FALLBACK
    #: Refuse a signal target that is not structurally defensible, using the
    #: ratio instead. Off by default, because the upstream engine can produce a
    #: volatility-fallback target and silently preferring the ratio over a stated
    #: target is a decision the trader should make.
    allow_volatility_fallback_stop: bool = False
    #: Minimum evidence score, 0..1, for a signal to be considered. **Not a
    #: probability.** ``None`` disables the filter.
    minimum_evidence_score: float | None = None
    #: Refuse a tradable signal entirely when false. Separates "the setup was
    #: read correctly" from "this system is allowed to act on it", so a long-only
    #: or short-only configuration needs no code change.
    allow_buy: bool = True
    allow_sell: bool = True
    #: Symbols this bridge will trade. Empty means "no restriction from the
    #: bridge"; the execution project has its own allowlist, and the two are
    #: independent, so a signal must satisfy both.
    allowed_symbols: frozenset[str] = frozenset()
    #: Maximum spread, in price units. ``None`` disables the check.
    max_spread: Decimal | None = None
    #: Refuse a new trade at or above this many open positions. ``None`` disables.
    max_open_positions: int | None = None

    def __post_init__(self) -> None:
        if self.risk_percent <= 0:
            raise ValueError(f"risk_percent must be positive, got {self.risk_percent}")
        if self.risk_percent > 100:
            raise ValueError(
                f"risk_percent cannot exceed 100, got {self.risk_percent}; a value above 100 "
                "is a fraction of the account being risked more than once over"
            )
        if self.reward_risk_ratio <= 0:
            raise ValueError(f"reward_risk_ratio must be positive, got {self.reward_risk_ratio}")
        if self.minimum_reward_risk_ratio is not None and self.minimum_reward_risk_ratio <= 0:
            # Zero would refuse every target, which is what an absent floor means
            # to say. The distinction is worth a validation error: a floor of
            # zero and no floor at all look identical in a configuration file
            # that was meant to set one.
            raise ValueError(
                f"minimum_reward_risk_ratio must be positive when set, got "
                f"{self.minimum_reward_risk_ratio}; omit it entirely to disable the check"
            )
        if self.max_spread is not None and self.max_spread <= 0:
            raise ValueError(f"max_spread must be positive when set, got {self.max_spread}")
        if self.max_open_positions is not None and self.max_open_positions < 0:
            raise ValueError(
                f"max_open_positions cannot be negative, got {self.max_open_positions}"
            )
        if self.minimum_evidence_score is not None and not (
            0.0 <= self.minimum_evidence_score <= 1.0
        ):
            raise ValueError(
                f"minimum_evidence_score must be between 0 and 1, got {self.minimum_evidence_score}"
            )
        if not self.allow_buy and not self.allow_sell:
            raise ValueError(
                "both allow_buy and allow_sell are false, which would refuse every signal"
            )

    def risk_amount(self, balance: Decimal) -> Decimal:
        """The maximum planned loss, in account currency.

        ``balance * risk_percent / 100``. The division by 100 is what makes
        ``risk_percent = 0.5`` mean half a percent rather than half the account.
        """
        if balance <= 0:
            raise ValueError(f"balance must be positive, got {balance}")
        return balance * self.risk_percent / Decimal(100)

    def reward_amount(self, balance: Decimal) -> Decimal:
        """The planned gain, in account currency, at the configured ratio."""
        return self.risk_amount(balance) * self.reward_risk_ratio

    def symbol_allowed(self, symbol: str) -> bool:
        """Whether this parameter set permits a symbol.

        An empty allowlist means unrestricted *by the bridge*. The execution
        project has its own allowlist and applies it independently, so a signal
        has to satisfy both; the bridge not having an opinion is not the same as
        the trade being permitted.
        """
        if not self.allowed_symbols:
            return True
        return symbol.strip().upper() in self.allowed_symbols

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk_percent": str(self.risk_percent),
            "reward_risk_ratio": str(self.reward_risk_ratio),
            "minimum_reward_risk_ratio": (
                str(self.minimum_reward_risk_ratio)
                if self.minimum_reward_risk_ratio is not None
                else None
            ),
            "take_profit_source": self.take_profit_source.value,
            "allow_volatility_fallback_stop": self.allow_volatility_fallback_stop,
            "minimum_evidence_score": self.minimum_evidence_score,
            "allow_buy": self.allow_buy,
            "allow_sell": self.allow_sell,
            "allowed_symbols": sorted(self.allowed_symbols),
            "max_spread": str(self.max_spread) if self.max_spread is not None else None,
            "max_open_positions": self.max_open_positions,
        }


#: The longest pre-submit pause the bridge will accept as configuration, in
#: milliseconds. One hour: a sanity bound against a unit mistake (seconds typed
#: as milliseconds), not a recommended value. Anything above it is refused at
#: construction rather than slept through.
MAX_PRE_SUBMIT_DELAY_MS = 3_600_000


@dataclass(frozen=True, slots=True)
class PreSubmitDelay:
    """A bounded random pause before the final order-submission click.

    UI pacing for the submission workflow, nothing more: after the order is
    fully prepared and validated, the executor waits a freshly rolled duration
    before handing the order to the submission workflow. It does not change the
    trading decision, the prices, or the volume -- only when the handoff
    happens.

    It is deliberately **not** a mechanism for evading broker or platform
    automation controls: no mouse movement, no fake input, no timing anywhere
    else. Just one configurable pause, in one place, that is logged when taken.

    Disabled by default, so a fresh checkout behaves exactly as before.
    """

    #: Master switch. ``False`` means no pause is ever taken.
    enabled: bool = False
    #: Inclusive lower bound of the rolled pause, in milliseconds.
    min_ms: int = 1000
    #: Inclusive upper bound of the rolled pause, in milliseconds.
    max_ms: int = 5000

    def __post_init__(self) -> None:
        for name in ("min_ms", "max_ms"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{name} must be an integer number of milliseconds, got {value!r}")
        if self.min_ms < 0:
            raise ValueError(f"min_ms cannot be negative, got {self.min_ms}")
        if self.max_ms < self.min_ms:
            raise ValueError(
                f"max_ms ({self.max_ms}) cannot be below min_ms ({self.min_ms}); "
                "a range with no values in it would make every submission wait forever"
            )
        if self.max_ms > MAX_PRE_SUBMIT_DELAY_MS:
            raise ValueError(
                f"max_ms ({self.max_ms}) exceeds the sanity bound of "
                f"{MAX_PRE_SUBMIT_DELAY_MS} ms (one hour); check for a seconds-versus-"
                "milliseconds mistake"
            )

    def roll_ms(self, rng: random.Random) -> int | None:
        """A fresh pause duration in milliseconds, or ``None`` when disabled.

        Inclusive on both ends: ``randint(min_ms, max_ms)``. A plain PRNG is
        used rather than a cryptographic one because this is UI pacing, not
        security -- unpredictability beyond a uniform draw buys nothing here.
        The generator is injected rather than global so a test can seed it and
        get a reproducible draw.
        """
        if not self.enabled:
            return None
        return rng.randint(self.min_ms, self.max_ms)

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "min_ms": self.min_ms,
            "max_ms": self.max_ms,
        }


@dataclass(frozen=True, slots=True)
class RiskBudget:
    """How much money this trade is allowed to lose, and where that came from.

    A separate type rather than a bare ``Decimal``, because the number on its own
    is not auditable. "Risk $50" cannot be checked against anything; "0.5% of a
    $10,000 balance, targeting $50" can be recomputed by hand, and it is that
    recomputation which is the only thing standing between a misconfigured
    percentage and a system nobody chose.

    Every field is retained for the same reason :class:`PositionSize` retains
    every input. A decision record that cannot be re-derived is an assertion
    rather than a record.
    """

    #: The maximum planned loss, in the account currency.
    amount: Decimal
    #: The balance the amount was taken from. Always the *balance*, never the
    #: equity -- see :attr:`AccountBalance.effective_balance`.
    balance: Decimal
    #: The configured percentage, e.g. ``0.5`` for half a percent.
    risk_percent: Decimal
    #: The account currency ``amount`` is denominated in. Carried because a tick
    #: value in a different currency would make the division meaningless, and
    #: because the refusal that catches that has to be able to name both.
    currency: str
    #: The planned gain at the configured ratio. Recorded rather than recomputed
    #: later, because the *achieved* ratio can differ from the configured one and
    #: a log reporting the configured number would report an intention as a fact.
    #: **Derived, and checked against the ratio below** -- it is what
    #: ``amount * reward_risk_ratio`` produces, so a value that does not satisfy
    #: that is a mis-wiring rather than a different intent.
    reward_amount: Decimal
    #: The ratio ``reward_amount`` was derived from.
    reward_risk_ratio: Decimal

    def __post_init__(self) -> None:
        _positive(self.amount, "risk amount")
        _positive(self.balance, "balance")
        _positive(self.risk_percent, "risk_percent")
        if not self.currency.strip():
            raise ValueError("currency is required")
        _positive(self.reward_amount, "reward amount")
        _positive(self.reward_risk_ratio, "reward_risk_ratio")
        expected = self.amount * self.reward_risk_ratio
        if self.reward_amount != expected:
            # Not cosmetic. Nothing consumes `reward_amount` today -- the sizer
            # needs only `amount` -- so an inconsistent pair would pass every test
            # in the suite while the decision log advertised a planned gain the
            # bridge never aimed for. Checked here rather than left to the call
            # site because this model exists precisely so that it cannot be
            # constructed wrong, and "derived, therefore derivable" is the kind of
            # claim that rots the moment someone adds a field next to it.
            raise ValueError(
                f"reward_amount {self.reward_amount} does not match amount "
                f"{self.amount} at a ratio of {self.reward_risk_ratio}, which gives "
                f"{expected}. The planned gain is derived from the budget, not chosen "
                f"separately."
            )

    @property
    def fraction_of_balance(self) -> Decimal:
        """The amount as a fraction of the balance, i.e. ``risk_percent / 100``.

        Exposed because it is the number a reviewer checks first: a budget that
        claims to be half a percent of the balance and is not is a configuration
        error that no other field would reveal.
        """
        return self.amount / self.balance

    def to_dict(self) -> dict[str, Any]:
        return {
            "amount": str(self.amount),
            "balance": str(self.balance),
            "risk_percent": str(self.risk_percent),
            "currency": self.currency,
            "reward_amount": str(self.reward_amount),
            "reward_risk_ratio": str(self.reward_risk_ratio),
        }


@dataclass(frozen=True, slots=True)
class PositionSize:
    """A computed volume, with the arithmetic that produced it.

    Every input is retained. A volume on its own is not auditable: "0.37 lots"
    cannot be checked against anything, while the balance, risk, stop distance,
    tick size and tick value that produced it can be recomputed by hand.
    """

    volume: Decimal
    #: The volume before rounding to the broker's step.
    raw_volume: Decimal
    risk_amount: Decimal
    stop_distance: Decimal
    risk_per_unit: Decimal
    #: ``stop_distance / tick_size``. Retained because "0.003 / 0.00001 = 300
    #: ticks" is the step a reviewer wants to see, and re-deriving it from two
    #: rounded decimals is how a unit error hides.
    ticks: Decimal
    tick_size: Decimal
    tick_value: Decimal
    #: True when the volume was raised to reach the broker's minimum. Always
    #: ``False`` by design: a volume below the minimum is a refusal, and a sizing
    #: result that could express a floor-up would eventually be used by someone
    #: who did not know that.
    clamped_to_minimum: bool = False
    #: True when rounding to the step changed the volume.
    rounded: bool = False
    #: True when the volume exceeded the broker's maximum and was clamped down,
    #: which means the real risk is *below* the budget rather than at it.
    clamped_to_maximum: bool = False

    def __post_init__(self) -> None:
        _positive(self.volume, "volume")
        if self.raw_volume <= 0:
            raise ValueError(f"raw_volume must be positive, got {self.raw_volume}")
        if self.risk_per_unit <= 0:
            raise ValueError(f"risk_per_unit must be positive, got {self.risk_per_unit}")
        if self.clamped_to_minimum:
            # Not a defensive check against a future bug. It is the loudest
            # possible statement of a rule this project refuses to break.
            raise ValueError(
                "clamped_to_minimum cannot be set: a volume below the broker minimum is a "
                "refusal, never a floor-up, because flooring up risks more than the budget "
                "allows by an unbounded amount"
            )

    @property
    def planned_loss(self) -> Decimal:
        """Money actually at risk with this volume, after rounding.

        Almost never exactly ``risk_amount``: rounding down leaves it fractionally
        under, and clamping down leaves it further under. Exposed so a decision
        log can state the real number rather than the intended one.
        """
        return self.risk_per_unit * self.volume

    @property
    def is_within_budget(self) -> bool:
        """Whether the planned loss is at or under the intended risk amount.

        False only when rounding or clamping pushed it over, which rounding-down
        and clamping-down should make impossible. A test asserts that, because a
        sizer that can exceed its own budget is the failure this project exists to
        prevent.
        """
        return self.planned_loss <= self.risk_amount

    def to_dict(self) -> dict[str, Any]:
        return {
            "volume": str(self.volume),
            "raw_volume": str(self.raw_volume),
            "risk_amount": str(self.risk_amount),
            "planned_loss": str(self.planned_loss),
            "stop_distance": str(self.stop_distance),
            "risk_per_unit": str(self.risk_per_unit),
            "ticks": str(self.ticks),
            "tick_size": str(self.tick_size),
            "tick_value": str(self.tick_value),
            "clamped_to_minimum": self.clamped_to_minimum,
            "clamped_to_maximum": self.clamped_to_maximum,
            "rounded": self.rounded,
            "is_within_budget": self.is_within_budget,
        }


def canonical_ratio(value: Decimal) -> Decimal:
    """One ratio in one representation.

    ``Decimal`` carries its own exponent, and arithmetic leaves it wherever it
    lands: ``Decimal("1") / Decimal("1")`` is ``1`` while
    ``Decimal("1.0") / Decimal("1")`` is ``1.0``. Both are the same ratio, and
    before Phase 5 the bridge reported whichever one the arithmetic happened to
    produce -- so a decision record said ``"1"`` for a ratio-derived target and
    ``"1.0"`` for the identical ratio derived from a signal's target, and a log
    query for one of them missed the other.

    Normalisation alone is not enough, and the reason matters: ``Decimal("100")
    .normalize()`` is ``1E+2``, so a 100:1 configuration would have been written
    to the log in exponent notation. Any positive exponent is therefore quantised
    back to a plain integer before formatting.

    Only the representation changes. Numeric equality is unaffected, so this is
    safe to apply to a value someone is about to do arithmetic on.
    """
    normalised = value.normalize()
    exponent = normalised.as_tuple().exponent
    if isinstance(exponent, int) and exponent > 0:
        return normalised.quantize(Decimal(1))
    return normalised


def achieved_ratio(stop: StopLoss, take_profit: TakeProfit | None) -> Decimal | None:
    """The reward:risk a trade *actually* has, from the distances used.

    One implementation, shared. Phase 5 replaced four inline copies in
    :mod:`take_profit` and the property below with a call to this, because five
    unshared copies of the same division is five places for one of them to be
    edited and the other four to be quietly wrong -- and a decision log reporting
    a different ratio from the one a policy checked is exactly the kind of
    disagreement this project refuses to have between two of its own layers.

    It lives here rather than in ``take_profit`` because :class:`TradeIntent` needs
    it and ``take_profit`` imports this module; putting it there would be a cycle.

    ``None`` when there is no take profit, which is a real configuration
    (``TakeProfitSource.NONE``) rather than a missing value. Returning ``None``
    rather than raising keeps "the trader deliberately disabled targets"
    distinguishable from "the stop distance was zero", which would be a bug.
    ``StopLoss`` validates a positive distance at construction, so the only way to
    divide by zero here is an object that skipped it.

    Not rounded, and not truncated. A 1:1 target at a 0.00300 stop divides to
    exactly 1, and one at 0.00301 divides to something with a long tail; both are
    the honest figure, and rounding a ratio for readability is how a 0.9:1 becomes
    a 1:1 in a log. What *is* normalised is the representation -- see
    :func:`canonical_ratio` -- so the same ratio never appears as both ``1`` and
    ``1.0`` depending on which arithmetic produced it.
    """
    if take_profit is None or stop.distance <= 0:
        return None
    return canonical_ratio(take_profit.distance / stop.distance)


@dataclass(frozen=True, slots=True)
class TradeIntent:
    """A signal that has survived validation, sizing and risk, ready to execute.

    Everything needed to place the order, and nothing that is not. Its existence
    means the trade passed every check; the decision record is what says so
    formally.

    ``take_profit`` is optional because ``TakeProfitSource.NONE`` is a supported,
    documented, tested configuration -- exits managed elsewhere. It was mandatory
    until Phase 5, which made this class **unconstructible** under that policy: the
    take-profit resolver correctly returns ``None`` for it, so whoever wrote
    Phase 6 would have hit the mismatch and, plausibly, resolved it by refusing
    the trades an operator had deliberately enabled. A model that cannot represent
    a supported configuration is a bug that waits to be misdiagnosed.
    """

    signal: Signal
    symbol: str
    direction: Direction
    entry: Decimal
    stop_loss: StopLoss
    take_profit: TakeProfit | None
    position_size: PositionSize
    risk_parameters: RiskParameters
    account_balance: AccountBalance
    symbol_spec: SymbolSpec

    @property
    def volume(self) -> Decimal:
        return self.position_size.volume

    @property
    def risk_amount(self) -> Decimal:
        return self.position_size.risk_amount

    @property
    def reward_to_risk(self) -> Decimal | None:
        """Achieved reward:risk, from the distances actually used.

        Computed rather than copied from the configured ratio, because the
        achieved ratio can differ from the intended one -- a signal-supplied
        target used under the fallback policy does not produce exactly 1:1. A
        decision log that reported the configured ratio instead of the achieved
        one would be reporting an intention as a fact.

        ``None`` when the configuration has no take profit at all, which is a
        successful outcome and not a failure. See :func:`achieved_ratio`.
        """
        return achieved_ratio(self.stop_loss, self.take_profit)

    def to_dict(self) -> dict[str, Any]:
        ratio = self.reward_to_risk
        return {
            "signal_id": self.signal.signal_id,
            "symbol": self.symbol,
            "direction": self.direction.value,
            "entry": str(self.entry),
            "stop_loss": self.stop_loss.to_dict(),
            "take_profit": self.take_profit.to_dict() if self.take_profit is not None else None,
            "position_size": self.position_size.to_dict(),
            "risk_parameters": self.risk_parameters.to_dict(),
            "account_balance": self.account_balance.to_dict(),
            "symbol_spec": self.symbol_spec.to_dict(),
            "reward_to_risk": str(ratio) if ratio is not None else None,
            "has_take_profit": self.take_profit is not None,
        }


@dataclass(frozen=True, slots=True)
class ExecutionRequest:
    """What the bridge asks the execution layer to do.

    A narrow, explicit boundary type rather than the intent itself, so the
    executor cannot reach back into the sizing arithmetic and second-guess it.

    ``take_profit`` is optional, and it was not always. It was required until
    Phase 9, which made this class **unconstructible** under
    ``TakeProfitSource.NONE`` -- the same bug Phase 5 had already found and fixed
    one class higher up. ``TradeIntent.take_profit`` is optional and
    ``auto-trade``'s own ``TradeSignal.take_profit`` is optional, so a required
    field here was a third opinion nobody had asked for.

    Phase 8 found the consequence: an operator who deliberately manages exits
    elsewhere got every trade refused at the very last step, with a message about
    a model rather than about exits. **A model that cannot represent a supported
    configuration is a bug that waits to be misdiagnosed**, and it had waited two
    phases.
    """

    signal_id: str
    symbol: str
    direction: Direction
    volume: Decimal
    entry: Decimal
    stop_loss: Decimal
    take_profit: Decimal | None = None
    comment: str = ""
    #: The upstream candidate identifier, forwarded so the execution project's
    #: audit log can name the detector that caused the trade.
    strategy: str = ""
    #: Carried for traceability. Explicitly **not** a confidence score: the
    #: upstream engine's evidence score is not a probability, and storing it in a
    #: field the downstream project calls "confidence" without saying so would
    #: invite exactly that misreading.
    evidence_score: float | None = None
    #: The full upstream decision, verbatim. The execution project's audit log
    #: then keeps the provenance without the bridge needing a second audit store.
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.signal_id.strip():
            raise ValueError("signal_id is required")
        if not self.symbol.strip():
            raise ValueError("symbol is required")
        if not self.direction.is_tradable:
            raise ValueError(f"direction {self.direction.value} is not tradable")
        _positive(self.volume, "volume")
        _positive(self.entry, "entry")
        _positive(self.stop_loss, "stop_loss")
        if self.take_profit is not None:
            # Absent is allowed -- exits managed elsewhere is a supported policy --
            # but a take profit that is *present* and non-positive is not a target,
            # and it is refused here rather than passed to a broker that would.
            _positive(self.take_profit, "take_profit")

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "symbol": self.symbol,
            "direction": self.direction.value,
            "volume": str(self.volume),
            "entry": str(self.entry),
            "stop_loss": str(self.stop_loss),
            "take_profit": None if self.take_profit is None else str(self.take_profit),
            "comment": self.comment,
            "strategy": self.strategy,
            "evidence_score": self.evidence_score,
            "metadata": self.metadata,
        }


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """What the execution layer reported.

    The ``UNKNOWN`` status is modelled explicitly and cannot be collapsed into
    success or failure. When a final control may have been used, the outcome is
    unknown, and an unknown outcome is **not** a failure to be retried -- a retry
    may open a second position. That distinction is the reason the upstream
    project reports it separately, and the reason it survives here.

    ``status`` is a plain string rather than an enum member of this package,
    because the vocabulary belongs to the execution project. An adapter maps it
    and this class validates that the value is one this bridge knows how to act
    on -- an unrecognised status is treated as ``UNKNOWN``, which is the safe
    reading of an answer nobody understands.
    """

    STATUS_ACCEPTED = "ACCEPTED"
    STATUS_REQUESTED = "REQUESTED"
    STATUS_REJECTED = "REJECTED"
    STATUS_UNKNOWN = "UNKNOWN"
    STATUS_DRY_RUN = "DRY_RUN"

    #: Every status the bridge knows how to act on. Anything else is normalised
    #: to ``UNKNOWN`` at construction, because guessing that an unrecognised
    #: answer meant "rejected" could cause a retry that opens a second position.
    KNOWN_STATUSES = frozenset(
        {STATUS_ACCEPTED, STATUS_REQUESTED, STATUS_REJECTED, STATUS_UNKNOWN, STATUS_DRY_RUN}
    )

    signal_id: str
    status: str
    message: str = ""
    #: The position ticket, when the execution layer could identify one. Named
    #: ``position_id`` rather than ``order_id`` because that is what the upstream
    #: project actually reports, and calling it an order id would be a claim it
    #: does not support.
    position_id: str | None = None
    executed_price: Decimal | None = None
    requested_price: Decimal | None = None
    error: str | None = None
    #: What the execution layer observed to reach its conclusion. Free-form,
    #: because its shape belongs to the execution project.
    evidence: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.signal_id.strip():
            raise ValueError("signal_id is required")
        if not self.status.strip():
            raise ValueError("status is required")
        if self.status not in self.KNOWN_STATUSES:
            # Not an exception. An execution layer that invents a status is a
            # real possibility the bridge has to survive, and raising here would
            # turn a survivable surprise into a lost trade record. An unrecognised
            # answer is recorded as unknown and escalated.
            object.__setattr__(self, "status", self.STATUS_UNKNOWN)
            original = self.status
            object.__setattr__(
                self,
                "message",
                f"{self.message} (unrecognised execution status {original!r}; "
                "recorded as UNKNOWN and not retried)".strip(),
            )

    @property
    def is_accepted(self) -> bool:
        """Whether a position was independently observed to exist."""
        return self.status == self.STATUS_ACCEPTED

    @property
    def is_unknown(self) -> bool:
        """Whether the outcome could not be determined.

        Checked before any retry decision. A result in this state must be
        escalated, not re-sent.
        """
        return self.status == self.STATUS_UNKNOWN

    @property
    def is_rejected(self) -> bool:
        """Whether the order was definitively refused and not placed."""
        return self.status == self.STATUS_REJECTED

    @property
    def is_dry_run(self) -> bool:
        """Whether the request was validated and deliberately not sent."""
        return self.status == self.STATUS_DRY_RUN

    @property
    def is_retryable(self) -> bool:
        """Whether re-sending this request is safe.

        Only a clean rejection. ``UNKNOWN`` is not retryable and ``REQUESTED`` is
        not either, because in both cases a control may have been used.
        """
        return self.is_rejected

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "status": self.status,
            "message": self.message,
            "position_id": self.position_id,
            "executed_price": str(self.executed_price) if self.executed_price is not None else None,
            "requested_price": (
                str(self.requested_price) if self.requested_price is not None else None
            ),
            "error": self.error,
            "evidence": self.evidence,
        }


@dataclass(frozen=True, slots=True)
class TradeDecision:
    """The bridge's answer, either way.

    There is no "no result" path. Every signal produces a decision with an action
    and a reason, because a system that silently does nothing is
    indistinguishable from one that is broken.

    The rejection path is the one that matters most, so it carries the reason
    code, a human-readable explanation, and whatever partial arithmetic was
    completed before the refusal. A trader looking at a refusal wants to know
    *which* check failed and *why*, and "no trade" alone answers neither.
    """

    signal_id: str
    action: DecisionAction
    reason: str = ""
    explanation: str = ""
    intent: TradeIntent | None = None
    execution: ExecutionResult | None = None
    decided_at: datetime = field(default_factory=utc_now)
    #: Everything the pipeline computed before deciding, whether or not it acted.
    #: A refused trade still has a stop distance and often a raw volume, and
    #: recording them is what makes a refusal debuggable.
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.signal_id.strip():
            raise ValueError("signal_id is required")
        if self.action is not DecisionAction.NO_TRADE and self.intent is None:
            raise ValueError(
                f"action {self.action.value} requires an intent; only NO_TRADE may omit it"
            )
        if self.action is DecisionAction.NO_TRADE and not self.reason.strip():
            # A refusal without a reason is the one thing this project must never
            # produce. It is not a warning, it is a construction error: a
            # NO_TRADE with no reason is indistinguishable from a bug, which is
            # the specific failure mode this whole design exists to prevent.
            raise ValueError("a NO_TRADE decision must carry a reason code")

    @property
    def is_trade(self) -> bool:
        """Whether an order was actually sent.

        ``DRY_RUN`` is not a trade. It is a decision that would have been one.
        Collapsing the two would let a dry run look like a fill in a log.
        """
        return self.action is DecisionAction.EXECUTE

    @property
    def is_no_trade(self) -> bool:
        return self.action is DecisionAction.NO_TRADE

    @property
    def is_dry_run(self) -> bool:
        return self.action is DecisionAction.DRY_RUN

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "action": self.action.value,
            "reason": self.reason,
            "explanation": self.explanation,
            "decided_at": self.decided_at.isoformat(),
            "intent": self.intent.to_dict() if self.intent is not None else None,
            "execution": self.execution.to_dict() if self.execution is not None else None,
            "diagnostics": self.diagnostics,
        }
