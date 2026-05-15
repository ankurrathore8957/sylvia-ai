from __future__ import annotations

import os
import re
import ast
import operator
import platform
import subprocess
import threading
from dataclasses import dataclass, field
from typing import List, Literal, Optional, Sequence, Tuple

try:
    from ctransformers import AutoConfig, AutoModelForCausalLM  # type: ignore
except Exception:
    AutoConfig = None
    AutoModelForCausalLM = None

try:
    from transformers import AutoTokenizer  # type: ignore
except Exception:
    AutoTokenizer = None

try:
    import emotion as emotion_engine  # type: ignore
except Exception:
    emotion_engine = None

try:
    import orchestrator as orchestrator_engine  # type: ignore
except Exception:
    orchestrator_engine = None

Role = Literal["user", "assistant"]
Message = Tuple[Role, str]

APP_ALIASES = {
    "activity monitor": "Activity Monitor",
    "app store": "App Store",
    "automator": "Automator",
    "books": "Books",
    "calculator": "Calculator",
    "calendar": "Calendar",
    "chrome": "Chrome",
    "chess": "Chess",
    "contacts": "Contacts",
    "dictionary": "Dictionary",
    "facetime": "FaceTime",
    "facetime app": "FaceTime",
    "finder": "Finder",
    "freeform": "Freeform",
    "garageband": "GarageBand",
    "home": "Home",
    "image capture": "Image Capture",
    "imovie": "iMovie",
    "itunes": "Music",
    "keynote": "Keynote",
    "launchpad": "Launchpad",
    "mail": "Mail",
    "maps": "Maps",
    "messages": "Messages",
    "music": "Music",
    "music app": "Music",
    "notes": "Notes",
    "numbers": "Numbers",
    "pages": "Pages",
    "camera": "Photo Booth",
    "photo booth": "Photo Booth",
    "photos": "Photos",
    "podcasts": "Podcasts",
    "preview": "Preview",
    "quicktime": "QuickTime Player",
    "quicktime player": "QuickTime Player",
    "reminders": "Reminders",
    "broswer": "Safari",
    "browser": "Safari",
    "safari": "Safari",
    "settings": "System Settings",
    "shortcuts": "Shortcuts",
    "siri": "Siri",
    "stickies": "Stickies",
    "stocks": "Stocks",
    "system preferences": "System Preferences",
    "system settings": "System Settings",
    "terminal": "Terminal",
    "textedit": "TextEdit",
    "text edit": "TextEdit",
    "time machine": "Time Machine",
    "tv": "TV",
    "voice memos": "Voice Memos",
    "weather": "Weather",
}


@dataclass
class BrainConfig:
    model_dir: str = os.path.join(os.path.dirname(__file__), "BRAIN")
    model_file: str = "model.gguf"
    model_type: str = "llama"
    prefer_gpu_layers: int = field(
        default_factory=lambda: int(
            os.environ.get(
                "SYLVIA_GPU_LAYERS",
                "24" if platform.system() == "Darwin" else "0",
            )
        )
    )
    context_length: int = 1024
    max_history_turns: int = 3
    threads: int = field(
        default_factory=lambda: max(2, min(6, os.cpu_count() or 4))
    )


