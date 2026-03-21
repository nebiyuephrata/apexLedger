"""
ledger/domain/errors.py
=======================
Domain-specific exceptions used by aggregates and command handlers.
"""

class DomainError(Exception):
    """Raised when a domain invariant or business rule is violated."""
    pass
