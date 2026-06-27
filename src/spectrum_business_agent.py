"""
Spectrum Business AI Sales Agent - Jason calling from Spectrum.

Manages multi-turn conversation for B2B fiber internet, phone services, and
technician appointment scheduling. The public API matches ConversationAgent so
the existing Google Voice/realtime call loop can use this agent without changes.
"""

from __future__ import annotations

import logging
import re
import time

from groq import Groq

logger = logging.getLogger("SpectrumBusinessAgent")

SPECTRUM_OPENING_SCRIPT = (
    "Hi, this is {agent_name} calling from Spectrum Business. "
    "How are you doing today? "
    "Are you the person who handles internet and phone services for the business?"
)

_SYSTEM_TEMPLATE = """\
You are {agent_name}, a professional sales agent calling from Spectrum Business.

Company: Spectrum Business
Service: Pure fiber business internet, business phone, business mobile, Business Connect, Business TV, WiFi, security, and enterprise network solutions
Website: spectrum.com/business
Callback number: {callback_number}

YOUR GOAL: Qualify the business, collect useful follow-up details, and schedule a technician visit within the next two weeks when there is interest.

PHONE CALL RULES:
- You are on a live phone call right now.
- Respond only with what you say aloud. No markdown, labels, or stage directions.
- Do not say you are calling from any company except Spectrum Business.
- First try to reach the owner or the person who manages internet and phone services.
- Keep every reply to 1-2 short spoken sentences.
- Ask one specific question per turn.
- Listen first. Answer what the prospect just said before asking the next question.
- Do not monologue. Do not repeat the full opening pitch after the first turn.
- Lead with the business value that fits their answers: faster internet speeds, more reliable phone services, cost-effective packages, improved performance, mobile lines, Business Connect, Business TV, WiFi, or security.
- Appointment availability is Monday through Friday, 8:00 AM to 4:00 PM, over the next two weeks.
- Never guarantee service, savings, price, or availability. Do not invent coverage details.
- If they request do-not-call or removal, apologize, confirm you will note it, and end politely.

BUSINESS KNOWLEDGE:
- Spectrum Business serves small businesses, medium businesses, enterprise companies, healthcare offices, restaurants, retail stores, hotels, warehouses, transportation companies, call centers, and professional offices.
- Business Internet: up to gig speeds, fiber-powered network, no data caps, no annual contracts, static IP options, 24/7 support, and business-grade reliability.
- Common internet uses: credit card processing, video meetings, cloud software, remote workers, security cameras, and file sharing.
- Business Voice: unlimited calling, voicemail-to-email, call forwarding, caller ID, auto attendant, call transfer, conference calling, and simultaneous ring.
- Business Mobile: 5G access, unlimited talk/text, business plans, multi-line discounts, and no contracts.
- Business Connect: voice, video conferencing, messaging, team collaboration, and file sharing.
- Business TV: useful for sports bars, restaurants, hotels, waiting rooms, and gyms.
- Competitors include AT&T, Comcast Business, Verizon Business, Frontier Communications, Cox Business, and T-Mobile Business.

DISCOVERY QUESTIONS TO ROTATE NATURALLY:
- Are you the owner or the decision maker for internet and phone services?
- Who is your current internet or phone provider?
- Do you experience outages, slow speeds, dropped calls, or WiFi issues?
- How many employees or devices use the internet?
- How many phone lines or mobile lines do you use?
- Are you under contract, and when does it expire?
- Would a technician visit in the next two weeks be useful to review options?

OBJECTION PLAYBOOK:
- "Who is this?" Say you are Jason from Spectrum Business and you are calling because Spectrum recently expanded its pure fiber network in their area.
- "How did you get my number?" Say you are reaching out to local businesses in the area and can note their preference if they do not want future calls.
- "Not interested" Acknowledge briefly, mention one value point, then ask whether a quick technician visit would be worth reviewing.
- "Already have service" Say many businesses compare providers for reliability, speed, and package value, then ask if a quick visit would help.
- "What's the cost?" Say pricing depends on location and service needs, and the technician can review exact options.
- "Busy" Ask for a better time to reach them.
- "Send info" Confirm the best email or contact method, then still offer to schedule the technician visit.
- "Happy with current provider" Acknowledge it, then ask whether reliability, support, or monthly cost would be worth comparing.
- "Under contract" Say many businesses review options before renewal, then ask when the contract ends.
"""

