"""
Strict rain alert with a "clothes out" switch.

Alerts your phone via ntfy ONLY when:
  - proper rain (not drizzle) is falling at your location, AND
  - you've marked your clothes as "out" on the terrace.

The switch: send "out" or "in" to the control topic <NTFY_TOPIC>-clothes
(the phone shortcuts do this for you). The rain alert also has a
"Clothes collected" button that marks them "in".

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
CONTROL_TOPIC = f"{TOPIC}-clothes"

STATE_FILE = "state.json"

# Strict: ~1 mm in 15 min (about 4 mm/hour) = clearly noticeable rain.
RAIN_NOW_MM = 1.0

# WMO weather codes for real rain / showers / thunderstorms (drizzle excluded)
RAIN_CODES = set(range(61, 68)) | {80, 81, 82, 95, 96, 99}


def get_json(url):
    with urllib.request.urlopen(url, timeout=20) as r:
        return json.load(r)


def get_weather():
    return get_json(
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={LAT}&longitude={LON}"
        "&current=precipitation,rain,showers,weather_code"
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
    state = {"rain": "dry", "alerted": False, "clothes": "in", "clothes_time": 0}
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


def main():
    old = load_state()
    state = dict(old)

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
        print(f"Weather check failed, skipping this run: {e}")
        if state != old:
            save_state(state)
        return

    cur = data.get("current", {})
    amount = max(
        cur.get("precipitation") or 0,
        (cur.get("rain") or 0) + (cur.get("showers") or 0),
    )
    code = cur.get("weather_code")
    raining_now = code in RAIN_CODES and amount >= RAIN_NOW_MM

    # 3) Decide whether to alert (once per rain spell, only if clothes are out)
    if raining_now:
        state["rain"] = "raining"
        if state["clothes"] == "out" and not state["alerted"]:
            notify("It's raining now", "Proper rain at your place and your clothes are out. Get them in!")
            state["alerted"] = True
    else:
        state["rain"] = "dry"
        state["alerted"] = False

    print(
        f"code={code} amount={amount}mm raining={raining_now} "
        f"clothes={state['clothes']} alerted={state['alerted']}"
    )
    if state != old:
        save_state(state)


if __name__ == "__main__":
    main()
