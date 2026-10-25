"""
Rain alert for clothes drying on the terrace, with a "clothes out" switch.

Alerts your phone via ntfy ONLY when you've marked your clothes as "out", and:
  - rain is likely in the next ~30-45 minutes  -> "Rain coming soon" (early warning)
  - rain right now                             -> "It's raining now"
  - moderate/dense drizzle right now           -> "Drizzle likely now"
Light drizzle (weather code 51, ~0.1 mm) is ignored: it caused false alarms.

Each run checks several times, a few minutes apart, because GitHub only starts
scheduled runs roughly every 30 minutes even when asked for every 10.

The switch: send "out" or "in" to the control topic <NTFY_TOPIC>-clothes
(the phone shortcuts do this for you). The alerts also have a
"Clothes collected" button that marks them "in".

Needs 3 environment variables (set as GitHub secrets):
  LAT, LON     - your building's coordinates
  NTFY_TOPIC   - your private ntfy topic name
Optional: CHECKS (checks per run, default 6), INTERVAL (seconds between checks, default 270)
"""
import json
import os
import time
import urllib.request

LAT = os.environ["LAT"]
LON = os.environ["LON"]
TOPIC = os.environ["NTFY_TOPIC"]
CONTROL_TOPIC = f"{TOPIC}-clothes"

CHECKS = int(os.environ.get("CHECKS", "6"))
INTERVAL = int(os.environ.get("INTERVAL", "270"))

STATE_FILE = "state.json"

# 0.2 mm in 15 min: real drizzle tonight showed 0.2 mm; the false alarms
# from "light drizzle" showed only 0.1 mm.
RAIN_NOW_MM = 0.2
# Early warning: this much rain forecast in one of the next 15-minute slots...
SOON_MM = 0.2
# ...or this chance of rain in the coming hour.
SOON_PROBABILITY = 70

# WMO weather codes: moderate/dense drizzle (53-57), rain (61-67), showers (80-82),
# thunderstorms (95-99). Light drizzle (51) is ignored: it caused false alarms.
DRIZZLE_CODES = set(range(53, 58))
RAIN_CODES = DRIZZLE_CODES | set(range(61, 68)) | {80, 81, 82, 95, 96, 99}


def get_json(url):
    with urllib.request.urlopen(url, timeout=20) as r:
        return json.load(r)


def get_weather():
    return get_json(
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={LAT}&longitude={LON}"
        "&current=precipitation,rain,showers,weather_code"
        "&minutely_15=precipitation,weather_code&forecast_minutely_15=8"
        "&hourly=precipitation_probability&forecast_hours=3"
        "&timezone=Asia%2FKolkata"
    )


def latest_clothes_message(after_time):
    """Return (status, time) of the newest 'out'/'in' message newer than after_time."""
    url = f"https://ntfy.sh/{CONTROL_TOPIC}/json?poll=1&since=12h"
    with urllib.request.urlopen(url, timeout=20) as r:
        lines = r.read().decode("utf-8").splitlines()
    newest = None
    for line in lines:
        try:
            m = json.loads(line)
        except json.JSONDecodeError:
            continue
        if m.get("event") != "message":
            continue
        text = (m.get("message") or "").strip().lower()
        t = m.get("time", 0)
        if text in ("out", "in") and t > after_time and (newest is None or t >= newest[1]):
            newest = (text, t)
    return newest


def notify(title, message):
    req = urllib.request.Request(
        f"https://ntfy.sh/{TOPIC}",
        data=message.encode("utf-8"),
        headers={
            "Title": title,
            "Priority": "urgent",
            "Tags": "umbrella",
            # Tap this button after collecting -> switch goes to "in"
            "Actions": f"http, Clothes collected, https://ntfy.sh/{CONTROL_TOPIC}, body=in, clear=true",
        },
        method="POST",
    )
    urllib.request.urlopen(req, timeout=20)


def load_state():
    state = {"rain": "dry", "alerted": False, "warned": False, "clothes": "in", "clothes_time": 0}
    try:
        with open(STATE_FILE) as f:
            saved = json.load(f)
        if "state" in saved and "rain" not in saved:  # old format
            saved = {"rain": saved["state"]}
        state.update(saved)
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return state


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=1, sort_keys=True)


def read_weather(data):
    """Return (raining_now, rain_soon, details) from an Open-Meteo response."""
    cur = data.get("current", {})
    amount = max(
        cur.get("precipitation") or 0,
        (cur.get("rain") or 0) + (cur.get("showers") or 0),
    )
    code = cur.get("weather_code")
    raining_now = code in RAIN_CODES or amount >= RAIN_NOW_MM

    # The API also returns slots from earlier in the hour, so keep only upcoming ones
    now = cur.get("time") or ""
    m15 = data.get("minutely_15", {})
    upcoming = [
        (p or 0, c)
        for t, p, c in zip(m15.get("time") or [], m15.get("precipitation") or [], m15.get("weather_code") or [])
        if t >= now
    ][:3]
    soon_mm = [p for p, _ in upcoming]
    soon_codes = [c for _, c in upcoming]
    hourly = data.get("hourly", {})
    this_hour = now[:13] + ":00"
    probs = [
        p or 0
        for t, p in zip(hourly.get("time") or [], hourly.get("precipitation_probability") or [])
        if t >= this_hour
    ][:2]
    rain_soon = (
        any(x >= SOON_MM for x in soon_mm)
        or any(c in RAIN_CODES for c in soon_codes)
        or any(p >= SOON_PROBABILITY for p in probs)
    )
    details = f"code={code} now={amount}mm next45min={soon_mm} codes={soon_codes} prob={probs}"
    return raining_now, rain_soon, details


def check_once(state):
    # 1) Update the clothes switch from the control topic
    try:
        latest = latest_clothes_message(state["clothes_time"])
        if latest:
            state["clothes"], state["clothes_time"] = latest
    except Exception as e:
        print(f"Clothes switch check failed, keeping '{state['clothes']}': {e}")

    # 2) Check the weather
    try:
        data = get_weather()
    except Exception as e:
        print(f"Weather check failed, skipping this check: {e}")
        return

    raining_now, rain_soon, details = read_weather(data)
    clothes_out = state["clothes"] == "out"

    # 3) Decide whether to alert (once per rain spell, only if clothes are out)
    if raining_now:
        state["rain"] = "raining"
        if clothes_out and not state["alerted"]:
            cur_code = (data.get("current") or {}).get("weather_code")
            if cur_code in DRIZZLE_CODES:
                notify("Drizzle likely now", "Weather data shows drizzle at your place and your clothes are out. Worth a quick look.")
            else:
                notify("It's raining now", "Rain at your place and your clothes are out. Get them in!")
            state["alerted"] = True
            state["warned"] = True
    elif rain_soon:
        state["rain"] = "soon"
        state["alerted"] = False
        if clothes_out and not state["warned"]:
            notify("Rain coming soon", "Rain looks likely in the next 30-45 minutes and your clothes are out.")
            state["warned"] = True
    else:
        state["rain"] = "dry"
        state["alerted"] = False
        state["warned"] = False

    print(
        f"{time.strftime('%H:%M:%S')} {details} raining={raining_now} soon={rain_soon} "
        f"clothes={state['clothes']} alerted={state['alerted']} warned={state['warned']}"
    )


def main():
    old = load_state()
    state = dict(old)
    for i in range(CHECKS):
        check_once(state)
        if i < CHECKS - 1:
            time.sleep(INTERVAL)
    if state != old:
        save_state(state)


if __name__ == "__main__":
    main()
