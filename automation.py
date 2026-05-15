from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import ssl
import subprocess
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from typing import Optional

try:
    import certifi  # type: ignore
except Exception:
    certifi = None

try:
    import psutil  # type: ignore
except Exception:
    psutil = None

try:
    import weather as weather_service  # type: ignore
except Exception:
    weather_service = None


USER_AGENT = "SYLVIA/1.0 local desktop assistant"


@dataclass
class AutomationResult:
    text: str
    source: str = "automation"


class HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._in_title = False
        self._skip_depth = 0
        self._chunks: list[str] = []
        self.links: list[tuple[str, str]] = []
        self._active_href: str | None = None
        self._active_link_text: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "svg", "noscript", "template"}:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True
        if tag == "a":
            attrs_dict = dict(attrs)
            self._active_href = attrs_dict.get("href")
            self._active_link_text = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "svg", "noscript", "template"} and self._skip_depth:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False
        if tag == "a" and self._active_href:
            text = " ".join(" ".join(self._active_link_text).split())
            if text:
                self.links.append((text, self._active_href))
            self._active_href = None
            self._active_link_text = []

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        clean = " ".join(data.split())
        if not clean:
            return
        if self._in_title:
            self.title = f"{self.title} {clean}".strip()
        elif self._active_href:
            self._active_link_text.append(clean)
        else:
            self._chunks.append(clean)

    @property
    def text(self) -> str:
        return " ".join(" ".join(self._chunks).split())