_NEGATIVE_SIGNALS = frozenset(
    [
        "not interested",
        "don't call",
        "remove me",
        "stop calling",
        "do not call",
        "don't need",
        "no thanks",
        "take me off",
        "busy",
        "not a good time",
    ]
)

_ENGAGEMENT_KEYWORDS = frozenset(
    [
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "8 am",
        "9 am",
        "10 am",
        "11 am",
        "12 pm",
        "1 pm",
        "2 pm",
        "3 pm",
        "4 pm",
        "morning",
        "afternoon",
        "next week",
        "this week",
        "internet",
        "phone",
        "service",
        "package",
        "fiber",
        "speed",
        "reliable",
        "cost",
        "price",
        "save",
        "technician",
        "appointment",
        "owner",
        "manager",
    ]
)

_MAX_TURNS_DEFAULT = 18
_MAX_TURNS_ENGAGED = 40
_CONSECUTIVE_NEGATIVES_THRESHOLD = 4


def _clean_spoken_text(text: str) -> str:
    """Remove AI output artifacts that should not be spoken aloud."""
    text = (text or "").strip()
    for prefix in (
        "Jason:",
        "Agent:",
        "Assistant:",
        "[Jason]",
        "[Agent]",
        "JASON:",
        "AGENT:",
        "AI:",
        "Bot:",
        "Spectrum:",
        "[Response]:",
    ):
        if text.lower().startswith(prefix.lower()):
            text = text[len(prefix) :].strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1].strip()
    text = text.replace("**", "").replace("__", "").replace("*", "")
    return re.sub(r"\s+", " ", text).strip()


