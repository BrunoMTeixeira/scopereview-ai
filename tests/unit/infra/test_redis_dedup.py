from unittest.mock import MagicMock, patch

import src.infra.redis_dedup as redis_dedup_module


def _fake_redis_module(client: MagicMock):
    fake = MagicMock()
    fake.from_url.return_value = client
    fake.RedisError = type("RedisError", (Exception,), {})
    return fake


def test_redis_dedup_first_acquire_not_skip():
    mock_client = MagicMock()
    mock_client.set.return_value = True
    fake_redis = _fake_redis_module(mock_client)

    with patch.object(redis_dedup_module, "redis", fake_redis):
        from src.infra.redis_dedup import RedisPipelineDedup

        d = RedisPipelineDedup("redis://localhost:6379/0", ttl_seconds=60, key_prefix="t")
        assert d.should_skip_duplicate(42, "orchestrator") is False
        mock_client.set.assert_called_once()


def test_redis_dedup_second_call_skips():
    mock_client = MagicMock()
    mock_client.set.side_effect = [True, None]
    fake_redis = _fake_redis_module(mock_client)

    with patch.object(redis_dedup_module, "redis", fake_redis):
        from src.infra.redis_dedup import RedisPipelineDedup

        d = RedisPipelineDedup("redis://localhost:6379/0", ttl_seconds=60, key_prefix="t")
        assert d.should_skip_duplicate(7, "a") is False
        assert d.should_skip_duplicate(7, "a") is True


def test_redis_dedup_release_deletes():
    mock_client = MagicMock()
    fake_redis = _fake_redis_module(mock_client)

    with patch.object(redis_dedup_module, "redis", fake_redis):
        from src.infra.redis_dedup import RedisPipelineDedup

        d = RedisPipelineDedup("redis://localhost:6379/0", ttl_seconds=60, key_prefix="t")
        d.release(99, "orchestrator")
        mock_client.delete.assert_called_once()
