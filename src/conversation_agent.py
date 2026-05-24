"""
Realtime conversational sales agent — Tony from Indus Transports LLC.

Manages a multi-turn conversation history and generates short, natural responses
optimised for live phone calls (1–2 sentences, under 25 words per turn).

Usage:
    agent = ConversationAgent(api_key="gsk_...", contact_name="John")
    opening = agent.opening_line()
    # ... play opening via TTS ...
    response = agent.respond_to("Who is this?")
    # ... play response ...
    if agent.should_end_call():
        farewell = agent.goodbye_line()
"""

from __future__ import annotations

import logging
from typing import Optional

from groq import Groq

logger = logging.getLogger("GoogleVoiceAgent")

_SYSTEM_TEMPLATE = """\
You are {agent_name}, a professional freight dispatch sales agent at {company_name}.

Company: {company_name}
Website: {company_website}
Callback number: {callback_number}

About us: {company_context}

YOUR GOAL: qualify the carrier and book a 15-minute dispatch onboarding call.

PHONE CALL RULES — follow all of these:
- You are on a live phone call RIGHT NOW. The prospect just answered.
- Respond ONLY with what you say aloud — no stage directions, labels, or markdown.
- Every reply must be 1–2 short sentences (hard limit: 30 words).
- Sound warm, confident, and genuinely helpful — never scripted or pushy.
- Ask ONE specific qualifying question per turn (equipment type, lanes, dispatch situation).
- If they raise an objection, acknowledge briefly, then give ONE concrete value point.
- If they say they're busy: "No problem — when's a better time to reach you?"
- If they seem interested: "Great — are you free this week for a quick 15-minute call?"
- After 3 consecutive negative responses, thank them politely and signal that you'll let them go.
- Never guarantee earnings. Never invent company facts."""

_NEGATIVE_SIGNALS = frozenset([
    "not interested", "don't call", "remove me", "stop calling",
    "do not call", "don't need", "already have", "no thanks",
    "busy right now", "not a good time", "take me off",
])


class ConversationAgent:
    def __init__(
        self,
        api_key: str,
        model: str = "llama-3.3-70b-versatile",
        agent_name: str = "Tony",
        company_name: str = "Indus Transports LLC",
        company_context: str = "",
        company_website: str = "",
        callback_number: str = "+15551234567",
        contact_name: str = "",
    ):
        if not api_key:
            raise ValueError("api_key is required for ConversationAgent")
        self._client = Groq(api_key=api_key)
        self.model = model
        self.agent_name = agent_name
        self.contact_name = contact_name
        self._system = _SYSTEM_TEMPLATE.format(
            agent_name=agent_name,
            company_name=company_name,
            company_context=company_context or "Freight dispatch services for owner-operators.",
            company_website=company_website or "",
            callback_number=callback_number,
        )
        self._history: list[dict] = []
        self._turn_count: int = 0
        self._consecutive_negatives: int = 0

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def opening_line(self) -> str:
        """Generate the agent's first spoken line for this call."""
        name_q = f" Is this {self.contact_name}?" if self.contact_name else ""
        prompt = (
            f"Generate one warm, brief opening line for a cold call. "
            f"Introduce yourself as {self.agent_name} calling about freight dispatch.{name_q} "
            "Under 20 words total."
        )
        return self._raw_complete(prompt, max_tokens=60)

    def respond_to(self, prospect_text: str) -> str:
        """
        Generate a response to what the prospect said.
        Updates conversation history. Returns the agent's reply.
        """
        self._history.append({"role": "user", "content": prospect_text})
        self._turn_count += 1

        text_lower = prospect_text.lower()
        if any(signal in text_lower for signal in _NEGATIVE_SIGNALS):
            self._consecutive_negatives += 1
        else:
            self._consecutive_negatives = 0

        reply = self._complete()
        self._history.append({"role": "assistant", "content": reply})
        return reply

    def should_end_call(self) -> bool:
        """True when the agent should politely wrap up the call."""
        return self._consecutive_negatives >= 3 or self._turn_count > 18

    def goodbye_line(self) -> str:
        """Short, warm closing line."""
        return self._raw_complete(
            "Generate one brief, warm closing line to politely end the call. Under 15 words.",
            max_tokens=40,
        )

    def reset(self, contact_name: str = "") -> None:
        self._history.clear()
        self._turn_count = 0
        self._consecutive_negatives = 0
        self.contact_name = contact_name

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #

    def _complete(self) -> str:
        messages = [{"role": "system", "content": self._system}]
        messages.extend(self._history[-20:])   # rolling 20-turn window
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=80,
                temperature=0.78,
            )
            return resp.choices[0].message.content.strip()
        except Exception as exc:
            logger.error("LLM completion error: %s", exc)
            return "Sorry, could you repeat that?"

    def _raw_complete(self, user_prompt: str, max_tokens: int = 80) -> str:
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self._system},
                    {"role": "user",   "content": user_prompt},
                ],
                max_tokens=max_tokens,
                temperature=0.75,
            )
            return resp.choices[0].message.content.strip()
        except Exception as exc:
            logger.error("LLM raw completion error: %s", exc)
            return ""
