from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class ContractCompatibilityError(ValueError):
    """Raised when an artifact requires a contract generation this app cannot read."""


@dataclass(frozen=True, order=True)
class ContractVersion:
    major: int
    minor: int

    @classmethod
    def parse(cls, value: str) -> "ContractVersion":
        try:
            major, minor = str(value).split(".", 1)
            return cls(int(major), int(minor))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid contract version {value!r}; expected major.minor.") from exc

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}"


def require_compatible_contract(
    encountered: str,
    supported: str,
    *,
    artifact_path: str | Path,
    component: str,
) -> None:
    actual = ContractVersion.parse(encountered)
    maximum = ContractVersion.parse(supported)
    if actual.major > maximum.major:
        raise ContractCompatibilityError(
            f"{component} cannot read {artifact_path}: encountered contract {actual}, "
            f"maximum supported contract is {maximum}. Upgrade {component}."
        )

