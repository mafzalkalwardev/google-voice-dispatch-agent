"""Tests for multi-key Groq pool."""
from unittest.mock import MagicMock, patch

import pytest

from src.groq_pool import (
    GroqAllKeysFailed,
    GroqKeyPool,
    groq_should_failover,
    load_groq_api_keys,
)


def test_load_groq_api_keys_dedupes(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_one")
    monkeypatch.setenv("GROQ_API_KEY_2", "gsk_two")
    monkeypatch.setenv("GROQ_API_KEYS", "gsk_one,gsk_three")
    monkeypatch.delenv("GROQ_API_KEY_3", raising=False)
    keys = load_groq_api_keys()
    assert keys == ["gsk_one", "gsk_two", "gsk_three"]


def test_load_skips_placeholders(monkeypatch):
    for i in range(2, 11):
        monkeypatch.delenv(f"GROQ_API_KEY_{i}", raising=False)
    monkeypatch.delenv("GROQ_API_KEYS", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "your_groq_api_key_here")
    monkeypatch.setenv("GROQ_API_KEY_2", "gsk_real")
    assert load_groq_api_keys() == ["gsk_real"]


def test_groq_should_failover():
    assert groq_should_failover(Exception("Error code: 429 rate limit"))
    assert groq_should_failover(Exception("401 invalid_api_key"))
    assert not groq_should_failover(Exception("connection timeout"))


def test_execute_failover_second_key():
    pool = GroqKeyPool(["gsk_bad", "gsk_good"])
    calls = []

    def fn(client):
        calls.append(client)
        if len(calls) == 1:
            raise Exception("429 rate limit exceeded")
        return "ok"

    with patch.object(pool, "_client_for") as mock_client:
        mock_client.side_effect = [MagicMock(name="c1"), MagicMock(name="c2")]
        assert pool.execute(fn) == "ok"
        assert len(calls) == 2


def test_execute_raises_when_all_fail():
    pool = GroqKeyPool(["gsk_a", "gsk_b"])

    def fn(_client):
        raise Exception("401 invalid_api_key")

    with patch.object(pool, "_client_for", return_value=MagicMock()):
        with pytest.raises(GroqAllKeysFailed):
            pool.execute(fn)


def test_pool_requires_key():
    with pytest.raises(ValueError):
        GroqKeyPool([])
