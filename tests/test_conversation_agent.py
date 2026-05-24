"""Tests for src/conversation_agent.py — ConversationAgent with mocked Groq client."""

import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture
def mock_groq():
    with patch("src.conversation_agent.Groq") as MockGroq:
        mock_client = MagicMock()
        MockGroq.return_value = mock_client
        yield MockGroq, mock_client


def _make_completion(text: str):
    resp = MagicMock()
    resp.choices[0].message.content = text
    return resp


def test_missing_api_key_raises():
    from src.conversation_agent import ConversationAgent
    with pytest.raises(ValueError, match="api_key"):
        ConversationAgent(api_key="")


def test_opening_line_returns_string(mock_groq):
    _, mock_client = mock_groq
    mock_client.chat.completions.create.return_value = _make_completion(
        "Hi, this is Tony from Indus Transports — do you have a moment?"
    )
    from src.conversation_agent import ConversationAgent
    agent = ConversationAgent(api_key="test_key", contact_name="John")
    line = agent.opening_line()
    assert isinstance(line, str)
    assert len(line) > 0


def test_opening_line_strips_wrapping_quotes(mock_groq):
    _, mock_client = mock_groq
    mock_client.chat.completions.create.return_value = _make_completion('"Hi, Tony from Indus Transports."')
    from src.conversation_agent import ConversationAgent
    agent = ConversationAgent(api_key="test_key")
    assert agent.opening_line() == "Hi, Tony from Indus Transports."


def test_respond_to_returns_string(mock_groq):
    _, mock_client = mock_groq
    mock_client.chat.completions.create.return_value = _make_completion(
        "Great, what equipment type are you running?"
    )
    from src.conversation_agent import ConversationAgent
    agent = ConversationAgent(api_key="test_key")
    reply = agent.respond_to("Tell me more about your service.")
    assert isinstance(reply, str)
    assert len(reply) > 0


def test_respond_to_updates_history(mock_groq):
    _, mock_client = mock_groq
    mock_client.chat.completions.create.return_value = _make_completion("Sure thing!")
    from src.conversation_agent import ConversationAgent
    agent = ConversationAgent(api_key="test_key")
    agent.respond_to("Hello")
    assert len(agent._history) == 2  # user + assistant
    assert agent._history[0]["role"] == "user"
    assert agent._history[1]["role"] == "assistant"


def test_should_end_call_false_initially(mock_groq):
    _, mock_client = mock_groq
    mock_client.chat.completions.create.return_value = _make_completion("ok")
    from src.conversation_agent import ConversationAgent
    agent = ConversationAgent(api_key="test_key")
    assert not agent.should_end_call()


def test_should_end_call_after_three_negatives(mock_groq):
    _, mock_client = mock_groq
    mock_client.chat.completions.create.return_value = _make_completion("I understand.")
    from src.conversation_agent import ConversationAgent
    agent = ConversationAgent(api_key="test_key")
    agent.respond_to("not interested")
    agent.respond_to("don't call me again")
    agent.respond_to("remove me from your list")
    assert agent.should_end_call()


def test_negative_counter_resets_on_positive(mock_groq):
    _, mock_client = mock_groq
    mock_client.chat.completions.create.return_value = _make_completion("ok")
    from src.conversation_agent import ConversationAgent
    agent = ConversationAgent(api_key="test_key")
    agent.respond_to("not interested")
    agent.respond_to("not interested")
    agent.respond_to("Actually, tell me more")
    assert agent._consecutive_negatives == 0


def test_should_end_call_after_max_turns(mock_groq):
    _, mock_client = mock_groq
    mock_client.chat.completions.create.return_value = _make_completion("ok")
    from src.conversation_agent import ConversationAgent
    agent = ConversationAgent(api_key="test_key")
    for _ in range(19):
        agent.respond_to("Tell me more")
    assert agent.should_end_call()


def test_goodbye_line_returns_string(mock_groq):
    _, mock_client = mock_groq
    mock_client.chat.completions.create.return_value = _make_completion(
        "Thanks for your time, have a great day!"
    )
    from src.conversation_agent import ConversationAgent
    agent = ConversationAgent(api_key="test_key")
    goodbye = agent.goodbye_line()
    assert isinstance(goodbye, str)
    assert len(goodbye) > 0


def test_reset_clears_history_and_counters(mock_groq):
    _, mock_client = mock_groq
    mock_client.chat.completions.create.return_value = _make_completion("ok")
    from src.conversation_agent import ConversationAgent
    agent = ConversationAgent(api_key="test_key")
    agent.respond_to("not interested")
    agent.respond_to("stop calling")
    agent.reset(contact_name="NewPerson")
    assert agent._history == []
    assert agent._turn_count == 0
    assert agent._consecutive_negatives == 0
    assert agent.contact_name == "NewPerson"


def test_respond_to_handles_api_error(mock_groq):
    _, mock_client = mock_groq
    mock_client.chat.completions.create.side_effect = Exception("network error")
    from src.conversation_agent import ConversationAgent
    agent = ConversationAgent(api_key="test_key")
    reply = agent.respond_to("Hello")
    assert isinstance(reply, str)
    assert len(reply) > 0  # fallback phrase


def test_history_limited_to_rolling_window(mock_groq):
    _, mock_client = mock_groq
    mock_client.chat.completions.create.return_value = _make_completion("ok")
    from src.conversation_agent import ConversationAgent
    agent = ConversationAgent(api_key="test_key")
    for i in range(15):
        agent.respond_to(f"Message {i}")

    # _complete passes last 20 turns — verify no IndexError or crash
    agent.respond_to("last message")
    assert agent._turn_count == 16
