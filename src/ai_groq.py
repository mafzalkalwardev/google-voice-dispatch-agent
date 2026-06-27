import logging
import time

from groq import Groq

logger = logging.getLogger("GoogleVoiceAgent")

_SPECTRUM_CONTEXT = (
    "Spectrum Business recently expanded its pure fiber network in the prospect's area. "
    "The offer is faster business internet speeds, more reliable phone services, "
    "cost-effective packages, and improved overall performance. The goal is to schedule "
    "a technician visit over the next two weeks, Monday through Friday, between 8:00 AM and 4:00 PM."
)

_SPECTRUM_STATIC_SCRIPT = (
    "Hi, this is {agent_name} calling from Spectrum Business. How are you doing today? "
    "May I please speak with the owner or the person who manages your internet and phone services? "
    "The reason for my call is to inform you that Spectrum has recently expanded our pure fiber network in your area. "
    "Because of this, we're now able to offer businesses faster internet speeds, more reliable phone services, "
    "and cost-effective packages with improved overall performance. "
    "What we'd like to do is schedule a quick visit from one of our technicians to install the services "
    "and get everything set up for your business. "
    "We currently have appointments available over the next two weeks, Monday through Friday, between 8:00 AM and 4:00 PM. "
    "What day and time would work best for you?"
)

_SPECTRUM_CALL_SCRIPT_SYSTEM = (
    "You are Jason calling from Spectrum Business. Write one natural spoken outbound "
    "business call script for pure fiber internet and phone services. Follow this sequence: "
    "greeting, ask for the owner or internet/phone decision maker, explain Spectrum expanded "
    "the pure fiber network nearby, mention faster internet speeds, reliable phone services, "
    "cost-effective packages, and improved performance, then ask to schedule a technician "
    "visit over the next two weeks, Monday through Friday, 8:00 AM to 4:00 PM. "
    "Do not use markdown, labels, or stage directions. Do not mention freight, dispatch, "
    "trucks, carriers, lanes, or Indus. Do not guarantee price or availability."
)

_SPECTRUM_VOICEMAIL_SYSTEM = (
    "You are Jason leaving a professional voicemail from Spectrum Business. Keep it under "
    "30 seconds. Mention the pure fiber network expansion, faster business internet, more "
    "reliable phone services, cost-effective packages, and a technician visit available "
    "Monday through Friday, 8:00 AM to 4:00 PM over the next two weeks. Include the callback "
    "number if provided. Do not mention freight, dispatch, trucks, carriers, lanes, or Indus."
)

_CALL_SCRIPT_SYSTEM = (
    "You are a top-performing but ethical freight dispatch sales agent representing a US "
    "dispatch company. Write one natural spoken connected-call script for live audio "
    "playback. Sound calm, confident, specific, and human — not scripted or robotic. "
    "Use this shape: introduce agent and company briefly, name ONE real trucking pain "
    "point relevant to the carrier's equipment type, connect it to dispatch value, ask "
    "one specific qualifying question about their equipment or lanes, and offer a "
    "low-friction 15-minute onboarding call. If equipment type is known, reference "
    "specific freight, RPM ranges, or common challenges for that equipment. Include a "
    "soft skepticism bridge (e.g., acknowledge they may already have dispatch covered). "
    "Do not use labels, stage directions, bullets, or quotation marks. "
    "Do not guarantee earnings, do not invent facts, and do not sound pushy. "
    "Keep the full spoken script under 95 words."
)

_VOICEMAIL_SYSTEM = (
    "You are a freight dispatch sales agent leaving a professional voicemail for an "
    "owner-operator or small carrier. Keep it under 28 seconds of speech, roughly "
    "70 words. Include: brief greeting with agent name and company, specific reason "
    "for calling tied to the carrier's equipment type if known, one concrete value "
    "offer (load finding, rate negotiation, or paperwork), and callback number stated "
    "twice. Sound warm, direct, and credible. Do not guarantee earnings or specific rates."
)

