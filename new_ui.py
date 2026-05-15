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
try:
    import tkinter as tk
except ModuleNotFoundError as exc:
    raise SystemExit(
        "Tkinter is not installed. Install it and retry (e.g., 'python3-tk' on Debian/Ubuntu)."
    ) from exc
from datetime import datetime
from typing import Callable
from urllib.request import Request, urlopen

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


class CyberAssistantUI:
    FRAME_MS = 16
    MAX_CHAT_MESSAGES = 12
    MAX_MESSAGE_CHARS = 900

    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("AURA Voice Assistant")
        self.root.geometry("1320x820")
        self.root.minsize(980, 640)
        self.root.configure(bg="#0d0805")
        self.root.resizable(True, True)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self._ui_thread_id = threading.get_ident()
        self._ui_tasks: "queue.Queue[Callable[[], None]]" = queue.Queue()
        self._closing = False

        self.canvas = tk.Canvas(
            self.root,
            bg="#0d0805",
            highlightthickness=0,
            bd=0,
            relief="flat",
        )
        self.canvas.pack(fill="both", expand=True)

        self.width = 1320
        self.height = 820
        self.center = (self.width / 2, self.height / 2 + 10)

        self.listening = False
        self.last_frame_time = time.perf_counter()
        self.runtime_start = time.perf_counter()
        self.popup_until = 0.0
        self.popup_title = "MIC READY"
        self.popup_text = "Standby"

        self.time_text = "--:--:--"
        self.date_text = "-- --- ----"
        self.battery_text = "Battery: Detecting..."
        self.weather_text = "Weather: Fetching..."
        self.device_text = f"{platform.system()} {platform.release()}"
        self.fps_text = "FPS 60"
        self.stats_text = "CPU --%  •  RAM --%  •  DISK --%"
        self.network_text = "NETWORK: Detecting..."

        # Pipelines (keep heavy work out of the GUI file)
        self._brain = brain.get_default_brain() if brain else None
        self._mic = mic.get_default_mic() if mic else None
        self._tts = tts.get_default_tts() if tts else None

        if self._brain:
            self.model_status_text = self._brain.status
        else:
            self.model_status_text = f"Model: unavailable ({_BRAIN_IMPORT_ERROR})" if _BRAIN_IMPORT_ERROR else "Model: unavailable"
        self._last_assistant_message = ""
        self._placeholder_counter = 0
        self._chat_overlay_geom: tuple[int, int, int, int] | None = None

        # Chat history for better answer quality.
        self._chat_history: list[tuple[str, str]] = []

        self.frame_samples = []
        self.grid_shift = 0.0

        self.accent_primary = "#ff9f00"
        self.bg_main = "#0a0704"
        self.panel_fill = "#1a1209"
        self.panel_line = "#5c3d1e"

        self.text_dim = "#b8a080"

        self.orb_palette = [
            "#ff9f00",
            "#00c3ff",
            "#7a5cff",
            "#ff6b35",
            "#ffb347",
            "#79ffe1",
            "#9f7bff",
        ]

        self.star_particles = []
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
            self.root.destroy()
        except Exception:
            pass

    def _build_particles(self) -> None:
        self.star_particles.clear()
        for _ in range(64):
            self.star_particles.append(
                {
                    "x": random.random(),
                    "y": random.random(),
                    "size": random.uniform(1.0, 2.8),
                    "speed": random.uniform(0.02, 0.12),
                    "drift": random.uniform(8.0, 24.0),
                    "phase": random.uniform(0.0, math.tau),
                    "depth": random.uniform(0.35, 1.0),
                }
            )

    def on_resize(self, event: tk.Event) -> None:
        if event.widget is not self.root:
            return
        self.width = max(1, event.width)
        self.height = max(1, event.height)
        self.center = (self.width / 2, self.height / 2 + min(24, self.height * 0.03))

    def on_click(self, event: tk.Event) -> None:
        cx, cy = self.center
        hit_r = min(self.width, self.height) * 0.115
        dx = event.x - cx
        dy = event.y - cy
        if dx * dx + dy * dy <= hit_r * hit_r:
            self.listening = not self.listening
            self.popup_until = time.perf_counter() + 2.3
            if self.listening:
                self.popup_title = "MICROPHONE ACTIVE"
                self.popup_text = "Listening..."
                self.start_voice_engine()
            else:
                self.popup_title = "MICROPHONE STANDBY"
                self.popup_text = "Idle"
                self.stop_voice_engine()

    def update_system_info(self) -> None:
        if self._closing:
            return
        now = datetime.now()
        self.time_text = now.strftime("%H:%M:%S")
        self.date_text = now.strftime("%d %b %Y")
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
            self.stats_text = "CPU --%  •  RAM --%  •  DISK --%"
        else:
            self.stats_text = f"CPU {int(cpu_pct)}%  •  RAM {int(mem_pct or 0)}%  •  DISK {int(disk_pct or 0)}%"

        self.network_text = self.get_network_text()

    def get_battery_text(self) -> str:
        try:
            if psutil:
                battery = psutil.sensors_battery()
                if battery:
                    mode = "Charging" if battery.power_plugged else "Battery"
                    return f"{mode}: {int(battery.percent)}%"
            if sys.platform == "darwin":
                output = subprocess.check_output(
                    ["pmset", "-g", "batt"],
                    text=True,
                    stderr=subprocess.DEVNULL,
                )
                state = "Charging" if "AC Power" in output else "Battery"
                for line in output.splitlines():
                    if "%" in line:
                        percent = line.split("	")[-1].split(";")[0].strip()
                        return f"{state}: {percent}"
        except Exception:
            pass
        return "Battery: Unavailable"

    def get_network_text(self) -> str:
        # Prefer psutil interface enumeration; fall back to a simple connectivity probe.
        try:
            if psutil:
                stats = psutil.net_if_stats()
                addrs = psutil.net_if_addrs()

                active_ifaces = []
                for name, s in stats.items():
                    if not s.isup:
                        continue
                    # ignore obvious loopback-like interfaces
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
                    suffix = f" {ip}" if ip else ""
                    return f"NETWORK: ONLINE{suffix}"
                return "NETWORK: OFFLINE"
        except Exception:
            pass

        try:
            with socket.create_connection(("1.1.1.1", 53), timeout=0.5):
                pass
            return "NETWORK: ONLINE"
        except Exception:
            return "NETWORK: OFFLINE"

    def fetch_weather_async(self) -> None:
        def worker() -> None:
            try:
                request = Request(
                    "https://wttr.in/?format=%l:+%C+%t ",
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                with urlopen(request, timeout=4) as response:
                    text = response.read().decode("utf-8", errors="ignore").strip()
                weather_text = f"Weather: {text}" if text else "Weather: Offline"
            except Exception:
                weather_text = "Weather: Offline"
            finally:
                def apply_weather() -> None:
                    self.weather_text = weather_text
                    if not self._closing:
                        self.root.after(900000, self.fetch_weather_async)

                self.run_on_ui(apply_weather)

        threading.Thread(target=worker, daemon=True).start()

    def init_pipelines_async(self) -> None:
        # Load the heavy pipelines on background threads so the UI stays smooth.
        if self._brain is None:
            self.model_status_text = "Model: unavailable"
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
        # Keep widgets persistent (placed as an overlay; avoids canvas create_window per frame).
        self.chat_frame = tk.Frame(self.root, bg=self.panel_fill, bd=0, highlightthickness=0)
        self.chat_frame.pack_propagate(False)

        header = tk.Frame(self.chat_frame, bg=self.panel_fill)
        header.pack(fill="x", padx=10, pady=(8, 4))

        self.chat_title = tk.Label(
            header,
            text="CHAT",
            bg=self.panel_fill,
            fg="#f0e6d3",
            font=("Helvetica", 11, "bold"),
        )
        self.chat_title.pack(side="left")

        self.chat_model_label = tk.Label(
            header,
            text=self.model_status_text,
            bg=self.panel_fill,
            fg="#8a7050",
            font=("Helvetica", 9, "bold"),
        )
        self.chat_model_label.pack(side="right")

        body = tk.Frame(self.chat_frame, bg=self.panel_fill)
        body.pack(fill="both", expand=True, padx=10, pady=(0, 6))

        self.chat_output = tk.Text(
            body,
            wrap="word",
            bg="#0f0a06",
            fg="#fff8f0",
            insertbackground="#fff8f0",
            bd=0,
            highlightthickness=1,
            highlightbackground="#3d2815",
            highlightcolor="#65e7ff",
            font=("Helvetica", 10),
        )
        self.chat_output.configure(state="disabled")
        self.chat_output.tag_configure("user_label", foreground="#ffaa00", font=("Helvetica", 10, "bold"))
        self.chat_output.tag_configure("assistant_label", foreground="#ff8c42", font=("Helvetica", 10, "bold"))
        self.chat_output.tag_configure("user_text", foreground="#fff8f0")
        self.chat_output.tag_configure("assistant_text", foreground="#f0e6d3")
        self.chat_output.tag_configure("placeholder", foreground="#c9a96e")

        scrollbar = tk.Scrollbar(body, command=self.chat_output.yview)
        self.chat_output.configure(yscrollcommand=scrollbar.set)

        self.chat_output.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        footer = tk.Frame(self.chat_frame, bg=self.panel_fill)
        footer.pack(fill="x", padx=10, pady=(0, 10))

        self.chat_input = tk.Text(
            footer,
            height=3,
            wrap="word",
            bg="#07111f",
            fg="#fff8f0",
            insertbackground="#fff8f0",
            bd=0,
            highlightthickness=1,
            highlightbackground="#3d2815",
            highlightcolor="#65e7ff",
            font=("Helvetica", 10),
        )
        self.chat_input.pack(side="left", fill="x", expand=True, padx=(0, 8))

        buttons = tk.Frame(footer, bg=self.panel_fill)
        buttons.pack(side="right")

        self.send_button = tk.Button(
            buttons,
            text="Send",
            command=self.on_send_clicked,
            bg="#1a1209",
            fg="#fff8f0",
            activebackground="#2a1c0e",
            activeforeground="#fff8f0",
            bd=0,
            padx=14,
            pady=8,
            font=("Helvetica", 10, "bold"),
        )
        self.send_button.pack(fill="x", pady=(0, 6))

        self.copy_button = tk.Button(
            buttons,
            text="Copy reply",
            command=self.copy_last_reply,
            bg="#07111f",
            fg="#ffb347",
            activebackground="#1a1209",
            activeforeground="#ffb347",
            bd=0,
            padx=14,
            pady=8,
            font=("Helvetica", 9, "bold"),
        )
        self.copy_button.pack(fill="x")

        # Enter = send, Shift+Enter = newline
        self.chat_input.bind("<Return>", self._on_enter_send)
        self.chat_input.bind("<Shift-Return>", self._on_shift_enter_newline)

        # Initial hint
        self._chat_append("assistant", "Hi — type a message and press Send.\n")

        # Start hidden until first layout pass.
        self.chat_frame.place(x=-2000, y=-2000, width=10, height=10)

    def _on_enter_send(self, event: tk.Event) -> str:
        self.on_send_clicked()
        return "break"

    def _on_shift_enter_newline(self, event: tk.Event) -> None:
        self.chat_input.insert("insert", "\n")

    def on_send_clicked(self) -> None:
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

        def worker(user_text: str, pid: int, history: list[tuple[str, str]]) -> None:
            reply = self._generate_reply(user_text, history)

            def apply_reply() -> None:
                self._replace_assistant_placeholder(pid, reply + "\n")
                self._chat_history.append(("assistant", reply))
                self._trim_chat_history()

            self.run_on_ui(apply_reply)
            self._speak_reply(reply)

        threading.Thread(target=worker, args=(text, placeholder_id, history_before), daemon=True).start()

    def _generate_reply(self, user_text: str, history: list[tuple[str, str]] | None = None) -> str:
        if self._brain is None:
            return "Local model unavailable (brain.py not loaded)."
        self.run_on_ui(lambda: setattr(self, "model_status_text", self._brain.status))
        return self._clean_chat_text(self._brain.generate(user_text, history=history or []))

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

    def _speak_reply(self, reply: str) -> None:
        if not reply.strip() or self._tts is None:
            return
        try:
            self._tts.speak(reply, play=True)
        except Exception:
            if _TTS_IMPORT_ERROR:
                self.run_on_ui(lambda: self._chat_append("assistant", f"TTS unavailable: {_TTS_IMPORT_ERROR}\n"))

    def _get_mic_status_text(self) -> str:
        if self._mic is None:
            return "Mic: unavailable"
        return getattr(self._mic, "status", "Mic: unknown")

    def _get_mic_debug_text(self) -> str:
        if self._mic is None:
            return "Input sensitivity: adaptive"
        last_error = getattr(self._mic, "last_error", "")
        if last_error:
            return f"Mic error: {last_error[:54]}"
        debug = getattr(self._mic, "debug", "")
        return debug[:64] if debug else "Input sensitivity: adaptive"

    def _chat_append(self, role: str, text: str) -> None:
        self.chat_output.configure(state="normal")
        if role == "user":
            self.chat_output.insert("end", "You: ", ("user_label",))
            self.chat_output.insert("end", text, ("user_text",))
        else:
            self.chat_output.insert("end", "AURA: ", ("assistant_label",))
            self.chat_output.insert("end", text, ("assistant_text",))
            self._last_assistant_message = text.strip()
        self.chat_output.configure(state="disabled")
        self.chat_output.see("end")

    def _append_assistant_placeholder(self) -> int:
        self._placeholder_counter += 1
        pid = self._placeholder_counter
        self.chat_output.configure(state="normal")
        self.chat_output.insert("end", "AURA: ", ("assistant_label",))
        start_mark = f"reply_start_{pid}"
        end_mark = f"reply_end_{pid}"
        self.chat_output.mark_set(start_mark, "end-1c")
        self.chat_output.insert("end", "…\n", ("assistant_text", "placeholder"))
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
            # Fallback if marks were lost for any reason.
            self.chat_output.insert("end", "AURA: ", ("assistant_label",))
            self.chat_output.insert("end", new_text, ("assistant_text",))
            self._last_assistant_message = new_text.strip()
        self.chat_output.configure(state="disabled")
        self.chat_output.see("end")

    def start_voice_engine(self) -> None:
        if self._mic is None:
            self.popup_until = time.perf_counter() + 2.3
            self.popup_title = "VOICE ENGINE OFFLINE"
            self.popup_text = "mic.py not available"
            if _MIC_IMPORT_ERROR:
                self._chat_append("assistant", f"Mic unavailable: {_MIC_IMPORT_ERROR}\n")
            return

        def on_text(text: str) -> None:
            self.run_on_ui(lambda t=text: self._handle_voice_text(t))

        # Hint for the user + quick sanity check for macOS permissions.
        self._chat_append("assistant", "Mic: listening (speak after a short pause)…\n")
        self._mic.start(on_text)
        self.root.after(1800, self._report_mic_start_status)

    def _report_mic_start_status(self) -> None:
        if not self.listening or self._mic is None:
            return
        status = getattr(self._mic, "status", "")
        last_error = getattr(self._mic, "last_error", "")
        if "error" in status.lower() or "failed" in status.lower() or "device" in status.lower():
            detail = f" ({last_error})" if last_error else ""
            self._chat_append("assistant", f"{status}{detail}\n")

    def stop_voice_engine(self) -> None:
        if self._mic is None:
            return
        self._mic.stop()

    def _handle_voice_text(self, text: str) -> None:
        # If mic was toggled off while processing, ignore.
        if not self.listening:
            return
        self._send_text(text)

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
        frame_start = time.perf_counter()
        dt = max(0.001, frame_start - self.last_frame_time)
        self.last_frame_time = frame_start

        self.frame_samples.append(1.0 / dt)
        if len(self.frame_samples) > 30:
            self.frame_samples.pop(0)
        avg_fps = sum(self.frame_samples) / len(self.frame_samples)
        self.fps_text = f"FPS {int(min(60, avg_fps))}"

        t = frame_start - self.runtime_start
        self.grid_shift = (self.grid_shift + dt * (16 if self.listening else 8)) % 60

        self.canvas.delete("all")
        self.draw_background(t)
        self.draw_layout_panels(t)
        self.draw_assistant_core(t)
        self.draw_analog_clock()
        self.draw_popup(t)

        if not self._closing:
            self.root.after(self.FRAME_MS, self.animate)
######
    def draw_background(self, t: float) -> None:
        self.canvas.create_rectangle(0, 0, self.width, self.height, fill=self.bg_main, outline="")

        bands = [
            ("#0f0a06", 0.00, 0.18),
            ("#120d08", 0.18, 0.38),
            ("#15100a", 0.38, 0.62),
            ("#0d0905", 0.62, 1.00),
        ]
        for color, top_ratio, bottom_ratio in bands:
            self.canvas.create_rectangle(
                0,
                self.height * top_ratio,
                self.width,
                self.height * bottom_ratio,
                fill=color,
                outline="",
            )

        glow_specs = [
            (self.width * 0.24, self.height * 0.18, 340, 180, "#2a1a0a"),
            (self.width * 0.76, self.height * 0.22, 300, 150, "#25180c"),
            (self.width * 0.50, self.height * 0.48, 520, 180, "#140e07"),
        ]
        for gx, gy, gw, gh, color in glow_specs:
            self.canvas.create_oval(gx - gw, gy - gh, gx + gw, gy + gh, fill=color, outline="")

        horizon = self.height * 0.67
        self.canvas.create_rectangle(0, horizon, self.width, self.height, fill="#0a0704", outline="")

        vanish_x = self.width / 2
        for i in range(-12, 13):
            px = vanish_x + i * self.width * 0.06
            self.canvas.create_line(vanish_x, horizon, px, self.height, fill="#2a1c0e", width=1)

        for i in range(11):
            y = horizon + ((i * 42 + self.grid_shift * (1 + i * 0.08)) % (self.height - horizon + 44))
            scale = (y - horizon + 25) / max(80, self.height - horizon + 25)
            edge_pad = (1 - scale) * self.width * 0.42
            color = "#3d2815" if i % 2 == 0 else "#2a1c0e"
            self.canvas.create_line(edge_pad, y, self.width - edge_pad, y, fill=color, width=max(1, scale * 1.8))

        arc_points = []
        wave_y = self.height * 0.29
        for x in range(-20, self.width + 21, 18):
            y = wave_y + math.sin(x * 0.008 + t * 0.55) * 10 + math.cos(x * 0.016 + t * 0.35) * 7
            arc_points.extend((x, y))
        self.canvas.create_line(arc_points, fill="#3d2a15", smooth=True, width=2)

        for i, particle in enumerate(self.star_particles):
            px = (particle["x"] * self.width + math.sin(t * (0.25 + particle["speed"]) + particle["phase"]) * particle["drift"]) % self.width
            py = (particle["y"] * self.height + t * 6 * particle["speed"] * particle["depth"]) % self.height
            size = particle["size"] * (0.9 + 0.15 * math.sin(t * 1.2 + i))
            color = "#ffb347" if i % 4 else "#ff9f00"
            self.canvas.create_oval(px - size, py - size, px + size, py + size, fill=color, outline="")

        for i in range(9):
            y = 80 + i * 52
            alpha_line = "#120d08" if i % 2 == 0 else "#0f0a06"
            self.canvas.create_line(0, y, self.width, y, fill=alpha_line, width=1)

        self.canvas.create_text(
            28,
            self.height - 26,
            anchor="w",
            text="AURA VOICE INTERFACE // LOW LATENCY VISUAL ENGINE",
            fill="#8a7050",
            font=("Helvetica", 10, "bold"),
        )
####
    def draw_layout_panels(self, t: float) -> None:
        margin = 28
        top_y = 24
        left_w = min(380, self.width * 0.28)
        right_w = min(370, self.width * 0.28)
        top_h = 134
        lower_h = 170

        self.draw_glass_panel(margin, top_y, margin + left_w, top_y + top_h, title="SYSTEM")
        self.draw_glass_panel(self.width - right_w - margin, top_y, self.width - margin, top_y + top_h, title="ENVIRONMENT")
        self.draw_glass_panel(margin, self.height - lower_h - 34, margin + left_w, self.height - 34, title="ASSISTANT STATE")

        lx = margin + 22
        self.canvas.create_text(lx, top_y + 46, anchor="w", text=self.time_text, fill="#fff8f0", font=("Helvetica", 30, "bold"))
        self.canvas.create_text(lx, top_y + 80, anchor="w", text=self.date_text, fill="#c9a96e", font=("Helvetica", 12))
        self.canvas.create_text(lx, top_y + 104, anchor="w", text=self.clip_text(f"{self.fps_text}   •   {self.device_text}", 40), fill="#9a7d5a", font=("Helvetica", 10, "bold"))
        self.canvas.create_text(lx, top_y + 123, anchor="w", text=self.clip_text(self.stats_text, 42), fill="#8a7050", font=("Helvetica", 10))

        rx = self.width - right_w - margin + 22
        self.canvas.create_text(rx, top_y + 46, anchor="w", text=self.clip_text(self.battery_text, 28), fill="#fff8f0", font=("Helvetica", 16, "bold"))
        self.canvas.create_text(rx, top_y + 74, anchor="w", text=self.clip_text(self.weather_text, 38), fill="#ffb347", font=("Helvetica", 11))
        self.canvas.create_text(rx, top_y + 103, anchor="w", text=self.clip_text(self.network_text, 38), fill="#8a7050", font=("Helvetica", 10))
        self.canvas.create_text(rx, top_y + 122, anchor="w", text="MIC STATE: " + ("ACTIVE" if self.listening else "STANDBY"), fill="#c9a96e", font=("Helvetica", 10, "bold"))

        lower_x = margin + 22
        self.canvas.create_text(lower_x, self.height - lower_h, anchor="w", text="VOICE ENGINE", fill="#f0e6d3", font=("Helvetica", 15, "bold"))
        self.canvas.create_text(
            lower_x,
            self.height - lower_h + 30,
            anchor="w",
            text="Mode: " + ("Listening / ready to transcribe" if self.listening else "Idle / waiting for user trigger"),
            fill="#b8a080",
            font=("Helvetica", 11),
        )
        self.canvas.create_text(
            lower_x,
            self.height - lower_h + 56,
            anchor="w",
            text=self.clip_text(self._get_mic_status_text(), 42),
            fill="#8a7050",
            font=("Helvetica", 10),
        )
        self.canvas.create_text(
            lower_x,
            self.height - lower_h + 76,
            anchor="w",
            text=self.clip_text(self._get_mic_debug_text(), 48),
            fill="#8a7050",
            font=("Helvetica", 10),
        )
        self.canvas.create_text(
            lower_x,
            self.height - lower_h + 96,
            anchor="w",
            text="Click the center core to toggle microphone state",
            fill="#ffb347",
            font=("Helvetica", 10, "bold"),
        )

        meter_x = lower_x
        meter_y = self.height - 84
        self.draw_level_meter(meter_x, meter_y, 220, 10, t, self.listening)

        self.canvas.create_text(
            self.width / 2,
            64,
            text="A U R A",
            fill="#fff8f0",
            font=("Helvetica", 24, "bold"),
        )
        self.canvas.create_text(
            self.width / 2,
            89,
            text="offline voice intelligence interface",
            fill="#8a7050",
            font=("Helvetica", 10),
        )

        # CHAT PANEL (new block)
        chat_x1 = self.width - right_w - margin
        chat_x2 = self.width - margin
        chat_y1 = top_y + top_h + 18
        reserve = max(210, min(270, self.height * 0.30))
        chat_y2 = max(chat_y1 + 210, self.height - 34 - reserve)
        self.draw_glass_panel(chat_x1, chat_y1, chat_x2, chat_y2, title="CHAT")

        chat_w = max(240, int(chat_x2 - chat_x1 - 20))
        chat_h = max(180, int(chat_y2 - chat_y1 - 34))
        self.chat_model_label.configure(text=self.model_status_text)

        # Place as an overlay (much cheaper than canvas.create_window every frame).
        gx = int(chat_x1 + 10)
        gy = int(chat_y1 + 24)
        gw = int(chat_w)
        gh = int(chat_h)
        geom = (gx, gy, gw, gh)
        if geom != self._chat_overlay_geom:
            self._chat_overlay_geom = geom
            self.chat_frame.place(x=gx, y=gy, width=gw, height=gh)

    def draw_glass_panel(self, x1: float, y1: float, x2: float, y2: float, title: str) -> None:
        self.canvas.create_rectangle(x1, y1, x2, y2, fill=self.panel_fill, outline=self.panel_line, width=1)
        self.canvas.create_rectangle(x1 + 1, y1 + 1, x2 - 1, y1 + 18, fill="#1a1209", outline="")
        self.canvas.create_line(x1 + 14, y1 + 18, x2 - 14, y1 + 18, fill="#2a1c0e", width=1)

        cut = 14
        self.canvas.create_line(x1, y1 + cut, x1, y1, x1 + cut, y1, fill="#b8860b", width=2)
        self.canvas.create_line(x2 - cut, y1, x2, y1, x2, y1 + cut, fill="#b8860b", width=2)
        self.canvas.create_line(x1, y2 - cut, x1, y2, x1 + cut, y2, fill="#5c3d1e", width=2)
        self.canvas.create_line(x2 - cut, y2, x2, y2, x2, y2 - cut, fill="#5c3d1e", width=2)

        self.canvas.create_text(x1 + 16, y1 + 9, anchor="w", text=title, fill="#d4a76a", font=("Helvetica", 9, "bold"))

    def draw_level_meter(self, x: float, y: float, width: float, height: float, t: float, active: bool) -> None:
        self.canvas.create_rectangle(x, y, x + width, y + height, fill="#07111f", outline="#3d2815", width=1)
        segments = 16
        gap = 3
        seg_w = (width - gap * (segments + 1)) / segments
        for i in range(segments):
            sx1 = x + gap + i * (seg_w + gap)
            sx2 = sx1 + seg_w
            intensity = 0.18
            if active:
                intensity = 0.35 + 0.65 * max(0, math.sin(t * 4.6 + i * 0.42))
            fill = self.mix_color("#2a1c0e", "#ffaa00", intensity)
            self.canvas.create_rectangle(sx1, y + 2, sx2, y + height - 2, fill=fill, outline="")

    def draw_analog_clock(self) -> None:
        r = max(64, min(self.width, self.height) * 0.082)
        cx = self.width - r - 44
        cy = self.height - r - 42

        self.canvas.create_oval(cx - r - 14, cy - r - 14, cx + r + 14, cy + r + 14, fill="#0a0704", outline="")
        self.canvas.create_oval(cx - r, cy - r, cx + r, cy + r, fill="#0f0a06", outline="#5c3d1e", width=2)
        self.canvas.create_oval(cx - r * 0.84, cy - r * 0.84, cx + r * 0.84, cy + r * 0.84, outline="#2a1c0e", width=1)

        for i in range(60):
            angle = math.radians(i * 6 - 90)
            inner = r * (0.76 if i % 5 == 0 else 0.86)
            outer = r * 0.94
            color = "#fff8f0" if i % 5 == 0 else "#8a7050"
            width = 2 if i % 5 == 0 else 1
            self.canvas.create_line(
                cx + math.cos(angle) * inner,
                cy + math.sin(angle) * inner,
                cx + math.cos(angle) * outer,
                cy + math.sin(angle) * outer,
                fill=color,
                width=width,
            )

        now = datetime.now()
        second = now.second + now.microsecond / 1_000_000
        minute = now.minute + second / 60
        hour = (now.hour % 12) + minute / 60

        self.draw_hand(cx, cy, r * 0.46, hour * 30 - 90, "#fff8f0", 4)
        self.draw_hand(cx, cy, r * 0.66, minute * 6 - 90, "#ffb347", 3)
        self.draw_hand(cx, cy, r * 0.80, second * 6 - 90, "#ff8c42", 2)

        self.canvas.create_oval(cx - 5, cy - 5, cx + 5, cy + 5, fill="#fff8f0", outline="")
        self.canvas.create_text(cx, cy + r + 18, text="CLOCK", fill="#a08060", font=("Helvetica", 10, "bold"))

    def draw_hand(self, cx: float, cy: float, length: float, angle_deg: float, color: str, width: int) -> None:
        angle = math.radians(angle_deg)
        x = cx + math.cos(angle) * length
        y = cy + math.sin(angle) * length
        self.canvas.create_line(cx, cy, x, y, fill=color, width=width, capstyle=tk.ROUND)

    def draw_assistant_core(self, t: float) -> None:
        cx, cy = self.center
        base_r = min(self.width, self.height) * 0.106
        pulse = 1.0 + (0.03 * math.sin(t * 2.2) if self.listening else 0.0)
        orbit_r = base_r * 1.55

        ring_colors = ["#2a1c0e", "#3d2815", "#4a3218"]
        ring_scales = [2.0, 1.68, 1.36]
        for color, scale in zip(ring_colors, ring_scales):
            rr = base_r * scale
            self.canvas.create_oval(cx - rr, cy - rr, cx + rr, cy + rr, outline=color, width=1)

        if self.listening:
            for i in range(3):
                rr = base_r * (1.18 + i * 0.18 + 0.04 * math.sin(t * (2.0 + i * 0.4)))
                line = self.mix_color("#5c3d1e", self.accent_primary, 0.35 + i * 0.18)
                self.canvas.create_oval(cx - rr, cy - rr, cx + rr, cy + rr, outline=line, width=2)

        disk_r = base_r * pulse
        self.canvas.create_oval(cx - disk_r, cy - disk_r, cx + disk_r, cy + disk_r, fill="#0f0a06", outline="#5c3d1e", width=2)

        inner_r = base_r * 0.74 * pulse
        self.canvas.create_oval(
            cx - inner_r,
            cy - inner_r,
            cx + inner_r,
            cy + inner_r,
            fill="#140e07",
            outline=self.mix_color("#5c3d1e", "#ffb347", 0.9 if self.listening else 0.22),
            width=2,
        )

        core_r = base_r * 0.38 * pulse
        self.canvas.create_oval(
            cx - core_r,
            cy - core_r,
            cx + core_r,
            cy + core_r,
            fill=self.mix_color("#2a1c0e", "#ffaa00", 0.88 if self.listening else 0.28),
            outline="",
        )

        for i in range(48):
            angle = math.tau * i / 48
            start_r = base_r * 0.92
            end_r = base_r * 1.12
            bar = 1.0
            if self.listening:
                bar += 0.18 * max(0.0, math.sin(t * 5.6 + i * 0.34))
            x1 = cx + math.cos(angle) * start_r
            y1 = cy + math.sin(angle) * start_r
            x2 = cx + math.cos(angle) * end_r * bar
            y2 = cy + math.sin(angle) * end_r * bar
            color = self.mix_color("#3d2815", "#ffb347", 0.85 if self.listening else 0.18)
            self.canvas.create_line(x1, y1, x2, y2, fill=color, width=2 if i % 6 == 0 else 1)

        self.canvas.create_text(cx, cy - base_r - 58, text="VOICE CORE", fill="#a08060", font=("Helvetica", 11, "bold"))
        self.canvas.create_text(cx, cy + base_r + 34, text="tap to toggle microphone", fill="#9a7d5a", font=("Helvetica", 11))
        self.canvas.create_text(
            cx,
            cy + base_r + 60,
            text="LISTENING" if self.listening else "IDLE",
            fill="#ffb347" if self.listening else "#8a7050",
            font=("Helvetica", 20, "bold"),
        )

        for i, color in enumerate(self.orb_palette):
            base_angle = i * (math.tau / len(self.orb_palette))
            angle = base_angle + (t * (0.22 + i * 0.015) if self.listening else 0.0)
            local_r = orbit_r + (5 * math.sin(t * 1.8 + i) if self.listening else 0.0)
            ox = cx + math.cos(angle) * local_r
            oy = cy + math.sin(angle) * local_r * 0.72
            size = 4.2 + (0.8 * math.sin(t * 2.1 + i) if self.listening else 0.0)

            self.canvas.create_oval(ox - size * 2.2, oy - size * 2.2, ox + size * 2.2, oy + size * 2.2, outline=self.mix_color("#3d2815", color, 0.55), width=1)
            self.canvas.create_oval(ox - size, oy - size, ox + size, oy + size, fill=self.mix_color("#5c3d1e", color, 0.78), outline="")

            if self.listening:
                tail_x = cx + math.cos(angle - 0.06) * (local_r - 10)
                tail_y = cy + math.sin(angle - 0.06) * ((local_r - 10) * 0.72)
                self.canvas.create_line(tail_x, tail_y, ox, oy, fill=self.mix_color("#4a3218", color, 0.45), width=1)

    def draw_popup(self, t: float) -> None:
        if time.perf_counter() > self.popup_until:
            return

        cx, cy = self.center
        w = min(360, self.width * 0.28)
        h = 86
        x1 = cx - w / 2
        y1 = cy - min(180, self.height * 0.24)
        y_offset = math.sin(t * 3.2) * 2.2
        y1 += y_offset
        x2 = x1 + w
        y2 = y1 + h

        self.canvas.create_rectangle(x1, y1, x2, y2, fill="#140e07", outline="#5c3d1e", width=1)
        self.canvas.create_rectangle(x1 + 1, y1 + 1, x2 - 1, y1 + 16, fill="#1a1209", outline="")
        self.canvas.create_line(x1 + 14, y1 + 16, x2 - 14, y1 + 16, fill="#3d2815", width=1)

        self.canvas.create_text(cx, y1 + 31, text=self.popup_title, fill="#fff8f0", font=("Helvetica", 13, "bold"))
        self.canvas.create_text(cx, y1 + 57, text=self.popup_text, fill="#ffb347", font=("Helvetica", 18, "bold"))

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
        return clean[: max(0, limit - 1)].rstrip() + "…"

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    CyberAssistantUI().run()
