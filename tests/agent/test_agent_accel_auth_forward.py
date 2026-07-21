"""Agent forwards browser session cookies to authenticated accelerator calls."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.agent.main import SESSION_COOKIE, _accel_headers, _accel_post


def test_accel_headers_uses_session_token():
    headers = _accel_headers("tok-abc")
    assert headers == {"Cookie": f"{SESSION_COOKIE}=tok-abc"}


@pytest.mark.asyncio
async def test_accel_post_forwards_session_token():
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"ok": True}
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("services.agent.main.httpx.AsyncClient", return_value=mock_client) as mock_factory:
        result = await _accel_post("/v1/plan", {"config_path": "x.yaml"}, session_token="sess-1")

    assert result == {"ok": True}
    mock_factory.assert_called_once()
    assert mock_factory.call_args.kwargs["headers"] == {"Cookie": f"{SESSION_COOKIE}=sess-1"}
    mock_client.post.assert_awaited_once()
