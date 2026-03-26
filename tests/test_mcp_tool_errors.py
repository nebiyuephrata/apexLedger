from __future__ import annotations

from ledger.domain.errors import DomainError
from ledger.event_store import OptimisticConcurrencyError
from ledger.mcp.tools import _err


def test_domain_error_is_returned_with_context_and_suggested_action():
    result = _err(
        DomainError(
            "Agent context has not been loaded",
            code="CONTEXT_NOT_LOADED",
            context={"stream_id": "agent-credit-sess-1"},
        )
    )

    assert result["ok"] is False
    assert result["error"]["error_type"] == "DomainError"
    assert result["error"]["context"]["stream_id"] == "agent-credit-sess-1"
    assert result["error"]["suggested_action"] == "load_or_reuse_a_context_ready_session_then_retry"


def test_occ_error_is_returned_with_stream_context():
    result = _err(OptimisticConcurrencyError("loan-APP-1", 3, 4))

    assert result["ok"] is False
    assert result["error"]["error_type"] == "OptimisticConcurrencyError"
    assert result["error"]["expected_version"] == 3
    assert result["error"]["actual_version"] == 4
    assert result["error"]["context"]["stream_id"] == "loan-APP-1"
    assert result["error"]["suggested_action"] == "reload_stream_and_retry"
