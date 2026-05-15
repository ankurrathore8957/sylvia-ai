from __future__ import annotations

import os
import platform
import queue
import re
import tempfile
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np  # type: ignore
import scipy.io.wavfile as wav  # type: ignore
import sounddevice as sd  # type: ignore
from faster_whisper import WhisperModel  # type: ignore

try:
    import webrtcvad  # type: ignore
except Exception:
    webrtcvad = None


@dataclass
class MicConfig:
    sample_rate: int = 16000
    chunk_size: int = 480  # 30 ms at 16 kHz, required by WebRTC VAD
    silence_threshold: float = 0.006  # minimum RMS threshold floor
    silence_duration: float = 0.9  # seconds of silence to end an utterance
    min_utterance: float = 0.45  # seconds
    max_utterance: float = 10.0  # force flush long speech (seconds)
    calibrate_seconds: float = 0.45  # estimate ambient noise at start
    threshold_multiplier: float = 2.8  # threshold = max(floor, ambient * multiplier)
    language: Optional[str] = "en"  # force English to avoid random auto-detected languages
    model_name: str = "base"
    compute_type: str = "int8"
    input_device: Optional[int] = None
    prefer_builtin_mac_mic: bool = True
    speech_start_cooldown: float = 1.2
    vad_aggressiveness: int = 3
    vad_frame_ms: int = 30
    voice_start_frames: int = 7
    min_voiced_frames: int = 5


