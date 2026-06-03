"""Tests for GroqAgent — all HTTP calls are mocked."""
import pytest
from unittest.mock import MagicMock, patch

from src.ai_groq import GroqAgent


def _mock_agent(response_text: str = "Hello, this is a test script.") -> tuple[GroqAgent, MagicMock]:
    """Return (agent, mock_client) with completions pre-configured."""
    with patch("src.groq_pool.Groq") as MockGroq:
        mock_response = MagicMock()
        mock_response.choices[0].message.content = response_text
        mock_client = MockGroq.return_value
        mock_client.chat.completions.create.return_value = mock_response
        agent = GroqAgent(api_key="test_key_abc123")
        agent._pool.execute = lambda fn: fn(mock_client)
    return agent, mock_client


# ---- generate_call_script ----

def test_generate_call_script_returns_string():
    agent, _ = _mock_agent("Hi John, calling about dispatch optimization...")
    result = agent.generate_call_script("John Doe", "book appointment")
    assert isinstance(result, str)
    assert len(result) > 0


def test_generate_call_script_passes_contact_name():
    agent, mock_client = _mock_agent()
    agent.generate_call_script("Alice Smith", "schedule a demo")
    call_args = mock_client.chat.completions.create.call_args
    messages = call_args.kwargs["messages"]
    user_msg = next(m["content"] for m in messages if m["role"] == "user")
    assert "Alice Smith" in user_msg


def test_generate_call_script_passes_agent_and_company_context():
    agent, mock_client = _mock_agent()
    agent.generate_call_script(
        "Acme Carrier",
        "book onboarding",
        agent_name="Tony",
        company_name="Indus Transports LLC",
        company_context="48-state dispatch support",
        company_website="https://industransports.online/",
    )
    call_args = mock_client.chat.completions.create.call_args
    messages = call_args.kwargs["messages"]
    user_msg = next(m["content"] for m in messages if m["role"] == "user")
    assert "Tony" in user_msg
    assert "Indus Transports LLC" in user_msg
    assert "48-state dispatch support" in user_msg


def test_generate_call_script_passes_objective():
    agent, mock_client = _mock_agent()
    agent.generate_call_script("Bob", "close the deal today")
    call_args = mock_client.chat.completions.create.call_args
    messages = call_args.kwargs["messages"]
    user_msg = next(m["content"] for m in messages if m["role"] == "user")
    assert "close the deal today" in user_msg


def test_generate_call_script_strips_whitespace():
    agent, _ = _mock_agent("  script with spaces  ")
    result = agent.generate_call_script("Test", "test")
    assert result == "script with spaces"


def test_generate_call_script_uses_correct_model():
    agent, mock_client = _mock_agent()
    agent.model = "llama-3.3-70b-versatile"
    agent.generate_call_script("Test", "test")
    call_args = mock_client.chat.completions.create.call_args
    assert call_args.kwargs["model"] == "llama-3.3-70b-versatile"


# ---- generate_voicemail ----

def test_generate_voicemail_returns_string():
    agent, _ = _mock_agent("Hi, leaving a voicemail for you.")
    result = agent.generate_voicemail("Jane", "dispatch services", "+15559876543")
    assert isinstance(result, str)
    assert len(result) > 0


def test_generate_voicemail_passes_callback():
    agent, mock_client = _mock_agent()
    agent.generate_voicemail("Jane", "dispatch offer", "+15559876543")
    call_args = mock_client.chat.completions.create.call_args
    messages = call_args.kwargs["messages"]
    user_msg = next(m["content"] for m in messages if m["role"] == "user")
    assert "+15559876543" in user_msg


def test_generate_voicemail_passes_agent_and_company():
    agent, mock_client = _mock_agent()
    agent.generate_voicemail(
        "Jane",
        "dispatch offer",
        "+15559876543",
        agent_name="Tony",
        company_name="Indus Transports LLC",
    )
    call_args = mock_client.chat.completions.create.call_args
    messages = call_args.kwargs["messages"]
    user_msg = next(m["content"] for m in messages if m["role"] == "user")
    assert "Tony" in user_msg
    assert "Indus Transports LLC" in user_msg


def test_generate_voicemail_max_tokens_limited():
    agent, mock_client = _mock_agent()
    agent.generate_voicemail("Jane", "offer", "+15551111111")
    call_args = mock_client.chat.completions.create.call_args
    assert call_args.kwargs["max_tokens"] == 180


def test_generate_voicemail_includes_offer():
    agent, mock_client = _mock_agent()
    agent.generate_voicemail("Jane", "10% capacity reduction guarantee", "+15551234567")
    call_args = mock_client.chat.completions.create.call_args
    messages = call_args.kwargs["messages"]
    user_msg = next(m["content"] for m in messages if m["role"] == "user")
    assert "10% capacity reduction guarantee" in user_msg


# ---- system prompts ----

def test_call_script_has_system_prompt():
    agent, mock_client = _mock_agent()
    agent.generate_call_script("Test", "test")
    call_args = mock_client.chat.completions.create.call_args
    messages = call_args.kwargs["messages"]
    sys_msg = next((m for m in messages if m["role"] == "system"), None)
    assert sys_msg is not None
    assert len(sys_msg["content"]) > 0


def test_voicemail_has_system_prompt():
    agent, mock_client = _mock_agent()
    agent.generate_voicemail("Test", "offer", "+15550000000")
    call_args = mock_client.chat.completions.create.call_args
    messages = call_args.kwargs["messages"]
    sys_msg = next((m for m in messages if m["role"] == "system"), None)
    assert sys_msg is not None


# ---- constructor validation ----

def test_missing_api_key_raises(monkeypatch):
    for name in ("GROQ_API_KEY", "GROQ_API_KEYS"):
        monkeypatch.delenv(name, raising=False)
    for i in range(2, 11):
        monkeypatch.delenv(f"GROQ_API_KEY_{i}", raising=False)
    with pytest.raises(ValueError, match="api_key"):
        GroqAgent(api_key="")