class SpectrumBusinessAgent:
    def __init__(
        self,
        api_key: str,
        model: str = "llama-3.3-70b-versatile",
        agent_name: str = "Jason",
        callback_number: str = "+15551234567",
        contact_name: str = "",
    ):
        if not api_key:
            raise ValueError("api_key is required for SpectrumBusinessAgent")
        self._client = Groq(api_key=api_key)
        self.model = model
        self.agent_name = agent_name
        self.callback_number = callback_number
        self.contact_name = contact_name
        self._system = self._build_system_prompt()
        self._history: list[dict] = []
        self._turn_count = 0
        self._consecutive_negatives = 0
        self._engaged = False

    def opening_line(self) -> str:
        """Return a short opener so the prospect can answer quickly."""
        return SPECTRUM_OPENING_SCRIPT.format(agent_name=self.agent_name)

    def respond_to(self, prospect_text: str) -> str:
        """Generate a response to what the prospect said."""
        self._history.append({"role": "user", "content": prospect_text})
        self._turn_count += 1

        text_lower = prospect_text.lower()
        if not self._engaged and any(kw in text_lower for kw in _ENGAGEMENT_KEYWORDS):
            self._engaged = True

        if any(signal in text_lower for signal in _NEGATIVE_SIGNALS):
            self._consecutive_negatives += 1
        else:
            self._consecutive_negatives = 0

        quick = self._quick_response(text_lower)
        if quick:
            self._history.append({"role": "assistant", "content": quick})
            return quick

        reply = self._complete()
        self._history.append({"role": "assistant", "content": reply})
        return _clean_spoken_text(reply)

    def should_end_call(self) -> bool:
        max_turns = _MAX_TURNS_ENGAGED if self._engaged else _MAX_TURNS_DEFAULT
        return (
            self._turn_count >= max_turns
            or self._consecutive_negatives >= _CONSECUTIVE_NEGATIVES_THRESHOLD
        )

    def goodbye_line(self) -> str:
        if self._consecutive_negatives >= _CONSECUTIVE_NEGATIVES_THRESHOLD:
            prompt = (
                "Write a brief, respectful goodbye for a Spectrum Business call that did not convert. "
                "Under 20 words. Return only spoken text."
            )
        else:
            prompt = (
                "Write a brief closing for a Spectrum Business call. Confirm the technician visit, "
                "callback, or next step. Under 20 words. Return only spoken text."
            )
        line = self._raw_complete(prompt, max_tokens=50)
        return line if line and len(line) >= 5 else "Thanks for your time. Have a great day."

    def reset(self, contact_name: str = "") -> None:
        self.contact_name = contact_name
        self._history = []
        self._turn_count = 0
        self._consecutive_negatives = 0
        self._engaged = False

    def state_snapshot(self) -> dict:
        return {
            "turn_count": self._turn_count,
            "consecutive_negatives": self._consecutive_negatives,
            "engaged": self._engaged,
            "should_end": self.should_end_call(),
            "history_length": len(self._history),
        }

    def build_stt_prompt(self) -> str:
        parts = ["Spectrum Business internet, phone, mobile, WiFi, and technician appointment sales call"]
        if self.contact_name:
            parts.append(f"business or contact name: {self.contact_name}")
        parts.append(
            "terms: owner, decision maker, current provider, internet services, phone services, pure fiber network, outages, speed, phone lines, mobile lines, contract, technician visit, appointment, Monday through Friday, 8 AM to 4 PM"
        )
        return "; ".join(parts)

    def _quick_response(self, text_lower: str) -> str:
        """Instant replies for common live-call turns to reduce dead air."""
        text = f" {text_lower} "
        if any(x in text for x in (" do not call ", " don't call ", " stop calling ", " remove me ", " take me off ")):
            return "I understand. I will note that preference, and thank you for your time."
        if "not interested" in text or "no thanks" in text or "don't need" in text:
            return "I understand. Before I let you go, is reliability or monthly cost something you would ever want compared?"
        if "busy" in text or "call me back" in text or "not a good time" in text:
            return "No problem. What day and time is better for a quick callback?"
        if "send" in text and ("info" in text or "email" in text or "information" in text):
            return "Absolutely. What is the best email, and who is your current internet provider?"
        if "who is" in text or "what is this" in text or "where are you calling" in text:
            return "This is Jason with Spectrum Business. I am calling because Spectrum expanded pure fiber service in your area."
        if any(x in text for x in (" hello ", " hi ", " hey ")):
            return "Hi, this is Jason with Spectrum Business. Are you the person who handles internet and phone services?"
        if "cost" in text or "price" in text or "expensive" in text or "how much" in text:
            return "Pricing depends on location and service needs. Who are you using now for internet or phone service?"
        if "contract" in text or "under contract" in text:
            return "That is okay. Many businesses review options before renewal; when does your current contract expire?"
        if "already" in text and ("provider" in text or "service" in text or "internet" in text):
            return "That makes sense. Are you happy with the reliability and support, or do you ever have outages?"
        if any(x in text for x in (" yes ", " yeah ", " yep ", " owner ", " manager ", " i am ", " speaking ")):
            return "Great. Who is your current internet or phone provider?"
        if any(x in text for x in (" no ", " nope ", " not me ", " wrong person ")):
            return "No problem. Who would be the right person to speak with about internet and phone services?"
        return ""

    def _build_system_prompt(self) -> str:
        return _SYSTEM_TEMPLATE.format(
            agent_name=self.agent_name,
            callback_number=self.callback_number,
        )

    def _complete(self, user_prompt: str = "") -> str:
        messages = (
            [{"role": "user", "content": user_prompt}]
            if user_prompt
            else [
                {
                    "role": "user",
                    "content": (
                        "Continue the live sales conversation based on the prospect's latest answer. "
                        "Do not repeat the full pitch. Ask one useful discovery or scheduling question. "
                        "Reply in one short spoken sentence unless a second sentence is truly needed."
                    ),
                }
            ]
        )
        full_messages = messages if user_prompt else self._history + messages
        return self._chat_complete_with_backoff(
            model=self.model,
            system=self._system,
            messages=full_messages,
            max_tokens=70,
            temperature=0.45,
        )

    def _raw_complete(self, user_prompt: str, max_tokens: int = 80) -> str:
        return self._chat_complete_with_backoff(
            model=self.model,
            system=self._system,
            messages=[{"role": "user", "content": user_prompt}],
            max_tokens=max_tokens,
            temperature=0.65,
        )

    def _chat_complete_with_backoff(self, **kwargs) -> str:
        system = kwargs.pop("system", None)
        if system:
            messages = list(kwargs.get("messages") or [])
            if not messages or messages[0].get("role") != "system":
                messages.insert(0, {"role": "system", "content": system})
            kwargs["messages"] = messages

        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = self._client.chat.completions.create(**kwargs)
                return _clean_spoken_text(response.choices[0].message.content or "")
            except Exception as exc:
                if attempt < max_retries - 1:
                    wait = 2**attempt
                    logger.warning(
                        "API error (attempt %d/%d): %s. Retrying in %ss...",
                        attempt + 1,
                        max_retries,
                        exc,
                        wait,
                    )
                    time.sleep(wait)
                else:
                    logger.error("Failed after %d attempts: %s", max_retries, exc)
                    return "I'm sorry, I had trouble hearing that. Could you repeat it?"