class MicPipeline:
    """
    Speech-to-text pipeline:
    - runs sounddevice InputStream in a background thread
    - detects utterances with adaptive local VAD
    - calls on_speech_start() as soon as user speech starts
    - transcribes with faster-whisper
    - calls on_text(text) for each utterance
    """

    def __init__(self, config: MicConfig | None = None) -> None:
        self.config = config or MicConfig()
        self._audio_queue: "queue.Queue[np.ndarray]" = queue.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._model: WhisperModel | None = None
        self._model_lock = threading.Lock()
        self._status = "Mic: idle"
        self._debug = ""
        self._last_error = ""

    @property
    def status(self) -> str:
        return self._status

    @property
    def debug(self) -> str:
        return self._debug

    @property
    def last_error(self) -> str:
        return self._last_error

    def load_model(self) -> None:
        if self._model is not None:
            return
        with self._model_lock:
            if self._model is not None:
                return
            self._status = "Mic: loading speech model..."
            self._model = WhisperModel(self.config.model_name, compute_type=self.config.compute_type)
            self._status = "Mic: ready"

    def start(
        self,
        on_text: Callable[[str], None],
        on_speech_start: Callable[[], None] | None = None,
    ) -> None:
        if self._thread and self._thread.is_alive():
            self._status = "Mic: already listening"
            return
        self._stop.clear()
        self._last_error = ""
        self._thread = threading.Thread(
            target=self._run,
            args=(on_text, on_speech_start),
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(
        self,
        on_text: Callable[[str], None],
        on_speech_start: Callable[[], None] | None,
    ) -> None:
        self._status = "Mic: starting..."
        try:
            self.load_model()
        except Exception as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"
            self._status = "Mic: speech model load failed"
            return

        # Drain queue
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except Exception:
                break

        def callback(indata, frames, time_info, status):
            if self._stop.is_set():
                return
            if status:
                self._debug = f"stream status={status}"
            try:
                # indata shape: (frames, channels)
                data = np.asarray(indata, dtype=np.float32).copy()
                self._audio_queue.put_nowait(data)
            except Exception:
                pass

        def rms(x: np.ndarray) -> float:
            if x.size == 0:
                return 0.0
            # x is float32 in [-1, 1]
            return float(np.sqrt(np.mean(np.square(x))))

        def is_human_voice(chunk: np.ndarray, threshold: float) -> tuple[bool, float, str]:
            level = rms(chunk)
            if level < threshold:
                return False, level, "quiet"

            if webrtcvad is None:
                return True, level, "rms-fallback"

            frame_len = int(sr * self.config.vad_frame_ms / 1000)
            samples = np.asarray(chunk, dtype=np.float32).reshape(-1)
            total = 0
            voiced = 0

            for start in range(0, max(0, samples.shape[0] - frame_len + 1), frame_len):
                frame = samples[start : start + frame_len]
                if frame.shape[0] != frame_len:
                    continue
                pcm16 = np.clip(frame, -1.0, 1.0)
                pcm16 = (pcm16 * 32767).astype(np.int16).tobytes()
                total += 1
                try:
                    if vad_detector.is_speech(pcm16, sr):
                        voiced += 1
                except Exception:
                    pass

            if total == 0:
                return False, level, "vad=0/0"

            is_voice = voiced > 0 and (voiced / total) >= 0.60
            return is_voice, level, f"vad={voiced}/{total}"

        buffer: list[np.ndarray] = []
        last_voice_t = time.time()
        last_speech_start_notify_t = 0.0
        utterance_start_t: float | None = None
        voiced_run = 0
        utterance_voiced_frames = 0
        sr = self.config.sample_rate
        device = self._resolve_input_device()
        if device is None:
            self._last_error = "No input microphone device found by sounddevice."
            self._status = "Mic: no input device"
            return

        vad_detector = webrtcvad.Vad(int(self.config.vad_aggressiveness)) if webrtcvad else None
        vad_label = "WebRTC VAD" if vad_detector is not None else "RMS fallback"
        self._status = f"Mic: listening ({vad_label})"
        try:
            with sd.InputStream(
                device=device,
                samplerate=sr,
                channels=1,
                blocksize=self.config.chunk_size,
                dtype="float32",
                callback=callback,
            ):
                # --- calibrate ambient noise floor ---
                calib_chunks = max(1, int((sr * max(0.1, self.config.calibrate_seconds)) / self.config.chunk_size))
                noise_samples: list[float] = []
                for _ in range(calib_chunks):
                    if self._stop.is_set():
                        break
                    try:
                        chunk = self._audio_queue.get(timeout=0.6)
                    except Exception:
                        continue
                    noise_samples.append(rms(chunk))
                ambient = float(np.median(noise_samples)) if noise_samples else 0.0
                dyn_thresh = max(float(self.config.silence_threshold), ambient * float(self.config.threshold_multiplier))
                pre_roll_samples = int(sr * 0.6)
                self._debug = f"ambient={ambient:.4f} threshold={dyn_thresh:.4f} {vad_label}"

                while not self._stop.is_set():
                    try:
                        chunk = self._audio_queue.get(timeout=0.25)
                    except Exception:
                        continue

                    is_voice, level, vad_debug = is_human_voice(chunk, dyn_thresh)
                    now = time.time()
                    if is_voice:
                        voiced_run += 1
                    else:
                        voiced_run = 0
                    self._debug = (
                        f"level={level:.4f} threshold={dyn_thresh:.4f} "
                        f"{vad_debug} run={voiced_run} device={device}"
                    )

                    if utterance_start_t is None:
                        buffer.append(chunk)
                        total = sum(x.shape[0] for x in buffer)
                        while total > pre_roll_samples and buffer:
                            total -= buffer.pop(0).shape[0]

                        if voiced_run < self.config.voice_start_frames:
                            continue

                        utterance_start_t = now
                        utterance_voiced_frames = voiced_run
                        last_voice_t = now

                        if (
                            on_speech_start is not None
                            and now - last_speech_start_notify_t >= self.config.speech_start_cooldown
                        ):
                            last_speech_start_notify_t = now
                            self._debug = (
                                f"speech_start level={level:.4f} threshold={dyn_thresh:.4f} "
                                f"{vad_debug}"
                            )
                            try:
                                on_speech_start()
                            except Exception:
                                pass
                        continue

                    buffer.append(chunk)
                    if is_voice:
                        last_voice_t = now
                        utterance_voiced_frames += 1

                    # force flush on long utterance
                    if utterance_start_t is not None and (now - utterance_start_t) >= self.config.max_utterance:
                        last_voice_t = 0.0  # trigger flush below

                    # end condition: silence gap after speech started
                    if utterance_start_t is not None and (now - last_voice_t) >= self.config.silence_duration:
                        audio = np.concatenate(buffer, axis=0)
                        buffer.clear()
                        utterance_start_t = None
                        voiced_frames = utterance_voiced_frames
                        utterance_voiced_frames = 0
                        voiced_run = 0

                        # too short? ignore
                        if audio.shape[0] < int(sr * self.config.min_utterance):
                            continue
                        if voiced_frames < self.config.min_voiced_frames:
                            self._debug = f"ignored weak speech voiced_frames={voiced_frames}"
                            continue

                        text = self._transcribe(audio, sr)
                        if text:
                            self._debug = f"transcribed={text}"
                            try:
                                on_text(text)
                            except Exception:
                                pass
        except Exception as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"
            self._status = "Mic: stream error"
        finally:
            if self._stop.is_set():
                self._status = "Mic: stopped"

    def _resolve_input_device(self) -> Optional[int]:
        if self.config.input_device is not None:
            return self.config.input_device
        devices = None
        try:
            devices = sd.query_devices()
        except Exception as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"

        if (
            self.config.prefer_builtin_mac_mic
            and platform.system() == "Darwin"
            and devices is not None
        ):
            preferred_names = (
                "macbook",
                "built-in microphone",
                "built in microphone",
                "studio display microphone",
            )
            for index, device in enumerate(devices):
                if int(device.get("max_input_channels", 0)) <= 0:
                    continue
                name = str(device.get("name", "")).lower()
                if any(preferred in name for preferred in preferred_names):
                    return index

        try:
            default_input = sd.default.device[0]
            if default_input is not None and int(default_input) >= 0:
                return int(default_input)
        except Exception:
            pass
        try:
            if devices is None:
                devices = sd.query_devices()
            for index, device in enumerate(devices):
                if int(device.get("max_input_channels", 0)) > 0:
                    return index
        except Exception as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"
        return None

    def _transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        if self._model is None:
            return ""

        # Write to wav for maximum compatibility with faster-whisper.
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
        tmp_path = tmp.name
        tmp.close()
        try:
            wav.write(tmp_path, sample_rate, audio)
            segments, _ = self._model.transcribe(
                tmp_path,
                language=self.config.language,
                beam_size=1,
                condition_on_previous_text=False,
                temperature=0.0,
                no_speech_threshold=0.65,
                log_prob_threshold=-1.0,
            )
            text_parts: list[str] = []
            for seg in segments:
                text = (seg.text or "").strip()
                if not text:
                    continue
                no_speech = getattr(seg, "no_speech_prob", 0.0) or 0.0
                avg_logprob = getattr(seg, "avg_logprob", 0.0) or 0.0
                if no_speech > 0.85 or avg_logprob < -1.35:
                    continue
                text_parts.append(text)
            return self._clean_transcript(" ".join(text_parts))
        finally:
            try:
                os.remove(tmp_path)
            except Exception:
                pass

    def _clean_transcript(self, text: str) -> str:
        clean = " ".join((text or "").split()).strip()
        if not clean:
            return ""

        lower = clean.lower().strip(" .!?")
        noise_hallucinations = {
            "thank you",
            "thanks for watching",
            "you",
            "bye",
            "bye bye",
            "music",
        }
        if lower in noise_hallucinations:
            return ""
        if len(re.findall(r"[a-zA-Z0-9]", clean)) < 2:
            return ""
        return clean


_DEFAULT_MIC: MicPipeline | None = None


def get_default_mic() -> MicPipeline:
    global _DEFAULT_MIC
    if _DEFAULT_MIC is None:
        _DEFAULT_MIC = MicPipeline()
    return _DEFAULT_MIC


def _cli() -> None:
    mic = get_default_mic()
    print("🎤 Starting mic... (Ctrl+C to stop)")

    def on_text(text: str) -> None:
        print("📝", text)

    mic.start(on_text)
    try:
        while True:
            time.sleep(0.2)
    except KeyboardInterrupt:
        mic.stop()
        time.sleep(0.4)
        print("\nStopped.")


if __name__ == "__main__":
    _cli()
