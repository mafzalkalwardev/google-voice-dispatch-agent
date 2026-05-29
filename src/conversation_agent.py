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
from src.dispatcher_intelligence import (
    DispatcherConversationState,
    build_dynamic_system_prompt,
    build_guardrail_reply,
    build_pricing_reply,
    update_state_from_utterance,
)

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
- Never guarantee earnings. Never invent company facts.

OBJECTION PLAYBOOK:
- "Who is this?" -> briefly repeat your name, company, and that you help carriers with dispatch/load support.
- "How did you get my number?" -> say you are calling publicly available carrier/business contacts and can remove them if they prefer.
- "Not interested" -> acknowledge, give one short value point, then ask if they want no further calls.
- "Already have dispatch" -> respect it, ask if they would compare rates or keep a backup dispatcher for tough weeks.
- "What do you charge?" -> explain pricing depends on setup/equipment and offer a quick onboarding call for exact terms.
- "Can you guarantee loads/earnings?" -> never guarantee; say results depend on market, lanes, equipment, and availability.
- "Send info" -> confirm the best email or text number and offer one specific next step.
- "Busy" -> ask for a better time and end quickly.
- "Remove me" or "stop calling" -> apologize, confirm you will mark them do-not-call, and end.
- Hostile or confused prospect -> stay calm, do not argue, end politely."""

_NEGATIVE_SIGNALS = frozenset([
    "not interested", "don't call", "remove me", "stop calling",
    "do not call", "don't need", "already have", "no thanks",
    "busy right now", "not a good time", "take me off",
])

_MAX_TURNS_DEFAULT = 18
_MAX_TURNS_ENGAGED = 40
_CONSECUTIVE_NEGATIVES_THRESHOLD = 3  # raised from 2 — carriers sometimes say no twice, then engage

# Trucking-specific terms that indicate a genuine carrier conversation
_ENGAGEMENT_KEYWORDS = frozenset([
    "dry van", "flatbed", "reefer", "step deck", "hotshot", "box truck",
    "sprinter van", "sprinter", "power only", "car hauler",
    "own authority", "my mc", "mc number", "my authority",
    "deadhead", "tonu", "detention", "drop hook", "drop and hook",
    "loadboard", "load board",
    "factoring", "quick pay",
    "what percent", "your percent", "what do you charge", "what's your fee",
    "preferred lanes", "what lanes",
    "rpm", "rate per mile",
])


def _clean_spoken_text(text: str) -> str:
    """Remove any AI output artifacts that should not be spoken aloud."""
    text = (text or "").strip()
    # Strip common label prefixes the LLM sometimes outputs
    for prefix in (
        "Tony:", "Agent:", "Assistant:", "[Tony]", "[Agent]",
        "TONY:", "AGENT:", "AI:", "Bot:", "Tony (agent):",
        "Dispatch Agent:", "Response:",
    ):
        if text.lower().startswith(prefix.lower()):
            text = text[len(prefix):].strip()
    # Strip surrounding quotes
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1].strip()
    # Strip markdown bold/italic that edge cases produce
    text = text.replace("**", "").replace("__", "").replace("*", "")
    # Collapse whitespace
    import re
    text = re.sub(r"\s+", " ", text).strip()
    return text


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
        self.company_name = company_name
        self.company_context = company_context or "Freight dispatch services for owner-operators."
        self.company_website = company_website or ""
        self.callback_number = callback_number
        self.contact_name = contact_name
        self.state = DispatcherConversationState()
        self._system = self._build_system_prompt()
        self._history: list[dict] = []
        self._turn_count: int = 0
        self._consecutive_negatives: int = 0
        self._engaged: bool = False

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def opening_line(self) -> str:
        """Generate Tony's first spoken greeting — warm and human, not a pitch."""
        # Varied openers to avoid sounding scripted on repeated calls
        prompt = (
            f"Write ONE warm, natural phone greeting for an outbound freight dispatch call. "
            f"You are {self.agent_name} from {self.company_name}. "
            "Style: casual, conversational, friendly — like a real person calling, not a robot. "
            "The greeting must: (1) say hello warmly, (2) state your name and company briefly, "
            "(3) add a casual human touch like 'how are things going?' or 'hope your day's going well'. "
            "Vary the wording each time — do NOT always use the same sentence structure. "
            "Examples of good variety: "
            "'Hey, how are you doing? This is Tony calling from Indus Transports.' "
            "'Hi there — Tony here at Indus Transports, how\'s it going?' "
            "'Hello, this is Tony with Indus Transports — hope I\'m catching you at a good time.' "
            "'Hey, good to connect — Tony from Indus Transports here, how are you today?' "
            "Under 18 words. Return ONLY the greeting text, no quotes, no labels."
        )
        line = self._raw_complete(prompt, max_tokens=55)
        # Fallback if LLM returns empty
        if not line or len(line) < 5:
            import random
            fallbacks = [
                f"Hey, how are you doing? This is {self.agent_name} with {self.company_name}.",
                f"Hi there — {self.agent_name} here from {self.company_name}, how's it going?",
                f"Hello, this is {self.agent_name} calling from {self.company_name} — how are you today?",
            ]
            line = random.choice(fallbacks)
        return line

    def respond_to(self, prospect_text: str) -> str:
        """
        Generate a response to what the prospect said.
        Updates conversation history. Returns the agent's reply.
        """
        self._history.append({"role": "user", "content": prospect_text})
        self._turn_count += 1
        update_state_from_utterance(self.state, prospect_text)

        text_lower = prospect_text.lower()
        if not self._engaged and any(kw in text_lower for kw in _ENGAGEMENT_KEYWORDS):
            self._engaged = True

        if any(signal in text_lower for signal in _NEGATIVE_SIGNALS):
            self._consecutive_negatives += 1
        else:
            self._consecutive_negatives = 0

        reply = (
            build_guardrail_reply(self.state, prospect_text)
            or build_pricing_reply(self.state, prospect_text)
            or self._complete()
        )
        self._history.append({"role": "assistant", "content": reply})
        return reply

    def should_end_call(self) -> bool:
        """True when the agent should politely wrap up the call."""
        max_turns = _MAX_TURNS_ENGAGED if self._engaged else _MAX_TURNS_DEFAULT
        return (
            self.state.interest_level == "DNC"
            or self._consecutive_negatives >= _CONSECUTIVE_NEGATIVES_THRESHOLD
            or self._turn_count > max_turns
        )

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
        self._engaged = False
        self.contact_name = contact_name
        self.state = DispatcherConversationState()
        self._system = self._build_system_prompt()

    def state_snapshot(self) -> dict:
        """Return confirmed conversation state for logs/tests."""
        return {
            "truck_type": self.state.truck_type,
            "interest_level": self.state.interest_level,
            "objections": list(self.state.objections),
            "negotiated_percentage": self.state.negotiated_percentage,
            "local_or_otr": self.state.local_or_otr,
            "preferred_lanes": self.state.preferred_lanes,
            "dispatcher_status": self.state.dispatcher_status,
            "follow_up_status": self.state.follow_up_status,
            "mc_number": self.state.mc_number,
            "dimensions": self.state.dimensions,
            "accessories": self.state.accessories,
            "email": self.state.email,
            "factoring_company": self.state.factoring_company,
            "carrier_style": self.state.carrier_style,
        }

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #

    def _build_system_prompt(self) -> str:
        return build_dynamic_system_prompt(
            agent_name=self.agent_name,
            company_name=self.company_name,
            company_website=self.company_website,
            callback_number=self.callback_number,
            company_context=self.company_context,
            contact_name=self.contact_name,
            state=self.state,
        )

    def _complete(self) -> str:
        self._system = self._build_system_prompt()
        messages = [{"role": "system", "content": self._system}]
        messages.extend(self._history[-30:])   # long-call memory window plus state summary
        # Shorter max_tokens for rushed carriers reduces latency significantly
        if self.state.carrier_style == "rushed":
            max_tokens = 55
        elif self.state.carrier_style in ("skeptical", "neutral"):
            max_tokens = 90
        else:
            max_tokens = 130
        temperature = 0.65 if self.state.carrier_style == "skeptical" else 0.75
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                # top_p for tighter distribution on short answers
                top_p=0.92,
            )
            return _clean_spoken_text(resp.choices[0].message.content)
        except Exception as exc:
            logger.error("LLM completion error: %s", exc)
            return "Sorry, could you say that again?"

    def _raw_complete(self, user_prompt: str, max_tokens: int = 80) -> str:
        self._system = self._build_system_prompt()
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
            return _clean_spoken_text(resp.choices[0].message.content)
        except Exception as exc:
            logger.error("LLM raw completion error: %s", exc)
            return ""
