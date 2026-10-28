"""Human pickup scoring copied from the Auto Dialer detection pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field
import re


HUMAN_KEYWORDS = (
    "hello",
    "hi",
    "hey",
    "yes",
    "yeah",
    "who is this",
    "assalamualaikum",
    "salam alaikum",
    "speaking",
)


@dataclass(frozen=True)
class HumanDetection:
    detected: bool
    confidence: float
    reasons: list[str] = field(default_factory=list)


class HumanDetector:
    def classify(
        self,
        *,
        transcript: str = "",
        speech_duration_seconds: float = 0.0,
        answer_elapsed_seconds: float = 0.0,
        has_speech_like: bool = False,
    ) -> HumanDetection:
        reasons: list[str] = []
        confidence = 0.0

        if self.has_human_keyword(transcript):
            confidence += 0.45
            reasons.append("human greeting keyword")
        if 0.15 <= speech_duration_seconds <= 2.5:
            confidence += 0.35
            reasons.append("short speech burst")
        if has_speech_like and answer_elapsed_seconds <= 8.0:
            confidence += 0.15
            reasons.append("speech during human-first window")

        confidence = min(1.0, confidence)
        return HumanDetection(
            detected=confidence >= 0.70,
            confidence=confidence,
            reasons=reasons,
        )

    @staticmethod
    def has_human_keyword(text: str) -> bool:
        if not text:
            return False
        return any(re.search(rf"\b{re.escape(word)}\b", text, re.I) for word in HUMAN_KEYWORDS)
