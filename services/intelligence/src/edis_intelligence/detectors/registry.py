"""Detector registry — register detectors by name; resolve + run by name.

The runner (X3) drives detection by name so detectors are pluggable: new ones
(IQR, ``ruptures`` changepoint — designed-future) register through the same
factory without touching the dispatch code. The registry stores *factories*
(zero-arg callables returning a fresh detector) so a detector is never shared
mutable state across series/threads, and so per-run tuning can construct its own
instance.

The :data:`DEFAULT_REGISTRY` ships the two MVP detectors (``robust_zscore`` and
``stl_seasonal``). The module-level :func:`register` / :func:`get_detector` /
:func:`list_detectors` operate on it. Everything is pure + import-safe.
"""

from __future__ import annotations

from typing import Callable

from edis_intelligence.detectors.base import Detector
from edis_intelligence.detectors.robust_zscore import RobustZScoreDetector
from edis_intelligence.detectors.stl_seasonal import StlSeasonalDetector

DetectorFactory = Callable[[], Detector]


class DetectorRegistry:
    """A name -> detector-factory map with simple resolution."""

    def __init__(self) -> None:
        self._factories: dict[str, DetectorFactory] = {}

    def register(self, name: str, factory: DetectorFactory, *, replace: bool = False) -> None:
        """Register ``factory`` under ``name`` (raises on duplicate unless ``replace``)."""

        if not replace and name in self._factories:
            raise ValueError(f"detector {name!r} already registered")
        self._factories[name] = factory

    def get(self, name: str) -> Detector:
        """Construct and return a fresh detector registered under ``name``."""

        try:
            factory = self._factories[name]
        except KeyError as exc:
            raise KeyError(f"no detector named {name!r}; known: {sorted(self._factories)}") from exc
        return factory()

    def names(self) -> list[str]:
        """Return the sorted list of registered detector names."""

        return sorted(self._factories)

    def __contains__(self, name: object) -> bool:
        return name in self._factories


#: The process-wide default registry, pre-loaded with the MVP detectors.
DEFAULT_REGISTRY = DetectorRegistry()
DEFAULT_REGISTRY.register("robust_zscore", RobustZScoreDetector)
DEFAULT_REGISTRY.register("stl_seasonal", StlSeasonalDetector)


def register(name: str, factory: DetectorFactory, *, replace: bool = False) -> None:
    """Register a detector factory on the default registry."""

    DEFAULT_REGISTRY.register(name, factory, replace=replace)


def get_detector(name: str) -> Detector:
    """Resolve a fresh detector by name from the default registry."""

    return DEFAULT_REGISTRY.get(name)


def list_detectors() -> list[str]:
    """List detector names registered on the default registry."""

    return DEFAULT_REGISTRY.names()
