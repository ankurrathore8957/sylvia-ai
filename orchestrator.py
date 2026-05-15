from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from automation import TaskAutomation


@dataclass
class RouteResult:
    text: str
    source: str


class SylviaOrchestrator:
    """
    Routes deterministic tasks before the local model.

    This keeps slow/fragile model generation away from questions that are
    better answered with local tools: system data, weather, time, storage,
    and web extraction/search.
    """

    def __init__(self) -> None:
        self.automation = TaskAutomation()
        self.hinglish_mode = False

    def route(self, user_text: str) -> Optional[RouteResult]:
        mode_reply = self._handle_language_mode(user_text)
        if mode_reply:
            return RouteResult(mode_reply, source="language-mode")

        tool_result = self.automation.answer(user_text)
        if tool_result:
            text = self._light_hinglish(tool_result.text) if self.hinglish_mode else tool_result.text
            return RouteResult(text, source=tool_result.source)

        return None

    def response_style_instruction(self, user_text: str) -> str:
        if self._asks_hinglish(user_text) or self.hinglish_mode:
            return (
                "Response language mode: Hinglish. Reply in natural Roman Hinglish, "
                "mixing simple Hindi and English words. Do not use Devanagari unless asked."
            )
        if self._asks_hindi(user_text):
            return "Response language mode: Hindi. Reply in simple Hindi."
        return "Response language mode: English. Reply in clear English."

    def _handle_language_mode(self, user_text: str) -> str:
        normalized = " ".join((user_text or "").lower().split()).strip(" .!?")
        if normalized in {
            "switch to hinglish",
            "reply in hinglish",
            "talk in hinglish",
            "hinglish mode",
            "turn on hinglish",
        }:
            self.hinglish_mode = True
            return "Hinglish mode on. Ab main simple Roman Hinglish mein reply karungi."
        if normalized in {
            "switch to english",
            "reply in english",
            "english mode",
            "turn off hinglish",
            "stop hinglish",
        }:
            self.hinglish_mode = False
            return "English mode on. I’ll reply in clear English now."
        return ""

    def _asks_hinglish(self, user_text: str) -> bool:
        normalized = user_text.lower()
        return bool(re.search(r"\b(hinglish|hindi english|roman hindi)\b", normalized))

    def _asks_hindi(self, user_text: str) -> bool:
        normalized = user_text.lower()
        return bool(re.search(r"\b(hindi|हिंदी)\b", normalized))

    def _light_hinglish(self, text: str) -> str:
        if not text:
            return text
        replacements = (
            ("It is ", "Abhi "),
            ("Battery is at ", "Battery "),
            ("Weather in ", "Weather "),
            ("I could not ", "Main abhi "),
            ("Storage: ", "Storage: "),
        )
        output = text
        for src, dst in replacements:
            output = output.replace(src, dst, 1)
        if output == text:
            return "Yeh raha: " + text
        if output.startswith("Battery ") and " hai" not in output[:35]:
            output = output.replace("%,", "% hai,", 1)
        return output
