"""Rotating opening lines so outbound calls do not sound identical."""

from __future__ import annotations

import hashlib
import random
from datetime import date
from typing import Optional

_CURATED_OPENINGS = [
    "Hey, how are you doing? This is {agent} with {company}.",
    "Hi there — {agent} here from {company}, how's it going?",
    "Hello, this is {agent} calling from {company} — hope I'm catching you at a good time.",
    "Hey, good to connect — {agent} from {company} here, how are you today?",
    "Hi, it's {agent} with {company}. Got a quick minute?",
    "Hey there, {agent} from {company} — how's your day going so far?",
    "Hi, this is {agent} at {company}. You got a second?",
    "Hello — {agent} with {company}. Hope you're doing well.",
]


def pick_opening(
    phone: str,
    agent_name: str,
    company_name: str,
    *,
    llm_line: Optional[str] = None,
    avoid_text: Optional[str] = None,
) -> str:
    """Pick a stable-but-varied opening for this phone (changes daily)."""
    if llm_line and llm_line.strip() and (not avoid_text or llm_line.strip() != avoid_text.strip()):
        return llm_line.strip()

    day_key = date.today().isoformat()
    digest = hashlib.sha256(f"{phone}|{day_key}".encode()).hexdigest()
    idx = int(digest[:8], 16) % len(_CURATED_OPENINGS)
    template = _CURATED_OPENINGS[idx]
    line = template.format(agent=agent_name, company=company_name)
    if avoid_text and line.strip() == avoid_text.strip():
        line = _CURATED_OPENINGS[(idx + 1) % len(_CURATED_OPENINGS)].format(
            agent=agent_name, company=company_name
        )
    return line


def random_curated(agent_name: str, company_name: str) -> str:
    template = random.choice(_CURATED_OPENINGS)
    return template.format(agent=agent_name, company=company_name)


def warmup_phrases(agent_name: str, company_name: str, count: int = 5) -> list[str]:
    """Phrases to pre-cache in TTSCache at batch start."""
    phrases = [
        random_curated(agent_name, company_name)
        for _ in range(min(count, len(_CURATED_OPENINGS)))
    ]
    seen: set[str] = set()
    out: list[str] = []
    for p in phrases:
        key = p.lower()
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out
