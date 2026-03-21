"""
ledger/domain/errors.py
=======================
Domain-specific exceptions used by aggregates and command handlers.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DomainError(Exception):
    """Raised when a domain invariant or business rule is violated."""

    message: str
    code: str = "DOMAIN_ERROR"
    context: dict = field(default_factory=dict)

    def __str__(self) -> str:
        return self.message
