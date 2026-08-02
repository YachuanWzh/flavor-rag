"""Tests for quality alert webhook (P1 item 3.3)."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.evaluation.quality_alert import (
    send_webhook_alert,
    should_trigger_alert,
    meets_auto_promote,
)


class TestShouldTriggerAlert:
    def test_below_threshold(self):
        assert should_trigger_alert(2) is False

    def test_at_threshold(self):
        assert should_trigger_alert(3) is True

    def test_above_threshold(self):
        assert should_trigger_alert(5) is True

    def test_custom_threshold(self):
        assert should_trigger_alert(2, threshold=2) is True
        assert should_trigger_alert(1, threshold=2) is False


class TestMeetsAutoPromote:
    def test_meets_criteria(self):
        assert meets_auto_promote(3, 0) is True

    def test_insufficient_positive(self):
        assert meets_auto_promote(2, 0) is False

    def test_has_negatives(self):
        assert meets_auto_promote(5, 1) is False


class TestSendWebhookAlert:
    @pytest.mark.asyncio
    async def test_empty_url_returns_false(self):
        result = await send_webhook_alert("", title="test", metrics={})
        assert result is False

    @pytest.mark.asyncio
    async def test_successful_send(self):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.status_code = 200

        with patch("app.evaluation.quality_alert.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await send_webhook_alert(
                "https://example.com/webhook",
                title="质量告警测试",
                metrics={"recall@5": 0.85, "ndcg@5": 0.80},
                failed_gates=[
                    {"metric": "recall@5", "value": 0.65, "operator": ">=", "threshold": 0.75}
                ],
            )
            assert result is True
            mock_client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_failure_returns_false(self):
        with patch("app.evaluation.quality_alert.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=Exception("connection failed"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await send_webhook_alert(
                "https://example.com/webhook",
                title="test",
                metrics={},
            )
            assert result is False