_EQUIPMENT_CONTEXT: dict[str, str] = {
    "Dry Van": (
        "Dry Van carriers typically haul palletized retail, packaged goods, or electronics. "
        "Common pain points: finding consistent reload opportunities, rate compression on "
        "high-volume lanes, and broker paperwork delays. RPM typically $2.00–$3.50."
    ),
    "Reefer": (
        "Reefer carriers haul temperature-sensitive freight like produce, dairy, and pharma. "
        "Pain points: detention on produce loads, reefer fuel costs, finding consistent "
        "backhauls. RPM typically $2.20–$3.80; produce season spikes April–July."
    ),
    "Flatbed": (
        "Flatbed carriers haul steel, lumber, construction materials, and machinery. "
        "Pain points: tarping labor, oversize permitting, seasonal demand swings. "
        "RPM typically $2.50–$4.50."
    ),
    "Step Deck": (
        "Step Deck carriers haul tall machinery, construction equipment, and wind components. "
        "Pain points: permitting for over-height freight, finding specialized loads. "
        "RPM typically $2.50–$4.50."
    ),
    "Hotshot": (
        "Hotshot carriers run time-critical regional freight, often oilfield or construction. "
        "Pain points: inconsistent load volume, empty return trips, fuel cost per mile. "
        "RPM typically $1.80–$3.50."
    ),
    "Box Truck": (
        "Box truck carriers often run local or regional delivery. Pain points: limited "
        "national load boards, inconsistent volume, low rates on retail delivery. "
        "RPM typically $1.50–$2.80."
    ),
    "Sprinter Van": (
        "Sprinter van carriers run expedited freight, medical supplies, and e-commerce. "
        "Pain points: finding premium expedited loads, positioning deadhead. "
        "RPM typically $1.80–$3.50 with expedited premiums."
    ),
    "Power Only": (
        "Power Only carriers run drop-and-hook operations for Amazon, UPS, or retail DCs. "
        "Pain points: finding drop-hook opportunities, trailer interchange logistics. "
        "RPM typically $1.80–$2.80 with consistent volume."
    ),
    "Car Hauler": (
        "Car hauler carriers transport vehicles from dealers, auctions, and private sellers. "
        "Pain points: backhaul positioning, insurance requirements, auction seasonality. "
        "Typically priced per vehicle ($300–$700+ depending on distance)."
    ),
}


