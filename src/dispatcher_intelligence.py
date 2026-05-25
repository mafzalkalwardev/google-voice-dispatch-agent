"""Dispatcher conversation intelligence for INDUS TRANSPORTS LLC.

This module keeps the freight-specific knowledge, state tracking, and pricing
guardrails out of the realtime audio loop. The LLM still handles natural
conversation, but deterministic state and negotiation rules keep Tony grounded.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


EQUIPMENT_PRICING: dict[str, int] = {
    "Box Truck": 10,
    "Hotshot": 10,
    "Car Hauler": 10,
    "Sprinter Van": 15,
    "Power Only": 8,
    "Dry Van": 6,
    "Reefer": 6,
    "Step Deck": 6,
    "Flatbed": 6,
}

_TRUCK_ALIASES: dict[str, str] = {
    "box truck": "Box Truck",
    "boxtruck": "Box Truck",
    "straight truck": "Box Truck",
    "hotshot": "Hotshot",
    "hot shot": "Hotshot",
    "car hauler": "Car Hauler",
    "car carrier": "Car Hauler",
    "sprinter": "Sprinter Van",
    "sprinter van": "Sprinter Van",
    "cargo van": "Sprinter Van",
    "power only": "Power Only",
    "dry van": "Dry Van",
    "van trailer": "Dry Van",
    "reefer": "Reefer",
    "refrigerated": "Reefer",
    "flatbed": "Flatbed",
    "step deck": "Step Deck",
    "stepdeck": "Step Deck",
}

_ACCESSORY_TERMS = (
    "tarps", "tarp", "straps", "chains", "binders", "ramps", "liftgate",
    "lift gate", "e-track", "etracks", "pallet jack", "reefer unit",
    "winch", "headache rack", "dunnage", "load bars",
)

_FACTORING_NAMES = (
    "otrcapital", "otr capital", "rts", "triumph", "wex", "loves",
    "love's", "tafs", "apex", "e capital", "ecapital", "truckstop factoring",
)

MARKET_KNOWLEDGE = (
    "Florida outbound is often weak, so reduce deadhead and price inbound/outbound carefully.",
    "Midwest markets usually have steadier freight density and practical lane options.",
    "Northeast freight can pay well, but tolls, parking, delivery windows, and traffic make planning harder.",
    "Texas outbound is generally strong, especially around Dallas, Houston, San Antonio, and Laredo.",
    "Produce season can create reefer spikes, but timing and detention risk matter.",
    "Mountain regions often have lower freight density, so plan reloads before sending a truck in.",
    "Deadhead reduction comes from pre-booking reloads, avoiding one-way weak markets, and matching equipment to lane demand.",
    "Local work can be steadier but often pays less per mile; OTR usually creates more lane options and gross potential.",
)

EQUIPMENT_KNOWLEDGE = (
    "Dry Van: ask for trailer length, swing/roll doors, food-grade status, and preferred no-touch freight.",
    "Reefer: ask about unit condition, temperature range, chute, produce/meat experience, and detention tolerance.",
    "Flatbed/Step Deck: ask about tarps, chains, straps, ramps, oversize experience, and load height.",
    "Hotshot: ask trailer length, dovetail/ramps, CDL/non-CDL, weight limits, and whether they run regional or OTR.",
    "Box Truck: ask length, dock height, liftgate, pallet jack, and local/regional preference.",
    "Sprinter Van: ask cargo dimensions, weight capacity, expedited experience, and team/solo availability.",
    "Car Hauler: ask units capacity, enclosed/open, insurance limits, auction/dealer experience, and lanes.",
    "Power Only: ask if they prefer drop-and-hook, Amazon/UPS style work, trailer interchange, and lane flexibility.",
)

NEGOTIATION_RULES = (
    "Default dispatch percentages by equipment: Box Truck 10%, Hotshot 10%, Car Hauler 10%, Sprinter Van 15%, Power Only 8%, Dry Van 6%, Reefer 6%, Step Deck 6%, Flatbed 6%.",
    "Ask what percentage the carrier was hoping for before discounting.",
    "Negotiate down by only 1-2 percentage points, and only for quality carriers with clean paperwork, good availability, and realistic lanes.",
    "Prioritize closing quality carriers, but never sound desperate or chase a bad-fit carrier.",
    "Frame the percentage around dedicated coverage, rate negotiation, paperwork, and deadhead reduction.",
)

COMPLIANCE_RULES = (
    "Never guarantee specific income, miles, rates, or freight volume.",
    "Do not pressure a carrier after they repeat that they are not interested.",
    "If they ask to be removed or say stop calling, apologize, confirm removal, and end.",
    "Never claim to be a broker, shipper, government agency, or Google Voice.",
    "Do not mention call recording unless recording is actually enabled and consent rules are handled.",
)

OBJECTION_HANDLING = (
    "Who is this: briefly repeat Tony, Indus Transports LLC, and freight dispatch/load support.",
    "Already have dispatch: respect it, ask if they would compare rates or keep a backup for weak weeks.",
    "Too expensive: ask what percentage they hoped for, then tie fee to dedicated work and better lane planning.",
    "Busy/driving: keep it under one short question and ask for a better callback time.",
    "Send info: confirm email and ask for a specific 15-minute follow-up window.",
    "New authority: educate gently about paperwork, factoring, setup packets, lanes, and avoiding bad freight.",
)

LANE_STRATEGY = (
    "Ask where they are based, where they like to run, and where they refuse to go.",
    "For weak outbound states, talk about booking reloads before committing to the inbound move.",
    "For local carriers, qualify radius, home-daily expectations, appointment tolerance, and minimum daily gross.",
    "For OTR carriers, qualify preferred regions, home time, and willingness to chase stronger markets.",
)

PAYMENT_FACTORING_KNOWLEDGE = (
    "Ask whether they use factoring, quick pay, or broker direct pay.",
    "Confirm whether their factoring company approves brokers before booking loads.",
    "Explain that setup packets, COI, W-9, MC authority, and factoring NOA often slow down first loads.",
    "Do not promise same-day payment; explain payment depends on broker terms, quick pay, or factoring approval.",
)


@dataclass
class DispatcherConversationState:
    truck_type: str = ""
    interest_level: str = ""
    objections: list[str] = field(default_factory=list)
    negotiated_percentage: str = ""
    local_or_otr: str = ""
    preferred_lanes: str = ""
    dispatcher_status: str = ""
    follow_up_status: str = ""
    mc_number: str = ""
    company_name: str = ""
    dimensions: str = ""
    accessories: str = ""
    email: str = ""
    factoring_company: str = ""
    pricing_discussion: str = ""
    carrier_style: str = "unknown"
    turn_count: int = 0

    def summary_lines(self) -> list[str]:
        fields = (
            ("truck_type", self.truck_type),
            ("interest_level", self.interest_level),
            ("objections", ", ".join(self.objections)),
            ("negotiated_percentage", self.negotiated_percentage),
            ("local_or_otr", self.local_or_otr),
            ("preferred_lanes", self.preferred_lanes),
            ("dispatcher_status", self.dispatcher_status),
            ("follow_up_status", self.follow_up_status),
            ("mc_number", self.mc_number),
            ("dimensions", self.dimensions),
            ("accessories", self.accessories),
            ("email", self.email),
            ("factoring_company", self.factoring_company),
            ("carrier_style", self.carrier_style),
        )
        return [f"- {name}: {value}" for name, value in fields if value]


def render_knowledge_sections() -> str:
    sections = (
        ("MARKET KNOWLEDGE", MARKET_KNOWLEDGE),
        ("EQUIPMENT KNOWLEDGE", EQUIPMENT_KNOWLEDGE),
        ("NEGOTIATION RULES", NEGOTIATION_RULES),
        ("COMPLIANCE RULES", COMPLIANCE_RULES),
        ("OBJECTION HANDLING", OBJECTION_HANDLING),
        ("LANE STRATEGY", LANE_STRATEGY),
        ("PAYMENT AND FACTORING KNOWLEDGE", PAYMENT_FACTORING_KNOWLEDGE),
    )
    rendered: list[str] = []
    for title, lines in sections:
        rendered.append(f"\n{title}:")
        rendered.extend(f"- {line}" for line in lines)
    return "\n".join(rendered)


def normalize_truck_type(text: str) -> str:
    lower = text.lower()
    for alias, canonical in _TRUCK_ALIASES.items():
        if alias in lower:
            return canonical
    return ""


def default_percentage_for(truck_type: str) -> int:
    canonical = normalize_truck_type(truck_type) or truck_type
    return EQUIPMENT_PRICING.get(canonical, 8)


def negotiation_floor_for(truck_type: str) -> int:
    default = default_percentage_for(truck_type)
    return max(1, default - (2 if default >= 10 else 1))


def parse_percentage(text: str) -> Optional[int]:
    match = re.search(r"\b(1[0-9]|[1-9])\s*(?:%|\bpercent(?:age)?\b)", text, re.I)
    if not match:
        return None
    return int(match.group(1))


def update_state_from_utterance(state: DispatcherConversationState, utterance: str) -> None:
    text = (utterance or "").strip()
    lower = text.lower()
    state.turn_count += 1

    truck_type = normalize_truck_type(text)
    if truck_type:
        state.truck_type = truck_type

    email = re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", text)
    if email:
        state.email = email.group(0)

    mc = re.search(r"\bMC[\s#:-]*(\d{5,9})\b", text, re.I)
    if mc:
        state.mc_number = "MC-" + mc.group(1)

    dimensions = _extract_dimensions(text)
    if dimensions:
        state.dimensions = dimensions

    accessories = [term for term in _ACCESSORY_TERMS if term in lower]
    if accessories:
        state.accessories = _merge_csv(state.accessories, accessories)

    factoring = _extract_factoring(lower)
    if factoring:
        state.factoring_company = factoring

    if any(word in lower for word in ("local", "home daily", "home every night")):
        state.local_or_otr = "Local"
    elif any(word in lower for word in ("otr", "over the road", "48 states", "regional")):
        state.local_or_otr = "OTR" if "regional" not in lower else "Regional"

    lanes = _extract_lanes(text)
    if lanes:
        state.preferred_lanes = lanes

    if any(phrase in lower for phrase in ("book myself", "dispatch myself", "use dat", "use truckstop")):
        state.dispatcher_status = "Self-dispatched"
    elif any(phrase in lower for phrase in ("have a dispatcher", "got a dispatcher", "already have dispatch")):
        state.dispatcher_status = "Has dispatcher"
    elif "leased" in lower:
        state.dispatcher_status = "Leased to carrier"
    elif "own authority" in lower or "my authority" in lower:
        state.dispatcher_status = "Own authority"

    pct = parse_percentage(text)
    if pct is not None:
        state.pricing_discussion = f"Carrier mentioned {pct}%"
        state.negotiated_percentage = f"{pct}%"

    objection = _detect_objection(lower)
    if objection and objection not in state.objections:
        state.objections.append(objection)

    follow_up = _detect_follow_up(text)
    if follow_up:
        state.follow_up_status = follow_up

    state.interest_level = _detect_interest(lower, state.interest_level)
    state.carrier_style = classify_carrier_style(state, lower)


def build_dynamic_system_prompt(
    *,
    agent_name: str,
    company_name: str,
    company_website: str,
    callback_number: str,
    company_context: str,
    contact_name: str,
    state: DispatcherConversationState,
) -> str:
    state_lines = state.summary_lines()
    state_summary = "\n".join(state_lines) if state_lines else "- no confirmed carrier details yet"
    style = state.carrier_style or "unknown"
    return f"""You are {agent_name}, a human-sounding freight dispatcher at {company_name}.

