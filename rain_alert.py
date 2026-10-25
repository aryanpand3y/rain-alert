"""
Strict rain alert: pushes a notification to your phone via ntfy ONLY when
proper rain (not drizzle) is falling at your location right now.

Needs 3 environment variables (set as GitHub secrets):
  LAT, LON     - your building's coordinates
  NTFY_TOPIC   - your private ntfy topic name
"""
import json
import os
import urllib.request

LAT = os.environ["LAT"]
LON = os.environ["LON"]
TOPIC = os.environ["NTFY_TOPIC"]

STATE_FILE = "state.json"

# Strict: ~1 mm in 15 min (about 4 mm/hour) = clearly noticeable rain.
RAIN_NOW_MM = 1.0

# WMO weather codes for real rain / showers / thunderstorms (drizzle excluded)
RAIN_CODES = set(range(61, 68)) | {80, 81, 82, 95, 96, 99}


def get_weather():
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={LAT}&longitude={LON}"
        "&current=precipitation,rain,showers,weather_code"
        "&timezone=Asia%2FKolkata"
    )
    with urllib.request.urlopen(url, timeout=20) as r:
        return json.load(r)


def notify(title, message):
    req = urllib.request.Request(
        f"https://ntfy.sh/{TOPIC}",
        data=message.encode("utf-8"),
        headers={"Title": title, "Priority": "urgent", "Tags": "umbrella"},
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
    amount = max(
        cur.get("precipitation") or 0,
        (cur.get("rain") or 0) + (cur.get("showers") or 0),
    )
    code = cur.get("weather_code")

    # BOTH must be true: real rain type AND a heavy-enough amount.
    raining_now = code in RAIN_CODES and amount >= RAIN_NOW_MM

    prev = load_state()
    if raining_now and prev != "raining":
        notify("It's raining now", "Proper rain has started at your place. Get the clothes in!")
    new = "raining" if raining_now else "dry"

    print(f"code={code} amount={amount}mm raining={raining_now} state: {prev} -> {new}")
    if new != prev:
        save_state(new)


if __name__ == "__main__":
    main()
