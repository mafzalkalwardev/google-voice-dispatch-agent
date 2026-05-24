import logging

from groq import Groq

logger = logging.getLogger("GoogleVoiceAgent")

_CALL_SCRIPT_SYSTEM = (
    "You are a top-performing but ethical truck dispatch sales agent. Write one natural "
    "spoken connected-call script for audio playback. Sound calm, confident, specific, "
    "and human. The prospect is usually an owner-operator or small carrier. Use this "
    "sales shape: introduce the agent and company, name a real trucking pain point, "
    "connect it to dispatch value, ask one equipment-or-lanes qualifying question, and "
    "offer a low-friction 10-minute onboarding call. Include a soft skepticism bridge "
    "such as respecting that they may already have dispatch covered. Do not use labels, "
    "stage directions, bullets, or quotation marks. Do not guarantee earnings, do not "
    "invent facts, and do not sound pushy."
)

_VOICEMAIL_SYSTEM = (
    "You are a dispatch sales agent leaving a professional voicemail. Keep it under 28 "
    "seconds of speech, roughly 70 words. Include: brief greeting, agent name, company "
    "name, reason for calling, specific value offer, and callback number. Sound warm, "
    "direct, and credible. Do not guarantee earnings."
)


class GroqAgent:
    def __init__(self, api_key: str, model: str = "llama-3.3-70b-versatile"):
        if not api_key:
            raise ValueError("api_key is required for GroqAgent")
        self._client = Groq(api_key=api_key)
        self.model = model

    def _complete(self, system: str, user: str, max_tokens: int = 512) -> str:
        response = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens,
            temperature=0.72,
        )
        text = response.choices[0].message.content.strip()
        if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
            text = text[1:-1].strip()
        return text

    def generate_call_script(
        self,
        contact_name: str,
        objective: str,
        context: str = "",
        tone: str = "professional and conversational",
        agent_name: str = "Tony",
        company_name: str = "Indus Transports LLC",
        company_context: str = "",
        company_website: str = "",
    ) -> str:
        context_block = (
            f"Contact context: {context}." if context else "No extra contact context."
        )
        company_block = (
            f"Company context: {company_context}." if company_context
            else "Use general truck dispatch sales context."
        )
        website_block = f"Website: {company_website}." if company_website else ""
        user_prompt = (
            f"Write the spoken call script for {agent_name} from {company_name} "
            f"calling {contact_name}. Objective: {objective}. Tone: {tone}. "
            f"{company_block} {website_block} {context_block} "
            "Make it useful for an owner-operator or small carrier: mention load "
            "finding, rate negotiation, paperwork support, and 24/7 dispatch only if "
            "supported by the company context. Keep the full spoken script under 95 words. "
            "Make the ask specific: ask what equipment they run or which lanes they prefer."
        )
        return self._complete(_CALL_SCRIPT_SYSTEM, user_prompt, max_tokens=600)

    def generate_voicemail(
        self,
        contact_name: str,
        offer_summary: str,
        callback_number: str,
        agent_name: str = "Tony",
        company_name: str = "Indus Transports LLC",
        company_context: str = "",
    ) -> str:
        user_prompt = (
            f"Leave a voicemail from {agent_name} at {company_name} for {contact_name}. "
            f"The offer: {offer_summary}. "
            f"Company context: {company_context}. "
            f"Callback number: {callback_number}."
        )
        return self._complete(_VOICEMAIL_SYSTEM, user_prompt, max_tokens=180)
