from __future__ import annotations

"""
Mac-compatible TTS pipeline for SYLVIA.

This version uses macOS's built-in neural/system voices through the `say`
command. It does not need a local VOICE model folder. The project virtualenv
also includes pyttsx3, but macOS `say` gives the most reliable file export.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass
class TTSConfig:
    output_dir: str = "tts_outputs"
    filename_prefix: str = "sylvia"
    keep_wav_files: int = 5
    rate: int = 158
    volume: float = 0.88
    pitch_base: int = -3
    pitch_modulation: int = 28
    preferred_voices: list[str] = field(
        default_factory=lambda: [
            "Tara (English (India))",
            "Tara",
            "Lekha",
            "Samantha",
            "Shelley (English (US))",
            "Sandy (English (US))",
            "Flo (English (US))",
            "Moira",
            "Karen",
        ]
    )


class TTSPipeline:
    def __init__(self, config: TTSConfig | None = None) -> None:
        self.config = config or TTSConfig()
        self._voice: str | None = None
        self._status = "TTS: not loaded"
        self._speech_lock = threading.Lock()
        self._play_lock = threading.Lock()
        self._play_process: subprocess.Popen | None = None
        self._synthesis_process: subprocess.Popen | None = None
        self._conversion_process: subprocess.Popen | None = None
        self._stop_token = 0
        self._available_voices_cache: set[str] | None = None

    @property
    def status(self) -> str:
        return self._status

    @property
    def voice(self) -> str:
        self.load()
        return self._voice or "Samantha"

    def load(self) -> None:
        if self._voice is not None:
            return
        if sys.platform != "darwin":
            self._status = "TTS: macOS voice backend required"
            return
        if shutil.which("say") is None:
            self._status = "TTS: macOS say command unavailable"
            return

        available = self.available_voices()
        for voice in self.config.preferred_voices:
            selected_voice = self._find_voice(available, voice)
            if selected_voice:
                self._voice = selected_voice
                break
        if self._voice is None:
            self._voice = "Samantha"

        self._status = f"TTS: ready ({self._voice})"

    def available_voices(self) -> set[str]:
        if self._available_voices_cache is not None:
            return set(self._available_voices_cache)
        if sys.platform != "darwin" or shutil.which("say") is None:
            return set()
        try:
            result = subprocess.run(
                ["say", "-v", "?"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except Exception:
            return set()

        voices: set[str] = set()
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            match = re.match(r"^(.+?)\s+[a-z]{2}_[A-Z]{2}\s+#", line)
            if match:
                voices.add(match.group(1).strip())
        self._available_voices_cache = set(voices)
        return voices

    def synthesize_to_wav(
        self,
        text: str,
        output_path: Optional[str] = None,
        speaker_id: Optional[int] = None,
        voice: Optional[str] = None,
    ) -> str:
        """
        Convert text to a WAV file. `speaker_id` is accepted for compatibility
        with the old VITS API but is ignored by the macOS backend.
        """
        del speaker_id
        self.load()
        if not self._voice:
            raise RuntimeError(f"TTS pipeline unavailable ({self._status}).")

        with self._play_lock:
            start_token = self._stop_token
        output_path = output_path or self._new_timestamped_wav_path()
        text = self._prepare_text(text)
        voice_name = self._select_voice(text, voice)
        self._raise_if_stopped(start_token)

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        fd, tmp_aiff = tempfile.mkstemp(suffix=".aiff")
        os.close(fd)
        os.remove(tmp_aiff)

        try:
            say_process = subprocess.Popen(
                [
                    "say",
                    "-v",
                    voice_name,
                    "-r",
                    str(int(self.config.rate)),
                    "-o",
                    tmp_aiff,
                    text,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            with self._play_lock:
                self._synthesis_process = say_process
            try:
                return_code = say_process.wait(timeout=60)
            except subprocess.TimeoutExpired as exc:
                self._terminate_process(say_process)
                raise RuntimeError("macOS say command timed out") from exc
            finally:
                with self._play_lock:
                    if self._synthesis_process is say_process:
                        self._synthesis_process = None

            with self._play_lock:
                was_cancelled = self._stop_token != start_token
            if was_cancelled:
                raise RuntimeError("TTS synthesis stopped")
            if return_code != 0:
                raise RuntimeError("macOS say command failed")

            if shutil.which("afconvert"):
                convert_process = subprocess.Popen(
                    ["afconvert", "-f", "WAVE", "-d", "LEI16", tmp_aiff, output_path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                with self._play_lock:
                    self._conversion_process = convert_process
                try:
                    convert_code = convert_process.wait(timeout=30)
                except subprocess.TimeoutExpired as exc:
                    self._terminate_process(convert_process)
                    raise RuntimeError("macOS audio conversion timed out") from exc
                finally:
                    with self._play_lock:
                        if self._conversion_process is convert_process:
                            self._conversion_process = None
                if convert_code != 0:
                    raise RuntimeError("macOS audio conversion failed")
            else:
                shutil.copyfile(tmp_aiff, output_path)
            with self._play_lock:
                if self._stop_token != start_token:
                    raise RuntimeError("TTS synthesis stopped")
            self._prune_old_wavs(os.path.dirname(os.path.abspath(output_path)))
            return output_path
        finally:
            try:
                os.remove(tmp_aiff)
            except OSError:
                pass

    def _new_timestamped_wav_path(self) -> str:
        output_dir = Path(self.config.output_dir)
        if not output_dir.is_absolute():
            output_dir = Path(__file__).resolve().parent / output_dir
        output_dir.mkdir(parents=True, exist_ok=True)

        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        base = output_dir / f"{self.config.filename_prefix}_{stamp}.wav"
        if not base.exists():
            return str(base)

        for index in range(1, 100):
            candidate = output_dir / f"{self.config.filename_prefix}_{stamp}_{index:02d}.wav"
            if not candidate.exists():
                return str(candidate)
        return str(base)

    def _prune_old_wavs(self, output_dir: str) -> None:
        keep = max(1, int(self.config.keep_wav_files))
        directory = Path(output_dir)
        files = sorted(
            directory.glob("*.wav"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for old_file in files[keep:]:
            try:
                old_file.unlink()
            except OSError:
                pass

    def _prepare_text(self, text: str) -> str:
        text = text.strip()
        text = re.sub(r"\[\[[^\]]+\]\]", "", text)
        text = re.sub(r"\s+([,.!?;:])", r"\1", text)
        text = re.sub(r"\bAI\b", "A I", text, flags=re.IGNORECASE)
        text = re.sub(r"\bTTS\b", "text to speech", text, flags=re.IGNORECASE)
        return self._apply_voice_tone(text)

    def _apply_voice_tone(self, text: str) -> str:
        if not text:
            return text

        pitch_base = int(self.config.pitch_base)
        pitch_modulation = int(self.config.pitch_modulation)
        if text.rstrip().endswith("?"):
            pitch_base += 1
        elif text.rstrip().endswith("!"):
            pitch_base += 2
            pitch_modulation += 3

        return f"[[pbas {pitch_base}]] [[pmod {pitch_modulation}]] {text}"

    def _select_voice(self, text: str, requested_voice: Optional[str]) -> str:
        if requested_voice:
            return requested_voice

        available = self.available_voices()
        has_devanagari = bool(re.search(r"[\u0900-\u097F]", text))
        if has_devanagari and "Lekha" in available:
            return "Lekha"
        tara_voice = self._find_voice(available, "Tara (English (India))") or self._find_voice(available, "Tara")
        if tara_voice:
            return tara_voice
        return self._voice or "Samantha"

    def _find_voice(self, available: set[str], preferred: str) -> str | None:
        if preferred in available:
            return preferred
        for voice in sorted(available):
            if voice.startswith(preferred + " ") or voice.startswith(preferred + "("):
                return voice
        return None

    def play_wav(self, wav_path: str) -> None:
        if sys.platform == "darwin" and shutil.which("afplay"):
            with self._play_lock:
                old_process = self._play_process
                self._play_process = None
            self._terminate_process(old_process)
            process = subprocess.Popen(
                ["afplay", "-v", str(float(self.config.volume)), wav_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            with self._play_lock:
                self._play_process = process
            try:
                process.wait()
            finally:
                with self._play_lock:
                    if self._play_process is process:
                        self._play_process = None
            return
        raise RuntimeError("Playback is only configured for macOS afplay.")

    def stop_playback(self) -> None:
        with self._play_lock:
            process = self._play_process
            synthesis_process = self._synthesis_process
            conversion_process = self._conversion_process
            self._play_process = None
            self._synthesis_process = None
            self._conversion_process = None
            self._stop_token += 1
        self._terminate_process(process)
        self._terminate_process(synthesis_process)
        self._terminate_process(conversion_process)

    def _terminate_process(self, process: subprocess.Popen | None) -> None:
        if process is None or process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=1.0)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

    def _raise_if_stopped(self, start_token: int) -> None:
        with self._play_lock:
            if self._stop_token != start_token:
                raise RuntimeError("TTS synthesis stopped")

    def speak(
        self,
        text: str,
        speaker_id: Optional[int] = None,
        output_path: Optional[str] = None,
        play: bool = True,
        voice: Optional[str] = None,
    ) -> str:
        with self._speech_lock:
            with self._play_lock:
                start_token = self._stop_token
            path = self.synthesize_to_wav(
                text,
                output_path=output_path,
                speaker_id=speaker_id,
                voice=voice,
            )
            if play:
                with self._play_lock:
                    if self._stop_token != start_token:
                        return path
                try:
                    self.play_wav(path)
                except Exception:
                    pass
            return path


_DEFAULT_TTS: TTSPipeline | None = None


def get_default_tts() -> TTSPipeline:
    global _DEFAULT_TTS
    if _DEFAULT_TTS is None:
        _DEFAULT_TTS = TTSPipeline()
    return _DEFAULT_TTS


def _cli() -> None:
    tts = get_default_tts()
    tts.load()
    print(tts.status)
    text = "Hello, I am Sylvia. This is a soft female voice test on your Mac."
    out = tts.speak(text, play=False)
    print("Saved:", out)


if __name__ == "__main__":
    _cli()