class TaskAutomation:
    def __init__(self) -> None:
        self._ssl_context = ssl.create_default_context(
            cafile=certifi.where() if certifi else None
        )

    def answer(self, user_text: str) -> Optional[AutomationResult]:
        text = " ".join((user_text or "").split())
        normalized = text.lower().strip()
        if not normalized:
            return None

        url = self._extract_url(text)
        if url:
            return AutomationResult(self.extract_url(url), source="web")

        if self._wants_weather(normalized):
            return AutomationResult(self.weather_report(text), source="weather")

        if self._wants_system_summary(normalized):
            return AutomationResult(self.system_summary(), source="system")

        if self._wants_battery(normalized):
            return AutomationResult(self.battery_status(), source="system")

        if self._wants_storage(normalized):
            return AutomationResult(self.storage_status(), source="system")

        if self._wants_time(normalized):
            return AutomationResult(self.time_status(), source="system")

        if self._wants_web_search(normalized):
            query = self._extract_search_query(text)
            if query:
                return AutomationResult(self.search_web(query), source="web")

        return None

    def time_status(self) -> str:
        now = datetime.now()
        return f"It is {now.strftime('%I:%M %p').lstrip('0')} on {now.strftime('%A, %d %B %Y')}."

    def battery_status(self) -> str:
        if psutil:
            try:
                battery = psutil.sensors_battery()
                if battery:
                    mode = "charging" if battery.power_plugged else "on battery"
                    return f"Battery is at {int(battery.percent)}%, {mode}."
            except Exception:
                pass

        if platform.system() == "Darwin":
            try:
                output = subprocess.check_output(
                    ["pmset", "-g", "batt"],
                    text=True,
                    stderr=subprocess.DEVNULL,
                    timeout=3,
                )
                state = "charging" if "AC Power" in output else "on battery"
                percent = re.search(r"(\d+)%", output)
                if percent:
                    return f"Battery is at {percent.group(1)}%, {state}."
            except Exception:
                pass
        return "I could not read the battery percentage."

    def storage_status(self) -> str:
        usage = shutil.disk_usage(os.path.abspath(os.sep))
        total = self._format_bytes(usage.total)
        used = self._format_bytes(usage.used)
        free = self._format_bytes(usage.free)
        percent = int((usage.used / usage.total) * 100) if usage.total else 0
        return f"Storage: {used} used of {total}. Free space is {free}. Disk is {percent}% full."

    def system_summary(self) -> str:
        pieces = [self.time_status(), self.battery_status(), self.storage_status()]
        if psutil:
            try:
                cpu = int(psutil.cpu_percent(interval=0.1))
                ram = int(psutil.virtual_memory().percent)
                pieces.append(f"CPU is around {cpu}% and memory is around {ram}%.")
            except Exception:
                pass
        return " ".join(pieces)

    def weather_report(self, user_text: str) -> str:
        city = self._extract_weather_city(user_text)
        if city:
            return self._weather_for_city(city)

        if weather_service is None:
            return "Weather module is not available."
        try:
            latitude, longitude = weather_service.get_precise_location(timeout=6)
            location = weather_service.reverse_geocode(latitude, longitude)
            current = weather_service.fetch_weather(latitude, longitude)
            return self._format_weather(location, current)
        except Exception as exc:
            return f"I could not fetch current weather right now: {type(exc).__name__}."

    def extract_url(self, url: str, limit: int = 900) -> str:
        page_url, html = self._fetch(url)
        extractor = HTMLTextExtractor()
        extractor.feed(html)
        title = extractor.title or page_url
        body = self._clean_web_text(extractor.text)
        if not body:
            return f"I opened {title}, but could not extract readable page text."
        return f"{title}: {body[:limit].strip()}"

    def search_web(self, query: str, max_results: int = 3) -> str:
        url = "https://duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
        _, html = self._fetch(url)
        extractor = HTMLTextExtractor()
        extractor.feed(html)

        results: list[tuple[str, str]] = []
        seen: set[str] = set()
        for title, href in extractor.links:
            clean_href = self._normalize_duckduckgo_href(href)
            if not clean_href.startswith(("http://", "https://")):
                continue
            clean_title = self._clean_web_text(title)
            if not clean_title or clean_href in seen:
                continue
            if "duckduckgo.com" in urllib.parse.urlparse(clean_href).netloc:
                continue
            seen.add(clean_href)
            results.append((clean_title, clean_href))
            if len(results) >= max_results:
                break

        if not results:
            return f"I could not find useful web results for {query!r}."
        lines = [f"Top web results for {query}:"]
        for index, (title, href) in enumerate(results, start=1):
            lines.append(f"{index}. {title} - {href}")
        return "\n".join(lines)

    def speak(self, text: str, play: bool = True) -> str:
        import tts

        engine = tts.get_default_tts()
        return engine.speak(text, play=play)

    def _fetch(self, url: str) -> tuple[str, str]:
        if not re.match(r"^https?://", url, flags=re.IGNORECASE):
            url = "https://" + url
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(
            request,
            context=self._ssl_context,
            timeout=12,
        ) as response:
            final_url = response.geturl()
            content_type = response.headers.get_content_charset() or "utf-8"
            body = response.read(1_500_000)
        return final_url, body.decode(content_type, errors="replace")

    def _weather_for_city(self, city: str) -> str:
        geo_url = "https://geocoding-api.open-meteo.com/v1/search?" + urllib.parse.urlencode(
            {"name": city, "count": 1, "language": "en", "format": "json"}
        )
        _, geo_body = self._fetch(geo_url)
        geo_data = json.loads(geo_body)
        results = geo_data.get("results") or []
        if not results:
            return f"I could not find weather coordinates for {city}."

        place = results[0]
        latitude = place["latitude"]
        longitude = place["longitude"]
        weather_url = (
            "https://api.open-meteo.com/v1/forecast?"
            + urllib.parse.urlencode(
                {
                    "latitude": latitude,
                    "longitude": longitude,
                    "current": "temperature_2m,relative_humidity_2m,wind_speed_10m",
                }
            )
        )
        _, weather_body = self._fetch(weather_url)
        current = json.loads(weather_body).get("current", {})
        location = {
            "city": place.get("name", city),
            "state": place.get("admin1", ""),
            "country": place.get("country", ""),
        }
        return self._format_weather(location, current)

    def _format_weather(self, location: dict, current: dict) -> str:
        city = location.get("city") or location.get("area") or "your area"
        country = location.get("country") or ""
        temperature = current.get("temperature_2m", "N/A")
        humidity = current.get("relative_humidity_2m", "N/A")
        wind_speed = current.get("wind_speed_10m", "N/A")
        place = f"{city}, {country}".strip(", ")
        return f"Weather in {place}: {temperature}°C, humidity {humidity}%, wind {wind_speed} km/h."

    def _extract_url(self, text: str) -> str | None:
        match = re.search(r"https?://\S+|www\.\S+", text, flags=re.IGNORECASE)
        if not match:
            return None
        return match.group(0).rstrip(").,!?")

    def _wants_web_search(self, normalized: str) -> bool:
        return any(
            phrase in normalized
            for phrase in (
                "search web",
                "search internet",
                "look up",
                "google",
                "find online",
                "web search",
                "internet search",
            )
        )

    def _wants_weather(self, normalized: str) -> bool:
        return "weather" in normalized or "temperature outside" in normalized

    def _wants_system_summary(self, normalized: str) -> bool:
        return any(phrase in normalized for phrase in ("system status", "system data", "system info", "device status"))

    def _wants_battery(self, normalized: str) -> bool:
        return "battery" in normalized or "power percentage" in normalized

    def _wants_storage(self, normalized: str) -> bool:
        return any(word in normalized for word in ("storage", "disk space", "free space", "space left"))

    def _wants_time(self, normalized: str) -> bool:
        time_phrases = ("what time", "current time", "tell me time", "date today", "today's date", "what date")
        return normalized in {"time", "date"} or any(phrase in normalized for phrase in time_phrases)

    def _extract_search_query(self, text: str) -> str:
        clean = re.sub(
            r"^(please\s+)?(search web|search internet|web search|internet search|look up|google|find online)\s+(for\s+)?",
            "",
            text,
            flags=re.IGNORECASE,
        )
        return clean.strip(" .?!")

    def _extract_weather_city(self, text: str) -> str:
        match = re.search(r"\bweather\s+(?:in|for|at)\s+(.+)$", text, flags=re.IGNORECASE)
        if not match:
            return ""
        return match.group(1).strip(" .?!")

    def _normalize_duckduckgo_href(self, href: str) -> str:
        if href.startswith("//"):
            href = "https:" + href
        parsed = urllib.parse.urlparse(href)
        params = urllib.parse.parse_qs(parsed.query)
        if "uddg" in params:
            return urllib.parse.unquote(params["uddg"][0])
        return href

    def _clean_web_text(self, text: str) -> str:
        clean = re.sub(r"\s+", " ", text or "").strip()
        clean = re.sub(r"Cookie Policy|Privacy Policy|Terms of Service", "", clean, flags=re.IGNORECASE)
        return clean.strip()

    def _format_bytes(self, value: int) -> str:
        amount = float(value)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if amount < 1024 or unit == "TB":
                if unit in {"B", "KB", "MB"}:
                    return f"{amount:.0f} {unit}"
                return f"{amount:.1f} {unit}"
            amount /= 1024
        return f"{amount:.1f} TB"


def main() -> int:
    parser = argparse.ArgumentParser(description="SYLVIA local task automation")
    parser.add_argument("query", nargs="+", help="Task or question to answer")
    parser.add_argument("--speak", action="store_true", help="Speak the answer using local TTS")
    args = parser.parse_args()

    automation = TaskAutomation()
    result = automation.answer(" ".join(args.query))
    text = result.text if result else "I do not have an automation for that yet."
    print(text)
    if args.speak:
        automation.speak(text, play=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
