from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class EmotionContext:
    label: str = "neutral"
    intensity: float = 0.0
    instruction: str = "Stay calm, clear, and natural."


class EmotionTracker:
    def __init__(self) -> None:
        self.current = EmotionContext()

    def observe(self, text: str) -> EmotionContext:
        detected = detect_emotion(text)
        if detected.intensity >= self.current.intensity or detected.label != "neutral":
            self.current = detected
        elif self.current.intensity > 0.0:
            self.current.intensity = max(0.0, self.current.intensity - 0.15)
            if self.current.intensity <= 0.1:
                self.current = EmotionContext()
        return self.current


def detect_emotion(text: str) -> EmotionContext:
    normalized = f" {re.sub(r'[^a-zA-Z]+', ' ', text.lower())} "

    patterns: list[tuple[str, tuple[str, ...], str]] = [
        (
            "sad",
            (" sad ", " upset ", " depressed ", " lonely ", " hurt ", " crying ", " broken "),
            "Be gentle and validating. Do not over-explain. Offer one small next step.",
        ),
        (
            "anxious",
            (" anxious ", " scared ", " worried ", " panic ", " nervous ", " stressed ", " overthinking "),
            "Slow the pace. Reassure first, then give a simple grounded suggestion.",
        ),
        (
            "angry",
            (" angry ", " mad ", " irritated ", " annoyed ", " frustrated ", " furious "),
            "Acknowledge the frustration. Keep the tone steady and avoid arguing.",
        ),
        (
            "tired",
            (" tired ", " exhausted ", " sleepy ", " drained ", " burnout ", " burned out "),
            "Use a soft tone. Keep the reply short and low-effort.",
        ),
        (
            "happy",
            (" happy ", " excited ", " great ", " awesome ", " proud ", " good news "),
            "Match the positive energy warmly without becoming loud or dramatic.",
        ),
    ]

    best_label = "neutral"
    best_score = 0
    best_instruction = "Stay calm, clear, and natural."

    for label, words, instruction in patterns:
        score = sum(1 for word in words if word in normalized)
        if score > best_score:
            best_label = label
            best_score = score
            best_instruction = instruction

    intensity = min(1.0, best_score / 2.0)
    return EmotionContext(best_label, intensity, best_instruction)


def build_emotion_instruction(context: EmotionContext) -> str:
    if context.label == "neutral":
        return "Detected user emotion: neutral. Stay calm, clear, and natural."
    return (
        f"Detected user emotion: {context.label} "
        f"(intensity {context.intensity:.1f}). {context.instruction}"
    )