Company: {company_name}
Website: {company_website}
Callback number: {callback_number}
Prospect/contact: {contact_name or "unknown"}

Company context:
{company_context or "Dedicated freight dispatch for owner-operators and small carriers."}

Primary goal:
Qualify the carrier deeply and book a 15-minute onboarding call if there is a real fit.

Conversation state you must remember:
{state_summary}

Adaptive style:
- Current carrier style: {style}.
- Rushed carriers: answer in one short sentence and ask for a callback time.
- Engaged carriers: use two natural sentences, then ask one specific qualifying question.
- Skeptical carriers: soften the tone, acknowledge first, and do not argue.
- New MCs: educate clearly about setup, factoring, lanes, and paperwork without talking down.
- Use natural phone language, varied sentence structure, and small fillers only when helpful.
- If interrupted or corrected, recover with: "Sure, go ahead" or "My mistake, what were you saying?"
- Do not repeat the same value pitch twice.

Hard output rules:
- Respond only with what Tony says aloud. No markdown, labels, notes, or stage directions.
- Default to 1-2 short sentences. Only go longer when the carrier is clearly engaged.
- Ask one question per turn.
- Never guarantee loads, earnings, rates, miles, or specific gross revenue.
- Never sound desperate. Do not chase bad-fit carriers.
- If they ask to be removed, confirm removal and end politely.
{render_knowledge_sections()}
"""


def build_pricing_reply(state: DispatcherConversationState, utterance: str) -> Optional[str]:
    lower = utterance.lower()
    pricing_intent = any(
        phrase in lower
        for phrase in ("what do you charge", "percentage", "percent", "your fee", "dispatch fee", "rate do you charge")
    )
    desired = parse_percentage(utterance)
    if not pricing_intent and desired is None:
        return None

    equipment = state.truck_type or normalize_truck_type(utterance) or "your equipment"
    default = default_percentage_for(equipment)
    floor = negotiation_floor_for(equipment)

    if desired is None:
        state.pricing_discussion = f"Quoted default {default}% for {equipment}"
        return (
            f"For {equipment}, we normally start around {default}% because it includes dedicated dispatch, "
            "rate negotiation, and paperwork. What percentage were you hoping for?"
        )

    state.pricing_discussion = f"Carrier asked for {desired}%; target range {floor}-{default}%"
    if desired < floor:
        state.negotiated_percentage = f"{floor}%"
        return (
            f"I probably cannot do {desired}% and still give you dedicated coverage. "
            f"For {equipment}, the best realistic range is about {floor}-{default}%; would {floor}% make sense if the lanes fit?"
        )
    if desired <= default:
        state.negotiated_percentage = f"{desired}%"
        return (
            f"If your paperwork is clean and the lanes are realistic, I can work near {desired}% for the right carrier. "
            "Are you mostly running local, regional, or OTR?"
        )

    state.negotiated_percentage = f"{default}%"
    return (
        f"We would normally be lower than {desired}% for {equipment}; our starting point is around {default}%. "
        "What lanes are you trying to cover right now?"
    )


def build_guardrail_reply(state: DispatcherConversationState, utterance: str) -> Optional[str]:
    lower = utterance.lower().strip()
    if any(phrase in lower for phrase in ("remove me", "stop calling", "do not call", "don't call")):
        state.interest_level = "DNC"
        return "Of course. I will mark you do-not-call right now. Sorry for the interruption, and have a safe day."
    if any(phrase in lower for phrase in ("guarantee", "guaranteed", "promise me loads", "promise loads")):
        return (
            "I cannot guarantee loads or income because the market moves. "
            "What I can do is work the lanes, reduce deadhead, and negotiate hard on every load."
        )
    if any(phrase in lower for phrase in ("hold on", "wait", "let me talk", "you cut me off", "stop interrupting")):
        return "You are right, go ahead. What were you saying?"
    if any(phrase in lower for phrase in ("busy", "driving", "in a meeting", "no time")):
        state.follow_up_status = "Needs callback"
        return "No worries, I will keep it quick. What is a better time today or tomorrow for a 15-minute call?"
    return None


def classify_carrier_style(state: DispatcherConversationState, lower_text: str) -> str:
    if any(phrase in lower_text for phrase in ("busy", "driving", "no time", "make it quick")):
        return "rushed"
    if any(phrase in lower_text for phrase in ("who is this", "how did you get", "not interested", "too expensive")):
        return "skeptical"
    if any(phrase in lower_text for phrase in ("new authority", "new mc", "just got my mc", "starting out")):
        return "new_mc"
    if "?" in lower_text or any(phrase in lower_text for phrase in ("tell me", "how does", "what do you", "sounds good")):
        return "engaged"
    return state.carrier_style if state.carrier_style != "unknown" else "neutral"


def _merge_csv(existing: str, values: list[str]) -> str:
    seen = [part.strip() for part in existing.split(",") if part.strip()]
    for value in values:
        if value not in seen:
            seen.append(value)
    return ", ".join(seen)


def _extract_dimensions(text: str) -> str:
    patterns = (
        r"\b\d{2}\s*(?:ft|feet|foot|')\b",
        r"\b\d{1,2}\s*x\s*\d{1,2}(?:\s*x\s*\d{1,2})?\b",
        r"\b\d{1,2}\s*(?:pallet|skid)s?\b",
    )
    hits: list[str] = []
    for pattern in patterns:
        hits.extend(match.group(0).strip() for match in re.finditer(pattern, text, re.I))
    return ", ".join(dict.fromkeys(hits))


def _extract_factoring(lower_text: str) -> str:
    for name in _FACTORING_NAMES:
        if name in lower_text:
            return name.title()
    if "factoring" in lower_text:
        return "Factoring mentioned"
    if "quick pay" in lower_text:
        return "Quick pay"
    return ""


def _extract_lanes(text: str) -> str:
    lower = text.lower()
    if "anywhere" in lower or "48 states" in lower:
        return "48 states"
    lane_match = re.search(
        r"\b(?:from|out of|based in)\s+([A-Za-z .]+?)\s+(?:to|into|down to|up to)\s+([A-Za-z .]+?)(?:[.,!?]|$)",
        text,
        re.I,
    )
    if lane_match:
        return f"{lane_match.group(1).strip()} to {lane_match.group(2).strip()}"
    region_terms = ("midwest", "northeast", "southeast", "texas", "florida", "california", "west coast", "east coast")
    hits = [term.title() for term in region_terms if term in lower]
    return ", ".join(hits)


def _detect_objection(lower_text: str) -> str:
    checks = (
        ("not interested", "Not interested"),
        ("already have", "Already has dispatcher"),
        ("too expensive", "Price concern"),
        ("how did you get", "Source of number"),
        ("who is this", "Company identity"),
        ("guarantee", "Guarantee request"),
        ("send me", "Wants information sent"),
    )
    for phrase, label in checks:
        if phrase in lower_text:
            return label
    return ""


def _detect_follow_up(text: str) -> str:
    lower = text.lower()
    if "email" in lower or "send info" in lower or "send me" in lower:
        return "Send information"
    if "call back" in lower or "tomorrow" in lower or "next week" in lower:
        return text.strip()[:120]
    if any(day in lower for day in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday")):
        return text.strip()[:120]
    return ""


def _detect_interest(lower_text: str, existing: str) -> str:
    if any(phrase in lower_text for phrase in ("remove me", "stop calling", "do not call", "don't call")):
        return "DNC"
    if any(phrase in lower_text for phrase in ("not interested", "no thanks", "don't need")):
        return "No"
    if any(phrase in lower_text for phrase in ("send me", "call me", "tomorrow", "maybe", "interested", "sounds good")):
        return "Maybe" if "maybe" in lower_text else "Yes"
    return existing
