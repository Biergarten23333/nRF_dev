"""Nominal immutable types for the heading-gauge semantic boundary."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
import math
from types import MappingProxyType
from typing import Any


class TypedCanonicalPayload(ABC):
    """Marker base for payloads that passed a current semantic validator."""

    @abstractmethod
    def to_payload(self) -> dict[str, Any]:
        """Return a detached JSON-compatible payload."""


@dataclass(frozen=True, slots=True, init=False)
class KProtocolRelativeByCoordinate(Mapping[str, float]):
    """Immutable nominal K_PROTOCOL_RELATIVE values in fixed coordinate order."""

    coordinate_order: tuple[str, ...]
    _values: tuple[float, ...]

    def __init__(
        self,
        *,
        coordinate_order: Sequence[str],
        k_protocol_relative_rad_by_coordinate: Mapping[str, float],
    ) -> None:
        order = tuple(coordinate_order)
        if not order or len(set(order)) != len(order):
            raise ValueError("K coordinate order must be nonempty and unique")
        if not isinstance(k_protocol_relative_rad_by_coordinate, Mapping):
            raise TypeError("K_PROTOCOL_RELATIVE mapping required at construction boundary")
        if set(k_protocol_relative_rad_by_coordinate) != set(order):
            raise ValueError("K coordinate set does not match coordinate order")
        values = tuple(float(k_protocol_relative_rad_by_coordinate[name]) for name in order)
        if not all(math.isfinite(value) and -math.pi <= value < math.pi for value in values):
            raise ValueError("K values must be finite canonical radians in [-pi,pi)")
        object.__setattr__(self, "coordinate_order", order)
        object.__setattr__(self, "_values", values)

    def __getitem__(self, coordinate: str) -> float:
        try:
            return self._values[self.coordinate_order.index(coordinate)]
        except ValueError as exc:
            raise KeyError(coordinate) from exc

    def __iter__(self) -> Iterator[str]:
        return iter(self.coordinate_order)

    def __len__(self) -> int:
        return len(self.coordinate_order)

    def as_read_only_mapping(self) -> Mapping[str, float]:
        """Local serialization view; public consumers keep the nominal type."""
        return MappingProxyType(dict(zip(self.coordinate_order, self._values, strict=True)))

    def to_payload(self) -> dict[str, float]:
        return dict(self.as_read_only_mapping())
