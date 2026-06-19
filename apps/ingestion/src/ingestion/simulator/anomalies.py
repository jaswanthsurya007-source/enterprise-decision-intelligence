"""Injectable anomaly profiles with ground-truth labelling.

Four profiles, matching architecture §5.1:

* ``spike``  — a sharp positive multiplier on the target metric for a short window.
* ``drop``   — a sharp negative multiplier (revenue collapse) for a window.
* ``drift``  — a slow ramp away from baseline that accumulates over the window.
* ``outage`` — an operational failure: latency_p95 and error_rate blow out while
  the dependent revenue falls (this is the mechanism behind ``revenue_drop_emea``).

An :class:`AnomalyState` is a *declarative* effect description: which day-range,
region, channel, and service it touches, and the multipliers/targets it applies.
The generator asks each active anomaly for its effect on a given ``(day, region,
channel)`` cell (:meth:`AnomalyState.effect`) and stamps ``anomaly_label`` on the
records it produced. Anomalies never block or drop records — they only reshape
the value and add a ground-truth label, exactly as detection eval expects.

Everything here is pure data + arithmetic (no RNG, no IO) so anomaly correctness
is unit-testable in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

AnomalyProfile = Literal["spike", "drop", "drift", "outage"]

PROFILES: tuple[AnomalyProfile, ...] = ("spike", "drop", "drift", "outage")


@dataclass(frozen=True)
class AnomalyEffect:
    """The computed effect of an anomaly on one ``(day, region, channel)`` cell.

    ``revenue_mult`` scales that cell's revenue; ``latency_mult`` /
    ``error_rate_mult`` scale the ops metrics for the matching service; ``label``
    is the ground-truth tag to stamp on affected records (``None`` if the cell is
    untouched). When ``label is None`` all multipliers are 1.0.
    """

    revenue_mult: float = 1.0
    latency_mult: float = 1.0
    error_rate_mult: float = 1.0
    label: str | None = None

    @property
    def active(self) -> bool:
        return self.label is not None


_NO_EFFECT = AnomalyEffect()


@dataclass
class AnomalyState:
    """A declared, deterministic anomaly applied over a contiguous day window.

    Parameters
    ----------
    profile:
        One of :data:`PROFILES`.
    start_day, end_day:
        Inclusive UTC calendar-day bounds of the effect.
    region, channel, service:
        Scope of the effect; ``None`` means "all". For ``outage`` the ``service``
        is the failing operational service (e.g. ``"checkout-api"``).
    magnitude:
        Profile-specific strength. For ``spike``/``drop`` it is the peak revenue
        multiplier delta (e.g. 0.36 for a -36% drop). For ``drift`` it is the
        total fractional revenue change reached by the end of the window. For
        ``outage`` it is the peak revenue-loss fraction.
    latency_peak_mult, error_peak_mult:
        For ``outage``: peak multipliers applied to baseline latency_p95 /
        error_rate (e.g. 7.78 takes ~180ms -> ~1400ms; 22.5 takes ~0.4% -> ~9%).
    label:
        Ground-truth label stamped on affected records (defaults to ``profile``).
    """

    profile: AnomalyProfile
    start_day: date
    end_day: date
    region: str | None = None
    channel: str | None = None
    service: str | None = None
    magnitude: float = 0.5
    latency_peak_mult: float = 1.0
    error_peak_mult: float = 1.0
    label: str | None = None

    def __post_init__(self) -> None:
        if self.label is None:
            self.label = self.profile

    # --- scope / timing helpers ------------------------------------------------

    def _covers_day(self, day: date) -> bool:
        return self.start_day <= day <= self.end_day

    def _matches_cell(self, region: str | None, channel: str | None) -> bool:
        if self.region is not None and region is not None and region != self.region:
            return False
        if self.channel is not None and channel is not None and channel != self.channel:
            return False
        return True

    def _matches_service(self, service: str) -> bool:
        if self.service is None:
            return True
        return service == self.service

    def _progress(self, day: date) -> float:
        """Fraction (0..1] through the window on ``day`` (1.0 on the last day)."""

        span = (self.end_day - self.start_day).days
        if span <= 0:
            return 1.0
        return ((day - self.start_day).days + 1) / (span + 1)

    # --- effects ---------------------------------------------------------------

    def revenue_effect(self, day: datetime, region: str, channel: str) -> AnomalyEffect:
        """Effect on a revenue cell. Returns the no-op effect if out of scope."""

        d = day.date()
        if not self._covers_day(d) or not self._matches_cell(region, channel):
            return _NO_EFFECT

        if self.profile == "spike":
            return AnomalyEffect(revenue_mult=1.0 + self.magnitude, label=self.label)
        if self.profile == "drop":
            return AnomalyEffect(revenue_mult=max(0.0, 1.0 - self.magnitude), label=self.label)
        if self.profile == "drift":
            # Linear ramp: small near the start, full magnitude by the last day.
            delta = self.magnitude * self._progress(d)
            return AnomalyEffect(revenue_mult=max(0.0, 1.0 - delta), label=self.label)
        if self.profile == "outage":
            return AnomalyEffect(
                revenue_mult=max(0.0, 1.0 - self.magnitude),
                label=self.label,
            )
        return _NO_EFFECT

    def ops_effect(self, day: datetime, region: str, service: str) -> AnomalyEffect:
        """Effect on ops metrics (latency/error) for an ``outage``; else no-op."""

        d = day.date()
        if self.profile != "outage":
            return _NO_EFFECT
        if not self._covers_day(d):
            return _NO_EFFECT
        if self.region is not None and region != self.region:
            return _NO_EFFECT
        if not self._matches_service(service):
            return _NO_EFFECT
        return AnomalyEffect(
            latency_mult=self.latency_peak_mult,
            error_rate_mult=self.error_peak_mult,
            label=self.label,
        )


def combine_revenue(effects: list[AnomalyEffect]) -> AnomalyEffect:
    """Combine overlapping revenue effects multiplicatively, keeping a label."""

    mult = 1.0
    label: str | None = None
    for e in effects:
        if e.active:
            mult *= e.revenue_mult
            label = e.label
    return AnomalyEffect(revenue_mult=mult, label=label)


def combine_ops(effects: list[AnomalyEffect]) -> AnomalyEffect:
    """Combine overlapping ops effects multiplicatively, keeping a label."""

    lat = 1.0
    err = 1.0
    label: str | None = None
    for e in effects:
        if e.active:
            lat *= e.latency_mult
            err *= e.error_rate_mult
            label = e.label
    return AnomalyEffect(latency_mult=lat, error_rate_mult=err, label=label)


def make_anomaly(
    profile: AnomalyProfile,
    *,
    start_day: date,
    duration_days: int = 5,
    region: str | None = None,
    channel: str | None = None,
    service: str | None = None,
    magnitude: float | None = None,
    latency_peak_mult: float | None = None,
    error_peak_mult: float | None = None,
    label: str | None = None,
) -> AnomalyState:
    """Build an :class:`AnomalyState` with sensible per-profile defaults.

    ``duration_days`` is inclusive (a 5-day outage spans ``start_day`` .. +4).
    Defaults are tuned so a raw ``inject`` of any profile produces a clearly
    detectable, realistic signal even without explicit magnitudes.
    """

    from datetime import timedelta

    end_day = start_day + timedelta(days=max(0, duration_days - 1))

    defaults_magnitude = {"spike": 0.8, "drop": 0.5, "drift": 0.4, "outage": 0.36}
    mag = defaults_magnitude[profile] if magnitude is None else magnitude

    # Outage ops defaults take ~180ms -> ~1400ms and ~0.4% -> ~9%.
    lat = 7.78 if latency_peak_mult is None else latency_peak_mult
    err = 22.5 if error_peak_mult is None else error_peak_mult

    return AnomalyState(
        profile=profile,
        start_day=start_day,
        end_day=end_day,
        region=region,
        channel=channel,
        service=service,
        magnitude=mag,
        latency_peak_mult=lat,
        error_peak_mult=err,
        label=label or profile,
    )