class SylviaBrain:
    """
    Sylvia local AI brain.

    Architecture:
    - deterministic direct command layer
    - tokenizer-aware chat formatting
    - GGUF local inference
    - conversational personality system
    """

    def __init__(self, config: BrainConfig | None = None) -> None:
        self.config = config or BrainConfig()

        self._llm = None
        self._tokenizer = None

        self._lock = threading.Lock()
        self._status = "Model: not loaded"
        self._persona_path = os.path.join(os.path.dirname(__file__), "user_persona.txt")
        self._persona_cache = ""
        self._persona_mtime = 0.0
        self._emotion_tracker = emotion_engine.EmotionTracker() if emotion_engine else None
        self._orchestrator = (
            orchestrator_engine.SylviaOrchestrator()
            if orchestrator_engine
            else None
        )

        self._system_prompt = """
You are Sylvia, a local AI companion.
Reply in clear English unless the user explicitly asks for Hindi or Hinglish.
Be warm, calm, concise, and accurate.
Answer the user's actual question directly.
Never invent random facts, names, code, files, or languages.
If unsure, say so briefly.
""".strip()

    @property
    def status(self) -> str:
        return self._status

    def load(self) -> None:
        if self._llm is not None:
            return

        if AutoModelForCausalLM is None:
            self._status = "Model: ctransformers not installed"
            return

        with self._lock:
            if self._llm is not None:
                return

            self._status = "Model: loading..."

            # Load tokenizer + configs
            try:
                if AutoTokenizer is not None:
                    self._tokenizer = AutoTokenizer.from_pretrained(
                        self.config.model_dir,
                        use_fast=True,
                    )
            except Exception:
                self._tokenizer = None

            llm_config = self._make_llm_config(gpu_layers=self.config.prefer_gpu_layers)

            # Load GGUF model
            try:
                self._llm = AutoModelForCausalLM.from_pretrained(
                    model_path_or_repo_id=self.config.model_dir,
                    model_file=self.config.model_file,
                    model_type=self.config.model_type,
                    config=llm_config,
                )
                backend = "Metal" if self.config.prefer_gpu_layers > 0 else "CPU"
            except Exception:
                llm_config = self._make_llm_config(gpu_layers=0)
                self._llm = AutoModelForCausalLM.from_pretrained(
                    model_path_or_repo_id=self.config.model_dir,
                    model_file=self.config.model_file,
                    model_type=self.config.model_type,
                    config=llm_config,
                )
                backend = "CPU"

            self._status = f"Model: ready (local/{backend})"

    def _make_llm_config(self, gpu_layers: int):
        if AutoConfig is None:
            return None
        llm_config = AutoConfig.from_pretrained(self.config.model_dir)
        llm_config.config.context_length = int(self.config.context_length)
        llm_config.config.threads = int(self.config.threads)
        llm_config.config.batch_size = 16
        llm_config.config.gpu_layers = max(0, int(gpu_layers))
        llm_config.config.temperature = 0.25
        llm_config.config.top_p = 0.85
        llm_config.config.top_k = 30
        llm_config.config.repetition_penalty = 1.15
        llm_config.config.max_new_tokens = 80
        return llm_config

    def build_prompt(
        self,
        user_text: str,
        history: Optional[Sequence[Message]] = None,
    ) -> str:

        system_prompt = self._build_system_prompt(user_text)
        messages = [{"role": "system", "content": system_prompt}]

        if history:
            recent = list(history)[-(self.config.max_history_turns * 2):]

            for role, content in recent:
                clean_content = self._limit_context_text(content, limit=380)
                messages.append(
                    {
                        "role": "assistant" if role == "assistant" else "user",
                        "content": clean_content,
                    }
                )

        messages.append(
            {
                "role": "user",
                "content": self._limit_context_text(user_text, limit=500),
            }
        )

        # Use tokenizer chat template if available
        if (
            self._tokenizer is not None
            and hasattr(self._tokenizer, "apply_chat_template")
        ):
            try:
                return self._tokenizer.apply_chat_template(
                    messages,
                    tokenize=False,
                    add_generation_prompt=True,
                )
            except Exception:
                pass

        # Fallback formatting
        prompt_parts = [system_prompt, "\n\n"]

        for msg in messages[1:]:
            role = msg["role"]

            if role == "user":
                prompt_parts.append(f"User: {msg['content']}\n")
            elif role == "assistant":
                prompt_parts.append(f"Sylvia: {msg['content']}\n")

        prompt_parts.append("Sylvia:")
        return "".join(prompt_parts)

    def _build_system_prompt(self, user_text: str) -> str:
        parts = [self._system_prompt]

        persona = self._load_user_persona()
        if persona:
            parts.append("User preferences:\n" + persona)

        if self._orchestrator is not None:
            parts.append(self._orchestrator.response_style_instruction(user_text))

        if self._emotion_tracker is not None and emotion_engine is not None:
            try:
                context = self._emotion_tracker.observe(user_text)
                if context.label != "neutral" and context.intensity >= 0.5:
                    parts.append(emotion_engine.build_emotion_instruction(context))
            except Exception:
                pass

        return "\n\n".join(parts)

    def _load_user_persona(self) -> str:
        try:
            mtime = os.path.getmtime(self._persona_path)
            if self._persona_cache and mtime == self._persona_mtime:
                return self._persona_cache
            with open(self._persona_path, "r", encoding="utf-8") as persona_file:
                self._persona_cache = self._limit_context_text(persona_file.read(), limit=380)
                self._persona_mtime = mtime
                return self._persona_cache
        except Exception:
            return ""

    def generate(
        self,
        user_text: str,
        history: Optional[Sequence[Message]] = None,
        max_new_tokens: int = 80,
        temperature: float = 0.25,
    ) -> str:
        reply = self.generate_cancellable(
            user_text=user_text,
            history=history,
            max_new_tokens=max_new_tokens,
            temperature=temperature,
            stop_event=None,
        )
        return reply or "I’m here :)"

    def generate_cancellable(
        self,
        user_text: str,
        history: Optional[Sequence[Message]] = None,
        max_new_tokens: int = 80,
        temperature: float = 0.25,
        stop_event: threading.Event | None = None,
    ) -> Optional[str]:

        # Fast deterministic layer
        direct = self._answer_directly(user_text)
        if direct is not None:
            if stop_event is not None and stop_event.is_set():
                return None
            return direct

        if self._orchestrator is not None:
            routed = self._orchestrator.route(user_text)
            if routed is not None:
                if stop_event is not None and stop_event.is_set():
                    return None
                return routed.text

        if stop_event is not None and stop_event.is_set():
            return None

        self.load()

        if stop_event is not None and stop_event.is_set():
            return None

        if self._llm is None:
            return f"Local model unavailable ({self._status})."

        prompt = self.build_prompt(
            user_text=user_text,
            history=history,
        )

        try:
            with self._lock:
                stream = self._llm(
                    prompt,
                    max_new_tokens=max_new_tokens,
                    temperature=temperature,
                    top_k=30,
                    top_p=0.85,
                    repetition_penalty=1.15,
                    stop=[
                        "<|user|>",
                        "<|system|>",
                        "<|assistant|>",
                        "</s>",
                        "\nUser:",
                        "User:",
                        "\nSylvia:",
                    ],
                    stream=True,
                )

                chunks: list[str] = []
                try:
                    for chunk in stream:
                        if stop_event is not None and stop_event.is_set():
                            close = getattr(stream, "close", None)
                            if callable(close):
                                close()
                            return None
                        chunks.append(str(chunk))
                finally:
                    close = getattr(stream, "close", None)
                    if callable(close):
                        close()

            text = self._clean_output("".join(chunks), user_text)
            if self._looks_like_wrong_language(text, user_text):
                return "I got a little garbled there. Please ask me again in English."

            return text or "I’m here :)"

        except Exception as exc:
            return f"(generation failed: {type(exc).__name__})"

    def _clean_output(self, text: str, user_text: str) -> str:
        text = text.strip()

        junk_markers = (
            "<reponame>",
            "<filename>",
            "```",
            "<|assistant|>",
            "<|user|>",
            "<|system|>",
        )

        for marker in junk_markers:
            if marker in text:
                text = text.split(marker)[0].strip()

        css_or_markup_markers = (
            "<style",
            "</style",
            "{",
            "font-family:",
            "background:",
            "color:",
            "padding:",
            "margin:",
        )
        if any(marker in text.lower() for marker in css_or_markup_markers):
            text = re.split(r"<style|</style|```|\{|\bfont-family:|\bbackground:|\bcolor:|\bpadding:|\bmargin:", text, maxsplit=1, flags=re.IGNORECASE)[0].strip()

        text = re.sub(
            r"^\s*(AI|Assistant|Sylvia)\s*:\s*",
            "",
            text,
            flags=re.IGNORECASE,
        )

        text = text.strip().strip('"').strip()

        if text.lower() == user_text.lower():
            return ""

        # Prevent endless rambling
        sentences = re.split(r"(?<=[.!?])\s+", text)

        if len(sentences) > 4:
            text = " ".join(sentences[:4]).strip()

        return text

    def _looks_like_wrong_language(self, text: str, user_text: str) -> bool:
        if self._allows_non_english(user_text):
            return False

        lower = f" {re.sub(r'[^a-zà-ÿ]+', ' ', text.lower())} "
        wrong_language_markers = (
            " olá ",
            " você ",
            " obrigado ",
            " obrigada ",
            " porque ",
            " então ",
            " não ",
            " sim ",
            " como posso ",
            " estoy ",
            " gracias ",
            " porque ",
            "bonjour",
            "merci",
            "désolé",
            "guten",
            "danke",
        )
        marker_hits = sum(1 for marker in wrong_language_markers if marker in lower)
        if marker_hits >= 2:
            return True

        non_ascii_letters = sum(1 for char in text if ord(char) > 127 and char.isalpha())
        ascii_letters = sum(1 for char in text if char.isascii() and char.isalpha())
        return non_ascii_letters > 6 and non_ascii_letters > ascii_letters * 0.25

    def _allows_non_english(self, user_text: str) -> bool:
        normalized = user_text.lower()
        language_requests = (
            "in hindi",
            "speak hindi",
            "reply in hindi",
            "hindi me",
            "hindi mein",
            "hinglish",
            "roman hindi",
            "hindi english",
            "translate",
            "portuguese",
            "spanish",
            "french",
            "german",
        )
        return any(phrase in normalized for phrase in language_requests)

    def _limit_context_text(self, text: str, limit: int = 900) -> str:
        clean = " ".join((text or "").split())
        return clean[:limit].strip()

    def _answer_directly(self, user_text: str) -> Optional[str]:
        normalized = re.sub(r"\s+", " ", user_text.strip().lower())
        normalized = normalized.strip(" .!?")

        # Greetings
        if normalized in {"hi", "hello", "hey", "hii", "yo", "namaste"}:
            if self._should_reply_hinglish(user_text):
                return "Hey Ankur :) kya chal raha hai?"
            return "Hey Ankur :) I’m Sylvia. What’s up?"

        if normalized in {"good morning", "morning"}:
            if self._should_reply_hinglish(user_text):
                return "Good morning Ankur :) aaj ka din halka aur accha rahe."
            return "Good morning Ankur :) Hope your day starts gently."

        if normalized in {"good afternoon"}:
            return "Good afternoon :) How’s your day going?"

        if normalized in {"good evening"}:
            if self._should_reply_hinglish(user_text):
                return "Good evening Ankur :) kaise ho?"
            return "Good evening Ankur :)"

        # Identity
        if any(
            phrase in normalized
            for phrase in (
                "who are you",
                "what is your name",
                "your name",
                "ur name",
            )
        ):
            if self._should_reply_hinglish(user_text):
                return "Main Sylvia hoon :) tumhari local AI companion."
            return "I’m Sylvia :) Your local AI companion."

        # App opening
        app_name = self._parse_open_app_command(normalized)

        if app_name:
            return self._open_mac_app(app_name)

        # Capitals
        capital_answers = {
            "france": "Paris",
            "germany": "Berlin",
            "italy": "Rome",
            "spain": "Madrid",
            "japan": "Tokyo",
            "india": "New Delhi",
            "united states": "Washington, D.C.",
            "usa": "Washington, D.C.",
            "uk": "London",
            "united kingdom": "London",
            "uttar pradesh": "Lucknow",
        }

        capital_match = re.search(
            r"capital of ([a-z ]+?)(?:[?.!]|$)",
            normalized,
        )

        if capital_match:
            country = capital_match.group(1).strip()

            if country in capital_answers:
                return capital_answers[country]

        # Arithmetic
        expression = self._extract_arithmetic_expression(user_text)

        if expression is None:
            return None

        try:
            value = self._safe_eval_arithmetic(expression)
        except Exception:
            return None

        if isinstance(value, float) and value.is_integer():
            value = int(value)

        return str(value)

    def _parse_open_app_command(self, normalized_text: str) -> Optional[str]:
        match = re.match(
            r"^(?:please\s+)?(?:open|launch|start|run)\s+(?:the\s+)?(.+?)(?:\s+(?:app|application))?$",
            normalized_text,
        )

        if not match:
            return None

        app_name = match.group(1).strip()
        app_name = re.sub(r"\s+", " ", app_name)
        app_name = app_name.removeprefix("the ").strip()

        return APP_ALIASES.get(app_name, app_name.title())

    def _open_mac_app(self, app_name: str) -> str:
        if platform.system() != "Darwin":
            return (
                f"App launching is configured for macOS. "
                f"I would open {app_name} on a Mac."
            )

        try:
            result = subprocess.run(
                ["open", "-a", app_name],
                capture_output=True,
                text=True,
                timeout=8,
            )

        except Exception as exc:
            return f"I could not open {app_name}: {type(exc).__name__}."

        if result.returncode == 0:
            return f"Opening {app_name} :)"

        detail = (result.stderr or result.stdout).strip()

        if detail:
            return (
                f"I could not find or open {app_name}. "
                f"macOS said: {detail}"
            )

        return f"I could not find or open {app_name}."

    def _extract_arithmetic_expression(self, user_text: str) -> Optional[str]:
        text = user_text.lower()

        text = text.replace("plus", "+")
        text = text.replace("minus", "-")
        text = text.replace("times", "*")
        text = text.replace("multiplied by", "*")
        text = text.replace("x", "*")
        text = text.replace("divided by", "/")
        text = text.replace("over", "/")

        for match in re.finditer(r"[-+*/().\d\s]+", text):
            expression = match.group(0).strip()

            if not expression:
                continue

            if not any(op in expression for op in "+-*/"):
                continue

            if re.sub(r"[-+*/().\d\s]", "", expression):
                continue

            if len(re.findall(r"\d+(?:\.\d+)?", expression)) >= 2:
                return expression

        return None

    def _should_reply_hinglish(self, user_text: str) -> bool:
        if self._orchestrator is None:
            return False
        return bool(
            getattr(self._orchestrator, "hinglish_mode", False)
            or "hinglish" in user_text.lower()
        )

    def _safe_eval_arithmetic(self, expression: str) -> int | float:
        ops = {
            ast.Add: operator.add,
            ast.Sub: operator.sub,
            ast.Mult: operator.mul,
            ast.Div: operator.truediv,
            ast.USub: operator.neg,
            ast.UAdd: operator.pos,
        }

        def eval_node(node):
            if isinstance(node, ast.Expression):
                return eval_node(node.body)

            if isinstance(node, ast.Constant):
                if isinstance(node.value, (int, float)):
                    return node.value

            if isinstance(node, ast.BinOp):
                if type(node.op) in ops:
                    return ops[type(node.op)](
                        eval_node(node.left),
                        eval_node(node.right),
                    )

            if isinstance(node, ast.UnaryOp):
                if type(node.op) in ops:
                    return ops[type(node.op)](
                        eval_node(node.operand)
                    )

            raise ValueError("unsafe arithmetic expression")

        return eval_node(ast.parse(expression, mode="eval"))


_DEFAULT_BRAIN: SylviaBrain | None = None


def get_default_brain() -> SylviaBrain:
    global _DEFAULT_BRAIN

    if _DEFAULT_BRAIN is None:
        _DEFAULT_BRAIN = SylviaBrain()

    return _DEFAULT_BRAIN


def _cli() -> None:
    brain = get_default_brain()

    brain.load()

    print("🧠", brain.status, "\n")

    history: List[Message] = []

    while True:
        user_input = input("You: ").strip()

        if not user_input:
            continue

        if user_input.lower() in {"exit", "quit"}:
            break

        reply = brain.generate(
            user_input,
            history=history,
        )

        print("Sylvia:", reply)

        history.append(("user", user_input))
        history.append(("assistant", reply))


if __name__ == "__main__":
    _cli()
