import math
import os
import platform
import queue
import random
import socket
import subprocess
import sys
import threading
import time
from difflib import SequenceMatcher
try:
    import tkinter as tk
except ModuleNotFoundError as exc:
    raise SystemExit(
        "Tkinter is not installed. Install it and retry (e.g., 'python3-tk' on Debian/Ubuntu)."
    ) from exc
from datetime import datetime
from typing import Callable

try:
    import psutil  # type: ignore
except Exception:
    psutil = None

try:
    import brain  # type: ignore
    _BRAIN_IMPORT_ERROR = None
except Exception as exc:
    brain = None
    _BRAIN_IMPORT_ERROR = str(exc)

try:
    import mic  # type: ignore
    _MIC_IMPORT_ERROR = None
except Exception as exc:
    mic = None
    _MIC_IMPORT_ERROR = str(exc)

try:
    import tts  # type: ignore
    _TTS_IMPORT_ERROR = None
except Exception as exc:
    tts = None
    _TTS_IMPORT_ERROR = str(exc)

try:
    import weather as weather_service  # type: ignore
    _WEATHER_IMPORT_ERROR = None
except Exception as exc:
    weather_service = None
    _WEATHER_IMPORT_ERROR = str(exc)


class SylviaInterfaceUI:
    FRAME_MS = 24
    MAX_CHAT_MESSAGES = 8
    MAX_MESSAGE_CHARS = 900

    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("SYLVIA Voice Interface")
        self.root.geometry("1320x820")
        self.root.minsize(980, 640)
        self.root.configure(bg="#020202")
        self.root.resizable(True, True)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self._ui_thread_id = threading.get_ident()
        self._ui_tasks: "queue.Queue[Callable[[], None]]" = queue.Queue()
        self._closing = False

        self.canvas = tk.Canvas(
            self.root,
            bg="#020202",
            highlightthickness=0,
            bd=0,
            relief="flat",
        )
        self.canvas.pack(fill="both", expand=True)

        self.width = 1320
        self.height = 820
        self.center = (self.width / 2, self.height / 2)

        self.listening = False
        self.last_frame_time = time.perf_counter()
        self.runtime_start = time.perf_counter()
        self.popup_until = 0.0
        self.popup_title = "SYSTEM READY"
        self.popup_text = "Standby"

        self.time_text = "--:--:--"
        self.date_text = "-- --- ----"
        self.battery_text = "PWR: Detecting..."
        self.weather_text = "ENV: Fetching..."
        self.device_text = f"{platform.system()} {platform.release()}"
        self.fps_text = "FPS 60"
        self.stats_text = "CPU --%  |  RAM --%  |  DISK --%"
        self.network_text = "NET: Detecting..."
        self._last_ui_error = ""

        self._brain = brain.get_default_brain() if brain else None
        self._mic = mic.get_default_mic() if mic else None
        self._tts = tts.get_default_tts() if tts else None

        if self._brain:
            self.model_status_text = self._brain.status
        else:
            self.model_status_text = f"AI: OFFLINE ({_BRAIN_IMPORT_ERROR})" if _BRAIN_IMPORT_ERROR else "AI: OFFLINE"
        self._last_assistant_message = ""
        self._placeholder_counter = 0
        self._chat_overlay_geom: tuple[int, int, int, int] | None = None
        self._chat_history: list[tuple[str, str]] = []
        self._generation_active = False
        self._active_generation_id = 0
        self._active_stop_event: threading.Event | None = None
        self._active_placeholder_id: int | None = None
        self._tts_job_id = 0
        self._tts_playback_active = False
        self._current_tts_text = ""

        self.frame_samples = []
        self.scan_line_offset = 0.0
        self.hex_rotation = 0.0
        self.data_stream_offset = 0.0
        self.ring_rotation = [0.0, 0.0, 0.0]  # Three concentric rings
        self.pulse_rings = []  # Energy pulse rings expanding
        self.waveform_data = [0.0] * 64  # Audio waveform simulation

        # SYLVIA color palette
        self.arc_blue = "#00d4ff"
        self.arc_glow = "#00a8cc"
        self.arc_dim = "#006b7d"
        self.hud_white = "#e8f4f8"
        self.hud_dim = "#7a9aaa"
        self.hud_dark = "#3a5a6a"
        self.panel_bg = "#080c10"
        self.panel_border = "#1a2a35"
        self.panel_active = "#00d4ff"
        self.warning_red = "#ff3333"
        self.success_green = "#00ff88"
        self.bg_main = "#020202"
        self.bg_grid = "#0a0f14"

        self.orb_palette = [
            "#00d4ff",
            "#00a8cc",
            "#0088aa",
            "#00ff88",
            "#00ccff",
            "#66e5ff",
            "#0099bb",
        ]

        self.data_particles = []
        self._build_particles()
        self._build_chat_widgets()

        self.root.bind("<Configure>", self.on_resize)
        self.canvas.bind("<Button-1>", self.on_click)

        self.fetch_weather_async()
        self.update_system_info()
        self.drain_ui_tasks()
        self.init_pipelines_async()
        self.animate()

    def run_on_ui(self, callback: Callable[[], None]) -> None:
        if self._closing:
            return
        if threading.get_ident() == self._ui_thread_id:
            callback()
        else:
            self._ui_tasks.put(callback)

    def drain_ui_tasks(self) -> None:
        if self._closing:
            return
        while True:
            try:
                callback = self._ui_tasks.get_nowait()
            except queue.Empty:
                break
            try:
                callback()
            except Exception:
                pass
        self.root.after(50, self.drain_ui_tasks)

    def on_close(self) -> None:
        self._closing = True
        try:
            self.stop_voice_engine()
        except Exception:
            pass
        try:
            self._stop_tts_playback()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    def _build_particles(self) -> None:
        self.data_particles.clear()
        for _ in range(64):
            self.data_particles.append(
                {
                    "x": random.random(),
                    "y": random.random(),
                    "size": random.uniform(0.3, 1.8),
                    "speed": random.uniform(0.02, 0.12),
                    "drift": random.uniform(3.0, 14.0),
                    "phase": random.uniform(0.0, math.tau),
                    "depth": random.uniform(0.15, 1.0),
                    "type": random.choice(["dot", "line", "hex", "cross"]),
                }
            )

    def on_resize(self, event: tk.Event) -> None:
        if event.widget is not self.root:
            return
        self.width = max(1, event.width)
        self.height = max(1, event.height)
        self.center = (self.width / 2, self.height / 2)

    def on_click(self, event: tk.Event) -> None:
        cx, cy = self.center
        hit_r = min(self.width, self.height) * 0.14
        dx = event.x - cx
        dy = event.y - cy
        if dx * dx + dy * dy <= hit_r * hit_r:
            self.listening = not self.listening
            self.popup_until = time.perf_counter() + 2.5
            if self.listening:
                self.popup_title = "AUDIO INPUT ACTIVE"
                self.popup_text = "Listening..."
                self.start_voice_engine()
                # Add energy pulse effect
                self.pulse_rings.append({"radius": 0.0, "alpha": 1.0, "speed": 2.0})
            else:
                self.popup_title = "AUDIO INPUT STANDBY"
                self.popup_text = "Idle"
                self.stop_voice_engine()

    def update_system_info(self) -> None:
        if self._closing:
            return
        now = datetime.now()
        self.time_text = now.strftime("%H:%M:%S")
        self.date_text = now.strftime("%d %b %Y").upper()
        self.battery_text = self.get_battery_text()
        self.update_runtime_stats()
        if not self._closing:
            self.root.after(1000, self.update_system_info)

    def update_runtime_stats(self) -> None:
        cpu_pct = None
        mem_pct = None
        disk_pct = None
        if psutil:
            try:
                cpu_pct = psutil.cpu_percent(interval=None)
                mem_pct = psutil.virtual_memory().percent
                disk_pct = psutil.disk_usage(os.path.abspath(os.sep)).percent
            except Exception:
                cpu_pct = None
        if cpu_pct is None:
            self.stats_text = "CPU --%  |  RAM --%  |  DISK --%"
        else:
            self.stats_text = f"CPU {int(cpu_pct)}%  |  RAM {int(mem_pct or 0)}%  |  DISK {int(disk_pct or 0)}%"
        self.network_text = self.get_network_text()

    def get_battery_text(self) -> str:
        try:
            if psutil:
                battery = psutil.sensors_battery()
                if battery:
                    mode = "AC" if battery.power_plugged else "BAT"
                    return f"PWR: {mode} {int(battery.percent)}%"
            if sys.platform == "darwin":
                output = subprocess.check_output(
                    ["pmset", "-g", "batt"],
                    text=True,
                    stderr=subprocess.DEVNULL,
                )
                state = "AC" if "AC Power" in output else "BAT"
                for line in output.splitlines():
                    if "%" in line:
                        percent = line.split("\t")[-1].split(";")[0].strip()
                        return f"PWR: {state} {percent}"
        except Exception:
            pass
        return "PWR: N/A"

    def get_network_text(self) -> str:
        try:
            if psutil:
                stats = psutil.net_if_stats()
                addrs = psutil.net_if_addrs()
                active_ifaces = []
                for name, s in stats.items():
                    if not s.isup:
                        continue
                    if name.lower().startswith(("lo", "loopback")):
                        continue
                    active_ifaces.append(name)
                ip = None
                for name in active_ifaces:
                    for addr in addrs.get(name, []):
                        if getattr(addr, "family", None) == socket.AF_INET:
                            if addr.address and not addr.address.startswith("127."):
                                ip = addr.address
                                break
                    if ip:
                        break
                if active_ifaces:
                    suffix = f"  {ip}" if ip else ""
                    return f"NET: ONLINE{suffix}"
                return "NET: OFFLINE"
        except Exception:
            pass
        try:
            with socket.create_connection(("1.1.1.1", 53), timeout=0.5):
                pass
            return "NET: ONLINE"
        except Exception:
            return "NET: OFFLINE"

    def fetch_weather_async(self) -> None:
        def worker() -> None:
            weather_text = "ENV: OFFLINE"
            try:
                if self._internet_available() and weather_service is not None:
                    latitude, longitude = weather_service.get_precise_location(timeout=8)
                    location = weather_service.reverse_geocode(latitude, longitude)
                    current = weather_service.fetch_weather(latitude, longitude)
                    weather_text = self._format_weather_text(location, current)
                elif _WEATHER_IMPORT_ERROR:
                    weather_text = "ENV: OFFLINE"
            except Exception:
                weather_text = "ENV: OFFLINE"
            finally:
                def apply_weather() -> None:
                    self.weather_text = self._sanitize_weather_text(weather_text)
                    if not self._closing:
                        self.root.after(900000, self.fetch_weather_async)
                self.run_on_ui(apply_weather)
        threading.Thread(target=worker, daemon=True).start()

    def _internet_available(self) -> bool:
        try:
            with socket.create_connection(("1.1.1.1", 53), timeout=1.2):
                pass
            return True
        except Exception:
            return False

    def _format_weather_text(self, location: dict, current: dict) -> str:
        city = (
            location.get("city")
            or location.get("area")
            or location.get("state")
            or ""
        )
        temperature = current.get("temperature_2m", "N/A")
        humidity = current.get("relative_humidity_2m", "N/A")
        wind_speed = current.get("wind_speed_10m", "N/A")

        if city and city != "Unknown":
            return f"ENV: {city} {temperature}C H{humidity}% W{wind_speed}km/h"
        return f"ENV: {temperature}C H{humidity}% W{wind_speed}km/h"

    def _sanitize_weather_text(self, text: str) -> str:
        clean = " ".join(str(text or "").split())
        lower = clean.lower()
        if not clean or "<!doctype" in lower or "<html" in lower or "</html" in lower:
            return "ENV: OFFLINE"
        if not clean.startswith("ENV:"):
            clean = f"ENV: {clean}"
        return clean[:80]

    def init_pipelines_async(self) -> None:
        if self._brain is None:
            self.model_status_text = "AI: OFFLINE"
        else:
            def brain_loader() -> None:
                try:
                    self._brain.load()
                finally:
                    self.run_on_ui(lambda: setattr(self, "model_status_text", self._brain.status))
            threading.Thread(target=brain_loader, daemon=True).start()
        if self._mic is None:
            return
        def mic_loader() -> None:
            try:
                self._mic.load_model()
            except Exception:
                pass
        threading.Thread(target=mic_loader, daemon=True).start()

    def _build_chat_widgets(self) -> None:
        self.chat_frame = tk.Frame(self.root, bg=self.panel_bg, bd=0, highlightthickness=0)
        self.chat_frame.pack_propagate(False)

        header = tk.Frame(self.chat_frame, bg=self.panel_bg)
        header.pack(fill="x", padx=8, pady=(6, 3))

        self.chat_title = tk.Label(
            header,
            text="COMMS",
            bg=self.panel_bg,
            fg=self.hud_white,
            font=("Courier New", 10, "bold"),
        )
        self.chat_title.pack(side="left")

        self.chat_model_label = tk.Label(
            header,
            text=self.model_status_text,
            bg=self.panel_bg,
            fg=self.hud_dim,
            font=("Courier New", 8),
        )
        self.chat_model_label.pack(side="right")

        body = tk.Frame(self.chat_frame, bg=self.panel_bg)
        body.pack(fill="both", expand=True, padx=8, pady=(0, 4))

        self.chat_output = tk.Text(
            body,
            wrap="word",
            bg="#060a0e",
            fg=self.hud_white,
            insertbackground=self.arc_blue,
            bd=0,
            highlightthickness=1,
            highlightbackground=self.panel_border,
            highlightcolor=self.arc_blue,
            font=("Courier New", 9),
        )
        self.chat_output.configure(state="disabled")
        self.chat_output.tag_configure("user_label", foreground=self.arc_blue, font=("Courier New", 9, "bold"))
        self.chat_output.tag_configure("assistant_label", foreground=self.success_green, font=("Courier New", 9, "bold"))
        self.chat_output.tag_configure("user_text", foreground=self.hud_white)
        self.chat_output.tag_configure("assistant_text", foreground=self.hud_dim)
        self.chat_output.tag_configure("placeholder", foreground=self.hud_dark)

        scrollbar = tk.Scrollbar(body, command=self.chat_output.yview, bg=self.panel_bg, troughcolor=self.panel_bg)
        self.chat_output.configure(yscrollcommand=scrollbar.set)

        self.chat_output.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        footer = tk.Frame(self.chat_frame, bg=self.panel_bg)
        footer.pack(fill="x", padx=8, pady=(0, 8))

        self.chat_input = tk.Text(
            footer,
            height=2,
            wrap="word",
            bg="#060a0e",
            fg=self.hud_white,
            insertbackground=self.arc_blue,
            bd=0,
            highlightthickness=1,
            highlightbackground=self.panel_border,
            highlightcolor=self.arc_blue,
            font=("Courier New", 9),
        )
        self.chat_input.pack(side="left", fill="x", expand=True, padx=(0, 6))

        buttons = tk.Frame(footer, bg=self.panel_bg)
        buttons.pack(side="right")

        self.send_button = tk.Button(
            buttons,
            text="TRANSMIT",
            command=self.on_send_clicked,
            bg=self.panel_bg,
            fg=self.arc_blue,
            activebackground="#0d1520",
            activeforeground=self.arc_blue,
            bd=1,
            relief="solid",
            padx=12,
            pady=6,
            font=("Courier New", 9, "bold"),
            highlightbackground=self.panel_border,
            highlightthickness=1,
        )
        self.send_button.pack(fill="x", pady=(0, 4))

        self.copy_button = tk.Button(
            buttons,
            text="COPY",
            command=self.copy_last_reply,
            bg="#060a0e",
            fg=self.hud_dim,
            activebackground=self.panel_bg,
            activeforeground=self.hud_white,
            bd=1,
            relief="solid",
            padx=12,
            pady=6,
            font=("Courier New", 8, "bold"),
            highlightbackground=self.panel_border,
            highlightthickness=1,
        )
        self.copy_button.pack(fill="x")

        self.chat_input.bind("<Return>", self._on_enter_send)
        self.chat_input.bind("<Shift-Return>", self._on_shift_enter_newline)
        self._chat_append("assistant", "[SYSTEM] SYLVIA interface initialized.\n")
        self.chat_frame.place(x=-2000, y=-2000, width=10, height=10)

    def _on_enter_send(self, event: tk.Event) -> str:
        self.on_send_clicked()
        return "break"

    def _on_shift_enter_newline(self, event: tk.Event) -> None:
        self.chat_input.insert("insert", "\n")

    def on_send_clicked(self) -> None:
        if self._generation_active or self._tts_playback_active:
            self.stop_current_response()
            return
        text = self.chat_input.get("1.0", "end").strip()
        if not text:
            return
        self.chat_input.delete("1.0", "end")
        self._send_text(text)

    def _send_text(self, text: str) -> None:
        text = self._clean_chat_text(text)
        self._chat_append("user", text + "\n")
        history_before = self._bounded_history()
        self._chat_history.append(("user", text))
        self._trim_chat_history()
        placeholder_id = self._append_assistant_placeholder()
        stop_event = threading.Event()
        self._active_generation_id += 1
        generation_id = self._active_generation_id
        self._active_stop_event = stop_event
        self._active_placeholder_id = placeholder_id
        self._generation_active = True
        self._set_send_button_generating(True)
        self._stop_tts_playback()

        def worker(user_text: str, pid: int, history: list[tuple[str, str]], gid: int, stopper: threading.Event) -> None:
            reply = self._generate_reply(user_text, history, stopper)
            if stopper.is_set() or reply is None:
                return

            def apply_reply() -> None:
                if gid != self._active_generation_id or stopper.is_set():
                    return
                self._replace_assistant_placeholder(pid, reply + "\n")
                self._chat_history.append(("assistant", reply))
                self._trim_chat_history()
                self._generation_active = False
                self._active_stop_event = None
                self._active_placeholder_id = None
                self._tts_job_id += 1
                tts_job_id = self._tts_job_id
                if self._tts is not None and reply.strip():
                    self._tts_playback_active = True
                    self._current_tts_text = reply
                    self._set_send_button_generating(True)
                    threading.Thread(
                        target=self._speak_reply,
                        args=(reply, tts_job_id),
                        daemon=True,
                    ).start()
                else:
                    self._set_send_button_generating(False)
            self.run_on_ui(apply_reply)
        threading.Thread(target=worker, args=(text, placeholder_id, history_before, generation_id, stop_event), daemon=True).start()

    def _generate_reply(self, user_text: str, history: list[tuple[str, str]] | None = None, stop_event: threading.Event | None = None) -> str | None:
        if self._brain is None:
            return "[ERROR] AI module not loaded."
        self.run_on_ui(lambda: setattr(self, "model_status_text", self._brain.status))
        if hasattr(self._brain, "generate_cancellable"):
            reply = self._brain.generate_cancellable(user_text, history=history or [], stop_event=stop_event)
        else:
            reply = self._brain.generate(user_text, history=history or [])
        if reply is None:
            return None
        return self._clean_chat_text(reply)

    def stop_current_response(self) -> None:
        if not self._generation_active:
            self._stop_tts_playback()
            self._tts_playback_active = False
            self._set_send_button_generating(False)
            return
        stopper = self._active_stop_event
        if stopper is not None:
            stopper.set()
        pid = self._active_placeholder_id
        self._generation_active = False
        self._active_generation_id += 1
        self._active_stop_event = None
        self._active_placeholder_id = None
        self._set_send_button_generating(False)
        self._stop_tts_playback()
        self._tts_playback_active = False
        if pid is not None:
            self._replace_assistant_placeholder(pid, "[STOPPED]\n")

    def _set_send_button_generating(self, generating: bool) -> None:
        if not hasattr(self, "send_button"):
            return
        if generating:
            self.send_button.configure(
                text="STOP",
                bg=self.warning_red,
                fg="#ffffff",
                activebackground="#8f1111",
                activeforeground="#ffffff",
                highlightbackground=self.warning_red,
                highlightcolor="#ffffff",
                relief="raised",
                bd=2,
            )
        else:
            self.send_button.configure(
                text="TRANSMIT",
                bg="#060a0e",
                fg=self.arc_blue,
                activebackground="#0d1520",
                activeforeground=self.arc_blue,
                highlightbackground=self.panel_border,
                highlightcolor=self.arc_blue,
                relief="solid",
                bd=1,
            )

    def _clean_chat_text(self, text: str) -> str:
        text = " ".join((text or "").replace("\r", "\n").split())
        return text[: self.MAX_MESSAGE_CHARS].strip()

    def _bounded_history(self) -> list[tuple[str, str]]:
        return [
            (role, self._clean_chat_text(content))
            for role, content in self._chat_history[-self.MAX_CHAT_MESSAGES :]
            if content.strip()
        ]

    def _trim_chat_history(self) -> None:
        if len(self._chat_history) > self.MAX_CHAT_MESSAGES:
            self._chat_history = self._chat_history[-self.MAX_CHAT_MESSAGES :]

    def _speak_reply(self, reply: str, job_id: int | None = None) -> None:
        if not reply.strip() or self._tts is None:
            return
        if job_id is not None and job_id != self._tts_job_id:
            return
        try:
            speech_text = self._clean_chat_text(reply)[:700]
            if job_id is not None and job_id != self._tts_job_id:
                return
            self._tts.speak(speech_text, play=True)
        except Exception:
            if _TTS_IMPORT_ERROR:
                self.run_on_ui(lambda: self._chat_append("assistant", f"[TTS ERROR] {_TTS_IMPORT_ERROR}\n"))
        finally:
            def clear_tts_state() -> None:
                if job_id is not None and job_id != self._tts_job_id:
                    return
                self._tts_playback_active = False
                self._current_tts_text = ""
                if not self._generation_active:
                    self._set_send_button_generating(False)
            self.run_on_ui(clear_tts_state)

    def _stop_tts_playback(self) -> None:
        self._tts_job_id += 1
        self._tts_playback_active = False
        self._current_tts_text = ""
        if self._tts is None:
            return
        stop_playback = getattr(self._tts, "stop_playback", None)
        if callable(stop_playback):
            try:
                stop_playback()
            except Exception:
                pass

    def _get_mic_status_text(self) -> str:
        if self._mic is None:
            return "MIC: OFFLINE"
        return getattr(self._mic, "status", "MIC: UNKNOWN")

    def _get_mic_debug_text(self) -> str:
        if self._mic is None:
            return "SENSITIVITY: ADAPTIVE"
        last_error = getattr(self._mic, "last_error", "")
        if last_error:
            return f"ERR: {last_error[:54]}"
        debug = getattr(self._mic, "debug", "")
        return debug[:64] if debug else "SENSITIVITY: ADAPTIVE"

    def _chat_append(self, role: str, text: str) -> None:
        self.chat_output.configure(state="normal")
        if role == "user":
            self.chat_output.insert("end", "> ", ("user_label",))
            self.chat_output.insert("end", text, ("user_text",))
        else:
            self.chat_output.insert("end", "[SYLVIA] ", ("assistant_label",))
            self.chat_output.insert("end", text, ("assistant_text",))
            self._last_assistant_message = text.strip()
        self.chat_output.configure(state="disabled")
        self.chat_output.see("end")

    def _append_assistant_placeholder(self) -> int:
        self._placeholder_counter += 1
        pid = self._placeholder_counter
        self.chat_output.configure(state="normal")
        self.chat_output.insert("end", "[SYLVIA] ", ("assistant_label",))
        start_mark = f"reply_start_{pid}"
        end_mark = f"reply_end_{pid}"
        self.chat_output.mark_set(start_mark, "end-1c")
        self.chat_output.insert("end", "PROCESSING...\n", ("assistant_text", "placeholder"))
        self.chat_output.mark_set(end_mark, "end-1c")
        self.chat_output.configure(state="disabled")
        self.chat_output.see("end")
        return pid

    def _replace_assistant_placeholder(self, placeholder_id: int, new_text: str) -> None:
        start_mark = f"reply_start_{placeholder_id}"
        end_mark = f"reply_end_{placeholder_id}"
        self.chat_output.configure(state="normal")
        try:
            self.chat_output.delete(start_mark, end_mark)
            self.chat_output.insert(start_mark, new_text, ("assistant_text",))
            self.chat_output.mark_unset(start_mark)
            self.chat_output.mark_unset(end_mark)
            self._last_assistant_message = new_text.strip()
        except Exception:
            self.chat_output.insert("end", "[SYLVIA] ", ("assistant_label",))
            self.chat_output.insert("end", new_text, ("assistant_text",))
            self._last_assistant_message = new_text.strip()
        self.chat_output.configure(state="disabled")
        self.chat_output.see("end")

    def start_voice_engine(self) -> None:
        if self._mic is None:
            self.popup_until = time.perf_counter() + 2.5
            self.popup_title = "AUDIO OFFLINE"
            self.popup_text = "mic.py unavailable"
            if _MIC_IMPORT_ERROR:
                self._chat_append("assistant", f"[MIC ERROR] {_MIC_IMPORT_ERROR}\n")
            return
        def on_text(text: str) -> None:
            self.run_on_ui(lambda t=text: self._handle_voice_text(t))
        def on_speech_start() -> None:
            self._handle_voice_start()
        self._chat_append("assistant", "[AUDIO] Input active. Speak clearly...\n")
        threading.Thread(
            target=lambda: self._mic.start(on_text, on_speech_start=on_speech_start),
            daemon=True,
        ).start()
        self.root.after(1800, self._report_mic_start_status)

    def _report_mic_start_status(self) -> None:
        if not self.listening or self._mic is None:
            return
        status = getattr(self._mic, "status", "")
        last_error = getattr(self._mic, "last_error", "")
        if "error" in status.lower() or "failed" in status.lower() or "device" in status.lower():
            detail = f" ({last_error})" if last_error else ""
            self._chat_append("assistant", f"[{status.upper()}]{detail}\n")

    def stop_voice_engine(self) -> None:
        if self._mic is None:
            return
        self._mic.stop()

    def _handle_voice_text(self, text: str) -> None:
        if not self.listening:
            return
        text = self._clean_chat_text(text)
        if not text:
            return

        if self._tts_playback_active:
            if self._is_stop_phrase(text):
                self._stop_tts_playback()
                return
            if self._looks_like_self_audio(text):
                self.popup_until = time.perf_counter() + 1.2
                self.popup_title = "SELF AUDIO IGNORED"
                self.popup_text = "Speaker echo filtered"
                return

        self._stop_tts_playback()
        self._send_text(text)

    def _handle_voice_start(self) -> None:
        if not self.listening or self._closing:
            return

        if self._tts_playback_active:
            def apply_candidate() -> None:
                self.popup_until = time.perf_counter() + 1.0
                self.popup_title = "VOICE CANDIDATE"
                self.popup_text = "Confirming human input..."
            self.run_on_ui(apply_candidate)
            return

        stopper = self._active_stop_event
        if stopper is not None:
            stopper.set()

        self._stop_tts_playback()

        def apply_interrupt() -> None:
            if not self.listening or self._closing:
                return
            self.popup_until = time.perf_counter() + 1.4
            self.popup_title = "VOICE DETECTED"
            self.popup_text = "Interrupting output..."

            if not self._generation_active:
                return

            pid = self._active_placeholder_id
            self._generation_active = False
            self._active_generation_id += 1
            self._active_stop_event = None
            self._active_placeholder_id = None
            self._set_send_button_generating(False)

            if pid is not None:
                self._replace_assistant_placeholder(pid, "[INTERRUPTED]\n")

        self.run_on_ui(apply_interrupt)

    def _is_stop_phrase(self, text: str) -> bool:
        normalized = " ".join(text.lower().split()).strip(" .!?")
        return normalized in {
            "stop",
            "stop speaking",
            "stop talking",
            "be quiet",
            "quiet",
            "cancel",
        }

    def _looks_like_self_audio(self, text: str) -> bool:
        spoken = self._clean_chat_text(self._current_tts_text).lower()
        heard = self._clean_chat_text(text).lower()
        if not spoken or not heard:
            return False

        ratio = SequenceMatcher(None, heard, spoken).ratio()
        if ratio >= 0.46:
            return True

        spoken_words = {
            word
            for word in spoken.split()
            if len(word) > 3
        }
        heard_words = {
            word
            for word in heard.split()
            if len(word) > 3
        }
        if not heard_words:
            return False
        overlap = len(spoken_words & heard_words) / max(1, len(heard_words))
        return overlap >= 0.55

    def copy_last_reply(self) -> None:
        if not self._last_assistant_message:
            return
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(self._last_assistant_message)
            self.root.update_idletasks()
        except Exception:
            pass

    def animate(self) -> None:
        if self._closing:
            return
        try:
            frame_start = time.perf_counter()
            dt = max(0.001, frame_start - self.last_frame_time)
            self.last_frame_time = frame_start

            self.frame_samples.append(1.0 / dt)
            if len(self.frame_samples) > 30:
                self.frame_samples.pop(0)
            avg_fps = sum(self.frame_samples) / len(self.frame_samples)
            self.fps_text = f"FPS {int(min(60, avg_fps))}"

            t = frame_start - self.runtime_start
            self.scan_line_offset = (self.scan_line_offset + dt * 50) % self.height
            self.hex_rotation = (self.hex_rotation + dt * 10) % 360
            self.data_stream_offset = (self.data_stream_offset + dt * 80) % 100

            # Update ring rotations (different speeds for each ring)
            self.ring_rotation[0] = (self.ring_rotation[0] + dt * 15) % 360
            self.ring_rotation[1] = (self.ring_rotation[1] + dt * 25) % 360
            self.ring_rotation[2] = (self.ring_rotation[2] + dt * 40) % 360

            # Update pulse rings
            for pulse in self.pulse_rings:
                pulse["radius"] += pulse["speed"]
                pulse["alpha"] -= 0.02
            self.pulse_rings = [p for p in self.pulse_rings if p["alpha"] > 0]

            # Update waveform data when listening
            if self.listening:
                for i in range(64):
                    self.waveform_data[i] = 0.3 + 0.7 * abs(math.sin(t * 8 + i * 0.3)) * (0.5 + 0.5 * math.sin(t * 3 + i * 0.1))
            else:
                for i in range(64):
                    self.waveform_data[i] *= 0.95

            self.canvas.delete("all")
            self.draw_background(t)
            self.draw_hex_grid(t)
            self.draw_circuit_lines(t)
            self.draw_data_streams(t)
            self.draw_hud_panels(t)
            self.draw_assistant_core(t)
            self.draw_arc_clock()
            self.draw_crosshairs(t)
            self.draw_popup(t)
            self._last_ui_error = ""
        except Exception as exc:
            self._last_ui_error = f"{type(exc).__name__}: {exc}"
        finally:
            if not self._closing:
                self.root.after(self.FRAME_MS, self.animate)

    def draw_background(self, t: float) -> None:
        self.canvas.create_rectangle(0, 0, self.width, self.height, fill=self.bg_main, outline="")

        # Subtle radial vignette
        cx, cy = self.center
        for i in range(12):
            ratio = i / 12
            r = max(self.width, self.height) * (0.25 + ratio * 0.75)
            alpha = max(0, min(255, int(8 * (1 - ratio))))
            color = f"#0{alpha:01x}0{alpha:01x}0{alpha:01x}"
            self.canvas.create_oval(cx - r, cy - r, cx + r, cy + r, fill=color, outline="")

        # Corner brackets (tech style)
        corner_size = 50
        corners = [
            (0, 0), (self.width - corner_size, 0),
            (0, self.height - corner_size), (self.width - corner_size, self.height - corner_size)
        ]
        for x, y in corners:
            self.canvas.create_line(x, y + 15, x, y, x + 15, y, fill=self.arc_dim, width=2)
            self.canvas.create_line(x + corner_size - 15, y, x + corner_size, y, x + corner_size, y + 15, fill=self.arc_dim, width=2)
            self.canvas.create_line(x, y + corner_size - 15, x, y + corner_size, x + 15, y + corner_size, fill=self.arc_dim, width=2)
            self.canvas.create_line(x + corner_size - 15, y + corner_size, x + corner_size, y + corner_size, x + corner_size, y + corner_size - 15, fill=self.arc_dim, width=2)

        # Scan lines (CRT effect)
        for i in range(0, self.height, 3):
            y = (i + int(self.scan_line_offset)) % self.height
            alpha = max(0, min(255, 6 + int(3 * math.sin(y * 0.015 + t * 2.5))))
            color = f"#0{alpha:01x}0{alpha:01x}0{alpha:01x}"
            self.canvas.create_line(0, y, self.width, y, fill=color, width=1)

        # Tech footer
        self.canvas.create_text(
            20, self.height - 18,
            anchor="w",
            text="SYLVIA // LOCAL VOICE INTERFACE // PRIVATE DESKTOP AI",
            fill=self.hud_dark,
            font=("Courier New", 8),
        )

    def draw_hex_grid(self, t: float) -> None:
        hex_size = 40
        cx, cy = self.center

        for row in range(-10, 11):
            for col in range(-14, 15):
                x = cx + col * hex_size * 1.5
                y = cy + row * hex_size * 1.732 + (col % 2) * hex_size * 0.866

                dist = math.sqrt((x - cx)**2 + (y - cy)**2)
                if dist > max(self.width, self.height) * 0.6:
                    continue

                alpha = max(0, 1 - dist / (max(self.width, self.height) * 0.45))
                brightness = max(0, min(255, int(15 + 25 * alpha * (0.5 + 0.5 * math.sin(t * 0.4 + row * 0.25 + col * 0.15)))))
                if brightness < 10:
                    continue
                color = f"#0{brightness//16:01x}{brightness//16:01x}"

                points = []
                for i in range(6):
                    angle = math.radians(60 * i + self.hex_rotation * 0.08)
                    px = x + hex_size * 0.35 * math.cos(angle)
                    py = y + hex_size * 0.35 * math.sin(angle)
                    points.extend([px, py])
                self.canvas.create_polygon(points, fill="", outline=color, width=1)

    def draw_circuit_lines(self, t: float) -> None:
        # Draw circuit-like patterns radiating from center
        cx, cy = self.center
        num_lines = 8

        for i in range(num_lines):
            angle = math.radians(i * 45 + t * 5)
            inner_r = min(self.width, self.height) * 0.18
            outer_r = min(self.width, self.height) * 0.35

            # Main line
            x1 = cx + math.cos(angle) * inner_r
            y1 = cy + math.sin(angle) * inner_r
            x2 = cx + math.cos(angle) * outer_r
            y2 = cy + math.sin(angle) * outer_r

            alpha = max(0, min(255, int(30 + 40 * math.sin(t * 2 + i * 0.8))))
            color = f"#0{alpha//16:01x}{alpha//16:01x}"
            self.canvas.create_line(x1, y1, x2, y2, fill=color, width=1)

            # Circuit nodes
            node_r = min(self.width, self.height) * 0.22
            nx = cx + math.cos(angle) * node_r
            ny = cy + math.sin(angle) * node_r
            node_size = 2 + math.sin(t * 3 + i) * 1
            self.canvas.create_oval(nx - node_size, ny - node_size, nx + node_size, ny + node_size, fill=self.arc_dim, outline="")

    def draw_data_streams(self, t: float) -> None:
        # Left data column - binary streams
        for i in range(24):
            y = ((i * 32 + self.data_stream_offset * 3) % (self.height - 80)) + 40
            x = 25
            bar_w = random.uniform(15, 50)
            alpha = max(0, min(255, int(35 + 55 * math.sin(t * 3.5 + i * 0.6))))
            color = f"#0{alpha//16:01x}{alpha//16:01x}"
            self.canvas.create_rectangle(x, y, x + bar_w, y + 1.5, fill=color, outline="")

            if i % 4 == 0:
                binary = "".join([str(random.randint(0, 1)) for _ in range(6)])
                self.canvas.create_text(x + 30, y + 10, text=binary, fill=self.hud_dark, font=("Courier New", 6))

        # Right data column
        for i in range(24):
            y = ((i * 32 + self.data_stream_offset * 2.5) % (self.height - 80)) + 40
            x = self.width - 75
            bar_w = random.uniform(15, 50)
            alpha = max(0, min(255, int(35 + 55 * math.sin(t * 2.8 + i * 0.9))))
            color = f"#0{alpha//16:01x}{alpha//16:01x}"
            self.canvas.create_rectangle(x, y, x + bar_w, y + 1.5, fill=color, outline="")

        # Floating particles
        for particle in self.data_particles:
            px = (particle["x"] * self.width + math.sin(t * particle["speed"] + particle["phase"]) * particle["drift"]) % self.width
            py = (particle["y"] * self.height + t * 3 * particle["speed"] * particle["depth"]) % self.height

            if particle["type"] == "dot":
                size = particle["size"] * (0.8 + 0.2 * math.sin(t * 2 + particle["phase"]))
                color = self.arc_blue if random.random() > 0.6 else self.hud_dark
                self.canvas.create_oval(px - size, py - size, px + size, py + size, fill=color, outline="")
            elif particle["type"] == "line":
                length = particle["size"] * 6
                angle = particle["phase"]
                self.canvas.create_line(px, py, px + math.cos(angle) * length, py + math.sin(angle) * length, fill=self.arc_dim, width=1)
            elif particle["type"] == "cross":
                size = particle["size"] * 2
                self.canvas.create_line(px - size, py, px + size, py, fill=self.hud_dark, width=1)
                self.canvas.create_line(px, py - size, px, py + size, fill=self.hud_dark, width=1)

    def draw_hud_panels(self, t: float) -> None:
        margin = 22
        top_y = 18

        # Top-left: System Status
        left_w = min(320, self.width * 0.24)
        self.draw_tech_panel(margin, top_y, margin + left_w, top_y + 110, "SYS.STATUS")

        lx = margin + 12
        self.canvas.create_text(lx, top_y + 32, anchor="w", text=self.time_text, fill=self.hud_white, font=("Courier New", 24, "bold"))
        self.canvas.create_text(lx, top_y + 56, anchor="w", text=self.date_text, fill=self.hud_dim, font=("Courier New", 9))
        self.canvas.create_text(lx, top_y + 74, anchor="w", text=self.clip_text(f"{self.fps_text}  |  {self.device_text}", 36), fill=self.hud_dark, font=("Courier New", 8))
        self.canvas.create_text(lx, top_y + 90, anchor="w", text=self.clip_text(self.stats_text, 38), fill=self.hud_dim, font=("Courier New", 8))

        # Top-right: Environment
        right_w = min(320, self.width * 0.24)
        self.draw_tech_panel(self.width - right_w - margin, top_y, self.width - margin, top_y + 110, "ENV.DATA")

        rx = self.width - right_w - margin + 12
        self.canvas.create_text(rx, top_y + 32, anchor="w", text=self.clip_text(self.battery_text, 24), fill=self.hud_white, font=("Courier New", 12, "bold"))
        self.canvas.create_text(rx, top_y + 52, anchor="w", text=self.clip_text(self.weather_text, 34), fill=self.arc_blue, font=("Courier New", 9))
        self.canvas.create_text(rx, top_y + 70, anchor="w", text=self.clip_text(self.network_text, 34), fill=self.hud_dim, font=("Courier New", 8))
        mic_state = "ACTIVE" if self.listening else "STANDBY"
        mic_color = self.arc_blue if self.listening else self.hud_dark
        self.canvas.create_text(rx, top_y + 88, anchor="w", text=f"AUDIO: {mic_state}", fill=mic_color, font=("Courier New", 9, "bold"))

        # Bottom-left: Voice Engine
        lower_h = 140
        self.draw_tech_panel(margin, self.height - lower_h - 24, margin + left_w, self.height - 24, "AUDIO.SYS")

        lower_x = margin + 12
        self.canvas.create_text(lower_x, self.height - lower_h + 6, anchor="w", text="VOICE ENGINE", fill=self.hud_white, font=("Courier New", 11, "bold"))
        mode_text = "LISTENING / TRANSCRIBE" if self.listening else "IDLE / TRIGGER"
        self.canvas.create_text(lower_x, self.height - lower_h + 26, anchor="w", text=f"MODE: {mode_text}", fill=self.hud_dim, font=("Courier New", 9))
        self.canvas.create_text(lower_x, self.height - lower_h + 44, anchor="w", text=self.clip_text(self._get_mic_status_text(), 38), fill=self.hud_dark, font=("Courier New", 8))
        self.canvas.create_text(lower_x, self.height - lower_h + 60, anchor="w", text=self.clip_text(self._get_mic_debug_text(), 44), fill=self.hud_dark, font=("Courier New", 8))
        self.canvas.create_text(lower_x, self.height - lower_h + 78, anchor="w", text="[ CLICK CORE TO TOGGLE AUDIO ]", fill=self.arc_blue, font=("Courier New", 8, "bold"))

        # Audio level meter
        self.draw_arc_meter(lower_x + 8, self.height - 52, 160, 8, t, self.listening)

        # Center title
        self.canvas.create_text(self.width / 2, 42, text="SYLVIA", fill=self.hud_white, font=("Courier New", 18, "bold"))
        self.canvas.create_text(self.width / 2, 62, text="LOCAL EMOTIONAL VOICE ASSISTANT", fill=self.hud_dark, font=("Courier New", 7))

        # Bottom-right: Chat Panel
        chat_x1 = self.width - right_w - margin
        chat_x2 = self.width - margin
        chat_y1 = top_y + 120
        reserve = max(180, min(240, self.height * 0.26))
        chat_y2 = max(chat_y1 + 180, self.height - 24 - reserve)
        self.draw_tech_panel(chat_x1, chat_y1, chat_x2, chat_y2, "COMMS.LOG")

        chat_w = max(200, int(chat_x2 - chat_x1 - 14))
        chat_h = max(140, int(chat_y2 - chat_y1 - 26))
        self.chat_model_label.configure(text=self.model_status_text)

        gx = int(chat_x1 + 7)
        gy = int(chat_y1 + 20)
        gw = int(chat_w)
        gh = int(chat_h)
        geom = (gx, gy, gw, gh)
        if geom != self._chat_overlay_geom:
            self._chat_overlay_geom = geom
            self.chat_frame.place(x=gx, y=gy, width=gw, height=gh)

    def draw_tech_panel(self, x1: float, y1: float, x2: float, y2: float, title: str) -> None:
        self.canvas.create_rectangle(x1, y1, x2, y2, fill=self.panel_bg, outline=self.panel_border, width=1)
        self.canvas.create_rectangle(x1 + 1, y1 + 1, x2 - 1, y1 + 14, fill="#0c1218", outline="")

        bracket = 8
        self.canvas.create_line(x1, y1 + bracket, x1, y1, x1 + bracket, y1, fill=self.arc_blue, width=2)
        self.canvas.create_line(x2 - bracket, y1, x2, y1, x2, y1 + bracket, fill=self.arc_blue, width=2)
        self.canvas.create_line(x1, y2 - bracket, x1, y2, x1 + bracket, y2, fill=self.panel_border, width=1)
        self.canvas.create_line(x2 - bracket, y2, x2, y2, x2, y2 - bracket, fill=self.panel_border, width=1)

        self.canvas.create_text(x1 + 10, y1 + 7, anchor="w", text=f"[{title}]", fill=self.hud_dim, font=("Courier New", 7, "bold"))
        self.canvas.create_line(x1 + 10, y1 + 14, x2 - 10, y1 + 14, fill=self.panel_border, width=1)

    def draw_arc_meter(self, x: float, y: float, width: float, height: float, t: float, active: bool) -> None:
        segments = 16
        gap = 2
        seg_w = (width - gap * (segments + 1)) / segments

        for i in range(segments):
            sx1 = x + gap + i * (seg_w + gap)
            sx2 = sx1 + seg_w
            intensity = 0.08
            if active:
                intensity = 0.25 + 0.75 * max(0, math.sin(t * 6 + i * 0.6))

            r = int(8 + 0 * intensity)
            g = int(16 + 212 * intensity)
            b = int(24 + 255 * intensity)
            fill = f"#{r:02x}{g:02x}{b:02x}"
            self.canvas.create_rectangle(sx1, y, sx2, y + height, fill=fill, outline="")

    def draw_arc_clock(self) -> None:
        r = max(45, min(self.width, self.height) * 0.055)
        cx = self.width - r - 32
        cy = self.height - r - 32

        self.canvas.create_oval(cx - r - 6, cy - r - 6, cx + r + 6, cy + r + 6, fill="#060a0e", outline="")
        self.canvas.create_oval(cx - r, cy - r, cx + r, cy + r, fill="#080c10", outline=self.panel_border, width=2)

        for i in range(60):
            angle = math.radians(i * 6 - 90)
            inner = r * (0.76 if i % 5 == 0 else 0.86)
            outer = r * 0.95
            color = self.hud_white if i % 5 == 0 else self.hud_dark
            width = 2 if i % 5 == 0 else 1
            self.canvas.create_line(
                cx + math.cos(angle) * inner, cy + math.sin(angle) * inner,
                cx + math.cos(angle) * outer, cy + math.sin(angle) * outer,
                fill=color, width=width,
            )

        now = datetime.now()
        second = now.second + now.microsecond / 1_000_000
        minute = now.minute + second / 60
        hour = (now.hour % 12) + minute / 60

        self.draw_hand(cx, cy, r * 0.48, hour * 30 - 90, self.hud_white, 3)
        self.draw_hand(cx, cy, r * 0.68, minute * 6 - 90, self.arc_blue, 2)
        self.draw_hand(cx, cy, r * 0.82, second * 6 - 90, self.success_green, 1)

        self.canvas.create_oval(cx - 4, cy - 4, cx + 4, cy + 4, fill=self.hud_white, outline="")
        self.canvas.create_text(cx, cy + r + 12, text="CHRONO", fill=self.hud_dark, font=("Courier New", 8, "bold"))

    def draw_hand(self, cx: float, cy: float, length: float, angle_deg: float, color: str, width: int) -> None:
        angle = math.radians(angle_deg)
        x = cx + math.cos(angle) * length
        y = cy + math.sin(angle) * length
        self.canvas.create_line(cx, cy, x, y, fill=color, width=width, capstyle=tk.ROUND)

    def draw_assistant_core(self, t: float) -> None:
        cx, cy = self.center
        base_r = min(self.width, self.height) * 0.095
        pulse = 1.0 + (0.05 * math.sin(t * 2.8) if self.listening else 0.0)
        orbit_r = base_r * 1.7

        # === HOLOGRAPHIC DATA RINGS (3 concentric, different speeds) ===
        ring_configs = [
            (self.ring_rotation[0], 2.4, "#0a1a25", 1),   # Inner - slow
            (self.ring_rotation[1], 1.9, "#0f2535", 1),   # Middle - medium
            (self.ring_rotation[2], 1.5, "#153045", 1),   # Outer - fast
        ]

        for rot, scale, color, width in ring_configs:
            rr = base_r * scale
            # Draw ring as segmented arc for tech look
            segments = 24
            for i in range(segments):
                start_angle = math.radians(rot + i * (360 / segments))
                end_angle = math.radians(rot + (i + 0.8) * (360 / segments))

                # Skip some segments for broken ring effect
                if i % 7 == 0:
                    continue

                x1 = cx + math.cos(start_angle) * rr
                y1 = cy + math.sin(start_angle) * rr
                x2 = cx + math.cos(end_angle) * rr
                y2 = cy + math.sin(end_angle) * rr

                alpha = 0.3 + 0.7 * math.sin(t * 2 + i * 0.5)
                if self.listening:
                    alpha = 0.6 + 0.4 * math.sin(t * 4 + i * 0.8)

                line_color = self.mix_color(color, self.arc_blue, alpha)
                self.canvas.create_line(x1, y1, x2, y2, fill=line_color, width=width)

        # === ENERGY PULSE RINGS (expanding from center) ===
        for pulse_ring in self.pulse_rings:
            pr = pulse_ring["radius"]
            alpha = pulse_ring["alpha"]
            if pr < base_r * 3 and alpha > 0:
                color = self.mix_color("#000000", self.arc_blue, alpha * 0.5)
                self.canvas.create_oval(cx - pr, cy - pr, cx + pr, cy + pr, outline=color, width=2)

        # === BREATHING RINGS (PRESERVED FROM ORIGINAL) ===
        if self.listening:
            for i in range(3):
                rr = base_r * (1.15 + i * 0.18 + 0.04 * math.sin(t * (2.2 + i * 0.5)))
                line = self.mix_color("#153045", self.arc_blue, 0.45 + i * 0.2)
                self.canvas.create_oval(cx - rr, cy - rr, cx + rr, cy + rr, outline=line, width=2)

        # === ARC REACTOR STYLE CORE ===
        disk_r = base_r * pulse
        self.canvas.create_oval(cx - disk_r, cy - disk_r, cx + disk_r, cy + disk_r, fill="#04080c", outline="#1a3040", width=2)

        inner_r = base_r * 0.72 * pulse
        self.canvas.create_oval(
            cx - inner_r, cy - inner_r, cx + inner_r, cy + inner_r,
            fill="#060a0f",
            outline=self.mix_color("#1a3040", self.arc_blue, 0.9 if self.listening else 0.2),
            width=2,
        )

        core_r = base_r * 0.32 * pulse
        self.canvas.create_oval(
            cx - core_r, cy - core_r, cx + core_r, cy + core_r,
            fill=self.mix_color("#0a1a25", self.arc_blue, 0.92 if self.listening else 0.25),
            outline="",
        )

        # Innermost bright core
        bright_r = base_r * 0.12 * pulse
        self.canvas.create_oval(
            cx - bright_r, cy - bright_r, cx + bright_r, cy + bright_r,
            fill=self.arc_blue if self.listening else self.arc_dim,
            outline="",
        )

        # === RADIAL TECH BARS ===
        for i in range(48):
            angle = math.tau * i / 48
            start_r = base_r * 0.88
            end_r = base_r * 1.12
            bar = 1.0
            if self.listening:
                bar += 0.22 * max(0.0, math.sin(t * 6.5 + i * 0.45))
            x1 = cx + math.cos(angle) * start_r
            y1 = cy + math.sin(angle) * start_r
            x2 = cx + math.cos(angle) * end_r * bar
            y2 = cy + math.sin(angle) * end_r * bar
            color = self.mix_color("#1a3040", self.arc_blue, 0.9 if self.listening else 0.15)
            self.canvas.create_line(x1, y1, x2, y2, fill=color, width=2 if i % 6 == 0 else 1)

        # === AUDIO WAVEFORM RING (when listening) ===
        if self.listening or max(self.waveform_data) > 0.01:
            wave_r = base_r * 1.35
            points = []
            for i in range(64):
                angle = math.tau * i / 64
                r = wave_r + self.waveform_data[i] * base_r * 0.25
                px = cx + math.cos(angle) * r
                py = cy + math.sin(angle) * r
                points.extend([px, py])
            # Close the loop
            points.extend(points[:2])
            wave_color = self.mix_color("#1a3040", self.arc_blue, 0.6 if self.listening else 0.1)
            self.canvas.create_line(points, fill=wave_color, smooth=True, width=1)

        # Labels
        self.canvas.create_text(cx, cy - base_r - 48, text="ARC REACTOR", fill=self.hud_dim, font=("Courier New", 9, "bold"))
        self.canvas.create_text(cx, cy + base_r + 26, text="[ CLICK TO TOGGLE AUDIO ]", fill=self.hud_dark, font=("Courier New", 9))

        status_text = "LISTENING" if self.listening else "STANDBY"
        status_color = self.arc_blue if self.listening else self.hud_dark
        self.canvas.create_text(cx, cy + base_r + 48, text=status_text, fill=status_color, font=("Courier New", 16, "bold"))

        # === ORBITING DATA NODES ===
        for i, color in enumerate(self.orb_palette):
            base_angle = i * (math.tau / len(self.orb_palette))
            angle = base_angle + (t * (0.28 + i * 0.018) if self.listening else 0.0)
            local_r = orbit_r + (5 * math.sin(t * 2.2 + i) if self.listening else 0.0)
            ox = cx + math.cos(angle) * local_r
            oy = cy + math.sin(angle) * local_r * 0.78
            size = 3.0 + (0.6 * math.sin(t * 2.5 + i) if self.listening else 0.0)

            self.canvas.create_oval(ox - size * 2, oy - size * 2, ox + size * 2, oy + size * 2, outline=self.mix_color("#1a3040", color, 0.5), width=1)
            self.canvas.create_oval(ox - size, oy - size, ox + size, oy + size, fill=self.mix_color("#1a3040", color, 0.8), outline="")

            if self.listening:
                tail_x = cx + math.cos(angle - 0.04) * (local_r - 6)
                tail_y = cy + math.sin(angle - 0.04) * ((local_r - 6) * 0.78)
                self.canvas.create_line(tail_x, tail_y, ox, oy, fill=self.mix_color("#1a3040", color, 0.35), width=1)

    def draw_crosshairs(self, t: float) -> None:
        # Draw targeting crosshairs at center
        cx, cy = self.center
        size = 20
        gap = 8

        # Horizontal lines
        self.canvas.create_line(cx - size - gap, cy, cx - gap, cy, fill=self.arc_dim, width=1)
        self.canvas.create_line(cx + gap, cy, cx + size + gap, cy, fill=self.arc_dim, width=1)
        # Vertical lines
        self.canvas.create_line(cx, cy - size - gap, cx, cy - gap, fill=self.arc_dim, width=1)
        self.canvas.create_line(cx, cy + gap, cx, cy + size + gap, fill=self.arc_dim, width=1)

        # Corner brackets around crosshair
        bracket = 6
        self.canvas.create_line(cx - gap, cy - gap, cx - gap + bracket, cy - gap, fill=self.arc_blue, width=1)
        self.canvas.create_line(cx - gap, cy - gap, cx - gap, cy - gap + bracket, fill=self.arc_blue, width=1)
        self.canvas.create_line(cx + gap, cy - gap, cx + gap - bracket, cy - gap, fill=self.arc_blue, width=1)
        self.canvas.create_line(cx + gap, cy - gap, cx + gap, cy - gap + bracket, fill=self.arc_blue, width=1)
        self.canvas.create_line(cx - gap, cy + gap, cx - gap + bracket, cy + gap, fill=self.arc_blue, width=1)
        self.canvas.create_line(cx - gap, cy + gap, cx - gap, cy + gap - bracket, fill=self.arc_blue, width=1)
        self.canvas.create_line(cx + gap, cy + gap, cx + gap - bracket, cy + gap, fill=self.arc_blue, width=1)
        self.canvas.create_line(cx + gap, cy + gap, cx + gap, cy + gap - bracket, fill=self.arc_blue, width=1)

    def draw_popup(self, t: float) -> None:
        if time.perf_counter() > self.popup_until:
            return

        cx, cy = self.center
        w = min(320, self.width * 0.24)
        h = 76
        x1 = cx - w / 2
        y1 = cy - min(140, self.height * 0.2)
        y_offset = math.sin(t * 4) * 2
        y1 += y_offset
        x2 = x1 + w
        y2 = y1 + h

        self.canvas.create_rectangle(x1, y1, x2, y2, fill="#060a0e", outline=self.panel_border, width=1)
        self.canvas.create_rectangle(x1 + 1, y1 + 1, x2 - 1, y1 + 12, fill="#0c1218", outline="")
        self.canvas.create_line(x1 + 10, y1 + 12, x2 - 10, y1 + 12, fill=self.panel_border, width=1)

        self.canvas.create_text(cx, y1 + 26, text=self.popup_title, fill=self.hud_white, font=("Courier New", 10, "bold"))
        self.canvas.create_text(cx, y1 + 48, text=self.popup_text, fill=self.arc_blue, font=("Courier New", 14, "bold"))

    def mix_color(self, c1: str, c2: str, amount: float) -> str:
        amount = max(0.0, min(1.0, amount))
        r1, g1, b1 = self.hex_to_rgb(c1)
        r2, g2, b2 = self.hex_to_rgb(c2)
        r = int(r1 + (r2 - r1) * amount)
        g = int(g1 + (g2 - g1) * amount)
        b = int(b1 + (b2 - b1) * amount)
        return f"#{r:02x}{g:02x}{b:02x}"

    def hex_to_rgb(self, value: str) -> tuple[int, int, int]:
        value = value.lstrip("#")
        return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)

    def clip_text(self, text: str, limit: int) -> str:
        clean = " ".join(str(text).split())
        if len(clean) <= limit:
            return clean
        return clean[: max(0, limit - 1)].rstrip() + "..."

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    SylviaInterfaceUI().run()
