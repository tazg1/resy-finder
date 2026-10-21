"""One check for a Resy table, run by GitHub Actions every 30 minutes.

Each run, for every restaurant in settings.json:
  1. Looks for open tables on your days and times, and alerts your phone about
     any it hasn't told you about before (remembered in seen.json).
  2. If the next date is released within the next 75 minutes, sends a heads-up
     alert 10 minutes before, then checks every few seconds right after the
     release (earliest release first).

What to look for is set in settings.json. Alerts go to the ntfy topic in the
NTFY_TOPIC secret.

    python cloud_check.py        # a normal check
    python cloud_check.py test   # just send a test alert
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta

import resy_finder as r

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(HERE, "settings.json")
SEEN_FILE = os.path.join(HERE, "seen.json")
LOOKAHEAD = timedelta(minutes=75)  # runs are every 30 min, and GitHub sometimes starts them late
HEADS_UP = timedelta(minutes=10)   # how long before a release to tell you to open Resy


def load_settings():
    with open(SETTINGS_FILE) as f:
        s = json.load(f)
    r.PARTY_SIZE = s["party_size"]
    r.DAYS = s["days"]
    r.EARLIEST = r.parse_clock(s["earliest"])
    r.LATEST = r.parse_clock(s["latest"])
    return s


def find_venue(name):
    hits = r.search_restaurants(name)
    if not hits:
        sys.exit(f"No restaurant on Resy matches '{name}'. Check the name in settings.json.")
    exact = [h for h in hits if h["name"].lower() == name.lower()]
    venue = (exact or hits)[0]
    print(f"Restaurant: {venue['name']} ({venue.get('neighborhood', '')})")
    return venue


def load_seen():
    try:
        with open(SEEN_FILE) as f:
            return set(json.load(f))
    except (OSError, ValueError):
        return set()


def heads_up(venue, plan, at, seen):
    """Alert your phone shortly before a release, so you can be ready to book by hand."""
    key = f"heads-up {venue['name']} {plan['day']}"
    if key in seen:
        return
    wait = (at - HEADS_UP - datetime.now().astimezone()).total_seconds()
    if wait > 0:
        print(f"Heads-up alert at {at - HEADS_UP:%I:%M %p}...")
        time.sleep(wait)
    minutes = round((at - datetime.now().astimezone()).total_seconds() / 60)
    if minutes < 1:
        return  # too late to be useful
    if r.alert(f"{venue['name']}: drop in {minutes} min",
               f"{plan['day']:%a %b %d} opens at {plan['release']:%I:%M %p} ET. "
               f"Open Resy, log in, and book right at the drop.",
               r.booking_link(venue, plan["day"].isoformat())):
        print("Heads-up alert sent.")
        seen.add(key)


def main():
    if not os.environ.get("NTFY_TOPIC"):
        sys.exit("The NTFY_TOPIC secret isn't set. Add it in GitHub: Settings > Secrets and variables > Actions.")

    if sys.argv[1:] == ["test"]:
        ok = r.alert("Resy finder", "Cloud alerts are working. You'll get one here when a table opens up.")
        sys.exit(0 if ok else "Test alert failed.")

    settings = load_settings()
    seen = load_seen()

    try:
        # 1. Anything open right now that we haven't already alerted about?
        releases = []
        for spot in settings["restaurants"]:
            print()
            venue = find_venue(spot["name"])
            tables = r.find_tables(venue)
            new = [t for t in tables if t["link"] + t["when"] not in seen]
            if new:
                r.show_tables(venue, new)
                r.alert_tables(venue, new)
                seen.update(t["link"] + t["when"] for t in new)
            else:
                print(f"Nothing new ({len(tables)} open table(s) already alerted)." if tables else "No tables right now.")

            plan = r.share_tips(venue, spot.get("release_time_et"))
            if plan["release"]:
                releases.append((r.eastern_to_local(plan["opens"], plan["release"]), venue, plan))
            else:
                print(f"Release time unknown for {venue['name']}. Set release_time_et in settings.json.")

        # 2. Is a next date about to be released? Earliest first.
        for at, venue, plan in sorted(releases, key=lambda x: x[0]):
            now = datetime.now().astimezone()
            if at - LOOKAHEAD <= now < at + r.TRY_FOR:
                print(f"\n{venue['name']}:")
                heads_up(venue, plan, at, seen)
                found = r.try_at_release(venue, plan["day"], at)
                seen.update(t["link"] + t["when"] for t in found)
            else:
                print(f"{venue['name']} next release: {plan['day']:%a %b %d} opens {plan['opens']:%a %b %d} at "
                      f"{plan['release']:%I:%M %p} ET. Not soon.")
    finally:
        with open(SEEN_FILE, "w") as f:
            json.dump(sorted(seen), f)


if __name__ == "__main__":
    main()