class GroqAgent:
    def __init__(self, api_key: str, model: str = "llama-3.3-70b-versatile"):
        if not api_key:
            raise ValueError("api_key is required for GroqAgent")
        self._client = Groq(api_key=api_key)
        self.model = model

    def _complete(self, system: str, user: str, max_tokens: int = 512) -> str:
        """Call Groq with simple retry/backoff on rate-limit errors.

        If the API fails even after retries, return a safe fallback so the
        dialing loop doesn't crash or go silent.
        """

        fallback = (
            "Hi, this is Tony with Indus Transports LLC. "
            "I’m calling because we help carriers keep dispatch moving with less "
            "broker paperwork delays and more consistent load options. "
            "Are you currently running Dry Van, Reefer, or Flatbed?"
        )


        delays = [2, 5, 10]
        last_exc: Exception | None = None

        for attempt in range(3):
            try:
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
            except Exception as exc:  # groq raises library-specific exceptions
                last_exc = exc
                msg = str(exc).lower()
                is_rate_limited = (
                    "429" in msg
                    or "rate limit" in msg
                    or "rate-limited" in msg
                    or "too many requests" in msg
                    or "rate_limit" in msg
                )

                if is_rate_limited and attempt < len(delays):
                    delay_s = delays[attempt]
                    logger.warning(
                        "Groq rate limited (attempt %d/%d). Backing off %ss. Error: %s",
                        attempt + 1,
                        3,
                        delay_s,
                        exc,
                    )
                    time.sleep(delay_s)
                    continue

                # Non-rate-limit errors (or last attempt) — break to fallback.
                break

        logger.error("Groq completion failed; using fallback. Error: %s", last_exc)
        return fallback


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
        truck_type: str = "",
    ) -> str:
        if _is_spectrum_company(company_name, company_context):
            user_prompt = (
                f"Write the spoken Spectrum Business call script for {agent_name} calling {contact_name}. "
                f"Objective: {objective}. Company context: {company_context or _SPECTRUM_CONTEXT}. "
                f"Contact context: {context or 'No extra contact context.'} "
                "Keep the script faithful to the provided Spectrum Business campaign."
            )
            text = self._complete(_SPECTRUM_CALL_SCRIPT_SYSTEM, user_prompt, max_tokens=360)
            if _looks_like_freight_text(text):
                return _SPECTRUM_STATIC_SCRIPT.format(agent_name=agent_name)
            return text or _SPECTRUM_STATIC_SCRIPT.format(agent_name=agent_name)

        context_block = (
            f"Contact context: {context}." if context else "No extra contact context."
        )
        company_block = (
            f"Company context: {company_context}." if company_context
            else "Use general truck dispatch sales context."
        )
        website_block = f"Website: {company_website}." if company_website else ""
        equipment_block = (
            f"Carrier equipment: {truck_type}. Equipment context: {_EQUIPMENT_CONTEXT.get(truck_type, '')}"
            if truck_type else "Equipment type unknown — ask what equipment they run."
        )
        user_prompt = (
            f"Write the spoken call script for {agent_name} from {company_name} "
            f"calling {contact_name}. Objective: {objective}. Tone: {tone}. "
            f"{company_block} {website_block} {context_block} {equipment_block} "
            "Keep the full spoken script under 95 words. "
            "Make the ask specific: reference the carrier's equipment type or ask about their lanes."
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
        truck_type: str = "",
    ) -> str:
        if _is_spectrum_company(company_name, company_context):
            user_prompt = (
                f"Leave a voicemail from {agent_name} at Spectrum Business for {contact_name}. "
                f"The offer: {offer_summary or _SPECTRUM_CONTEXT}. "
                f"Callback number: {callback_number}."
            )
            text = self._complete(_SPECTRUM_VOICEMAIL_SYSTEM, user_prompt, max_tokens=180)
            if _looks_like_freight_text(text):
                return _spectrum_voicemail_fallback(agent_name, callback_number)
            return text or _spectrum_voicemail_fallback(agent_name, callback_number)

        equipment_block = (
            f"Carrier equipment: {truck_type}. {_EQUIPMENT_CONTEXT.get(truck_type, '')}"
            if truck_type else ""
        )
        user_prompt = (
            f"Leave a voicemail from {agent_name} at {company_name} for {contact_name}. "
            f"The offer: {offer_summary}. "
            f"Company context: {company_context}. "
            f"{equipment_block} "
            f"Callback number: {callback_number}."
        )
        return self._complete(_VOICEMAIL_SYSTEM, user_prompt, max_tokens=180)


def _is_spectrum_company(company_name: str, company_context: str = "") -> bool:
    text = f"{company_name} {company_context}".lower()
    return "spectrum" in text or "pure fiber" in text or "business internet" in text


def _looks_like_freight_text(text: str) -> bool:
    lower = (text or "").lower()
    return any(
        term in lower
        for term in (
            "dispatch",
            "carrier",
            "truck",
            "freight",
            "load",
            "lane",
            "dry van",
            "reefer",
            "flatbed",
            "indus",
        )
    )


def _spectrum_voicemail_fallback(agent_name: str, callback_number: str) -> str:
    callback = f" Please call me back at {callback_number}." if callback_number else ""
    return (
        f"Hi, this is {agent_name} calling from Spectrum Business. "
        "Spectrum recently expanded our pure fiber network in your area, and we can discuss faster business internet, "
        "more reliable phone services, and cost-effective packages. "
        "We have technician visits available Monday through Friday, 8:00 AM to 4:00 PM over the next two weeks."
        f"{callback}"
    )
