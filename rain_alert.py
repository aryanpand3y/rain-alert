"""
Minimal rain alert: checks Open-Meteo for your location and pushes a
notification to your phone via ntfy when rain is about to start or has started.

Needs 3 environment variables (set as GitHub secrets):
  LAT, LON     - your building's coordinates (e.g. 12.9716, 77.5946)
  NTFY_TOPIC   - your private ntfy topic name (e.g. aryan-terrace-rain-8273)
"""
import json
import os
import urllib.request

LAT = os.environ["LAT"]
LON = os.environ["LON"]
TOPIC = os.environ["NTFY_TOPIC"]

STATE_FILE = "state.json"
RAIN_NOW_MM = 0.5      # mm in the current period that counts as "raining"
RAIN_SOON_MM = 0.5     # mm in a 15-min slot that counts as "rain coming"
LOOKAHEAD_SLOTS = 3    # 3 x 15 min = next ~45 minutes

# WMO weather codes that mean rain/drizzle/showers/thunderstorm
RAIN_CODES = set(range(61, 68)) | {80, 81, 82, 95, 96, 99}


def get_weather():
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={LAT}&longitude={LON}"
        "&current=precipitation,rain,weather_code"
        "&minutely_15=precipitation"
        f"&forecast_minutely_15={LOOKAHEAD_SLOTS}"
        "&timezone=Asia%2FKolkata"
    )
    with urllib.request.urlopen(url, timeout=20) as r:
        return json.load(r)


def notify(title, message, priority="high"):
    req = urllib.request.Request(
        f"https://ntfy.sh/{TOPIC}",
        data=message.encode("utf-8"),
        headers={"Title": title, "Priority": priority, "Tags": "umbrella"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=20)


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f).get("state", "dry")
    except (FileNotFoundError, json.JSONDecodeError):
        return "dry"


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump({"state": state}, f)


def main():
    try:
        data = get_weather()
    except Exception as e:
        print(f"Weather check failed, skipping this run: {e}")
        return

    cur = data.get("current", {})
    raining_now = (
        (cur.get("precipitation") or 0) >= RAIN_NOW_MM
        or (cur.get("rain") or 0) >= RAIN_NOW_MM
        or cur.get("weather_code") in RAIN_CODES
    )
    upcoming = data.get("minutely_15", {}).get("precipitation", []) or []
    rain_soon = any((p or 0) >= RAIN_SOON_MM for p in upcoming)

    prev = load_state()

    if raining_now:
        if prev != "raining":
            notify("It's raining now", "Rain has started at your place. Get the clothes in!", "urgent")
        new = "raining"
    elif rain_soon:
        if prev == "dry":
            notify("Rain coming soon", "Rain likely in the next 30-45 minutes. Good time to grab the clothes.")
        new = "raining" if prev == "raining" else "soon"
    else:
        new = "dry"

    print(f"now={raining_now} soon={rain_soon} state: {prev} -> {new}")
    if new != prev:
        save_state(new)


if __name__ == "__main__":
    main()
