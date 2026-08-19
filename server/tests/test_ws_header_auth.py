"""Unit tests for WebSocket header-based authentication.

These tests verify the _extract_ws_token function without requiring a running server.
Run with: uv run pytest tests/test_ws_header_auth.py -v
"""

import pytest


class MockHeaders:
    """Mock for Starlette Headers."""

    def __init__(self, headers: dict[str, str] | None = None):
        self._headers = {k.lower(): v for k, v in (headers or {}).items()}

    def get(self, key: str, default: str = "") -> str:
        return self._headers.get(key.lower(), default)


class MockWebSocket:
    """Mock WebSocket for testing token extraction."""

    def __init__(self, headers: dict[str, str] | None = None):
        self.headers = MockHeaders(headers)


# Import the function under test
from app.routers.v2.websockets import _extract_ws_token


class TestExtractWsToken:
    """Tests for _extract_ws_token function."""

    def test_extract_from_bearer_header(self):
        """Token extracted from Authorization: Bearer header."""
        ws = MockWebSocket({"Authorization": "Bearer my-jwt-token"})
        token = _extract_ws_token(ws, None)
        assert token == "my-jwt-token"

    def test_extract_from_bearer_header_lowercase(self):
        """Token extracted from lowercase 'bearer' header."""
        ws = MockWebSocket({"Authorization": "bearer my-jwt-token"})
        token = _extract_ws_token(ws, None)
        assert token == "my-jwt-token"

    def test_extract_from_bearer_header_mixed_case(self):
        """Token extracted from mixed case 'BeArEr' header."""
        ws = MockWebSocket({"Authorization": "BeArEr my-jwt-token"})
        token = _extract_ws_token(ws, None)
        assert token == "my-jwt-token"

    def test_extract_from_query_param(self):
        """Token extracted from query parameter when no header."""
        ws = MockWebSocket({})
        token = _extract_ws_token(ws, "query-token")
        assert token == "query-token"

    def test_header_takes_precedence_over_query(self):
        """Header token takes precedence over query param."""
        ws = MockWebSocket({"Authorization": "Bearer header-token"})
        token = _extract_ws_token(ws, "query-token")
        assert token == "header-token"

    def test_returns_none_when_no_token(self):
        """Returns None when neither header nor query param provided."""
        ws = MockWebSocket({})
        token = _extract_ws_token(ws, None)
        assert token is None

    def test_returns_none_with_empty_query_param(self):
        """Returns None when query param is empty string."""
        ws = MockWebSocket({})
        token = _extract_ws_token(ws, "")
        assert token is None

    def test_ignores_basic_auth_header(self):
        """Basic auth header is ignored, falls back to query param."""
        ws = MockWebSocket({"Authorization": "Basic dXNlcjpwYXNz"})
        token = _extract_ws_token(ws, "query-token")
        assert token == "query-token"

    def test_ignores_basic_auth_header_no_query(self):
        """Basic auth header is ignored, returns None if no query param."""
        ws = MockWebSocket({"Authorization": "Basic dXNlcjpwYXNz"})
        token = _extract_ws_token(ws, None)
        assert token is None

    def test_empty_bearer_header(self):
        """Empty Bearer header (just 'Bearer ') falls back to query."""
        ws = MockWebSocket({"Authorization": "Bearer "})
        token = _extract_ws_token(ws, "query-token")
        assert token == "query-token"

    def test_empty_bearer_header_no_query(self):
        """Empty Bearer header with no query returns None."""
        ws = MockWebSocket({"Authorization": "Bearer "})
        token = _extract_ws_token(ws, None)
        assert token is None

    def test_whitespace_only_bearer_header(self):
        """Bearer header with only whitespace falls back to query."""
        ws = MockWebSocket({"Authorization": "Bearer    "})
        token = _extract_ws_token(ws, "query-token")
        assert token == "query-token"

    def test_strips_whitespace_from_token(self):
        """Whitespace around token in header is stripped."""
        ws = MockWebSocket({"Authorization": "Bearer   my-token   "})
        token = _extract_ws_token(ws, None)
        assert token == "my-token"

    def test_authorization_header_case_insensitive(self):
        """Authorization header name is case-insensitive."""
        ws = MockWebSocket({"AUTHORIZATION": "Bearer my-token"})
        token = _extract_ws_token(ws, None)
        assert token == "my-token"

    def test_complex_jwt_token(self):
        """Complex JWT token with dots and special chars extracted correctly."""
        jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
        ws = MockWebSocket({"Authorization": f"Bearer {jwt}"})
        token = _extract_ws_token(ws, None)
        assert token == jwt


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
