from __future__ import annotations

import json
import ssl
import threading
import time
import urllib.request

import certifi
import CoreLocation
import Foundation
import objc


class LocationManager(Foundation.NSObject):
    def init(self):
        self = objc.super(LocationManager, self).init()
        if self is None:
            return None
        self.location = None
        self.event = threading.Event()
        return self

    def locationManager_didUpdateLocations_(self, manager, locations):
        if locations:
            loc = locations[-1]
            self.location = (
                loc.coordinate().latitude,
                loc.coordinate().longitude,
            )
            self.event.set()

    def locationManager_didFailWithError_(self, manager, error):
        self.event.set()


def _ssl_context():
    return ssl.create_default_context(cafile=certifi.where())


def get_precise_location(timeout: int = 15):
    manager = CoreLocation.CLLocationManager.alloc().init()
    delegate = LocationManager.alloc().init()
    manager.setDelegate_(delegate)

    if not CoreLocation.CLLocationManager.locationServicesEnabled():
        raise RuntimeError("macOS location services disabled.")

    manager.requestWhenInUseAuthorization()
    manager.startUpdatingLocation()

    run_loop = Foundation.NSRunLoop.currentRunLoop()
    start = Foundation.NSDate.date()
    while not delegate.event.is_set() and abs(start.timeIntervalSinceNow()) < timeout:
        run_loop.runUntilDate_(
            Foundation.NSDate.dateWithTimeIntervalSinceNow_(0.1)
        )

    manager.stopUpdatingLocation()
    if delegate.location is None:
        raise RuntimeError("Could not fetch precise GPS location.")
    return delegate.location


def reverse_geocode(latitude, longitude):
    url = (
        "https://nominatim.openstreetmap.org/reverse"
        f"?format=jsonv2&lat={latitude}&lon={longitude}&zoom=18&addressdetails=1"
    )
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "WeatherApp/1.0 (macOS Python Script)"},
    )

    time.sleep(1)
    with urllib.request.urlopen(request, context=_ssl_context(), timeout=10) as response:
        address = json.loads(response.read().decode("utf-8")).get("address", {})

    return {
        "area": (
            address.get("suburb")
            or address.get("neighbourhood")
            or address.get("quarter")
            or address.get("city_district")
            or address.get("district")
            or address.get("borough")
            or address.get("residential")
            or address.get("hamlet")
            or address.get("county")
            or "Unknown"
        ),
        "city": (
            address.get("city")
            or address.get("town")
            or address.get("municipality")
            or address.get("village")
            or address.get("county")
            or address.get("state_district")
            or "Unknown"
        ),
        "state": address.get("state") or address.get("region") or "Unknown",
        "country": address.get("country") or "Unknown",
    }


def fetch_weather(latitude, longitude):
    url = (
        "https://api.open-meteo.com/v1/forecast?"
        f"latitude={latitude}&longitude={longitude}"
        "&current=temperature_2m,relative_humidity_2m,wind_speed_10m"
    )
    with urllib.request.urlopen(url, context=_ssl_context(), timeout=10) as response:
        return json.loads(response.read().decode()).get("current", {})


def get_weather_report():
    print("\n📡 Fetching precise GPS location...")
    latitude, longitude = get_precise_location()
    print("📍 GPS location acquired.")

    print("\n🌍 Fetching address details...")
    location = reverse_geocode(latitude, longitude)

    print("🌤 Fetching weather...")
    weather = fetch_weather(latitude, longitude)

    return f"""

=========================================================
🌍 LOCATION DETAILS
=========================================================

Country     : {location['country']}
State       : {location['state']}
City        : {location['city']}
Area        : {location['area']}

=========================================================
📍 GPS COORDINATES
=========================================================

Latitude    : {latitude}
Longitude   : {longitude}

=========================================================
🌤 WEATHER DETAILS
=========================================================

Temperature : {weather.get('temperature_2m', 'N/A')} °C
Humidity    : {weather.get('relative_humidity_2m', 'N/A')} %
Wind Speed  : {weather.get('wind_speed_10m', 'N/A')} km/h

=========================================================
"""


if __name__ == "__main__":
    try:
        print(get_weather_report())
    except Exception as exc:
        print("\n❌ ERROR")
        print(type(exc).__name__)
        print(exc)
