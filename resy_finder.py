"""Ask for a restaurant, share tips on how to book it, then look on Resy for
a table for 2 on a Thursday, Friday, or Saturday between 6:00 and 7:30 PM.

Tips come from the restaurant's own Resy listing, its booking calendar, and
Resy's "How to Get Into..." guide for it when one exists. If nothing is open,
it offers to try again right when the next date is released, and alerts your
phone if it finds a table.

Run once:      py resy_finder.py
Keep checking: py resy_finder.py --watch --interval 5

Phone alerts are push notifications from the free ntfy app (ntfy.sh). The first
run walks you through it, sends a test alert, and saves your topic name in
resy_config.json. If you buy a Textbelt key (textbelt.com) and set TEXTBELT_KEY,
you also get a real text at the phone number saved in resy_config.json.

cloud_check.py runs these same checks on GitHub Actions, so your computer can be off.
"""

import argparse
import html
import json
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, time as clock, timedelta, timezone

# ---- Settings (cloud_check.py overrides these from settings.json) ----
PARTY_SIZE = 2
DAYS = ["Thursday", "Friday", "Saturday"]
EARLIEST = clock(18, 0)
LATEST = clock(19, 30)
SEARCH_NEAR = {"latitude": 40.7128, "longitude": -74.0060, "radius": 35420}  # New York City

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resy_config.json")

# When trying at release time: start a minute early, check every 3 seconds for the
# first 3 minutes after release (tables can go in seconds), then every 10 seconds
TRY_EARLY = timedelta(minutes=1)
TRY_FOR = timedelta(minutes=10)
FAST_FOR = timedelta(minutes=3)
FAST_EVERY_SECONDS = 3
TRY_EVERY_SECONDS = 10

# Public key the resy.com website itself uses
API_KEY = "VbWk7s3L4KiK5fzlO7JD3Q5EYolJI7n5"
API = "https://api.resy.com"
GUIDES = "https://blog.resy.com/wp-json/wp/v2"
BROWSER = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36"}

# A sentence is a booking tip if it mentions booking AND a time, timing, or tactic
BOOKING_WORDS = re.compile(r"reserv|table|book|drop|release|walk-in|notify|wait ?list|seat", re.I)
TIMING_WORDS = re.compile(
    r"\d+\s*days?|\bweeks?\b|\bmonths?\b|\d{1,2}(:\d\d)?\s*(a\.?m\.?|p\.?m\.?)|midnight|noon|"
    r"in advance|ahead|notify|walk-in|bar|line up|log on|minutes? before|seconds", re.I)
CLOCK_TIME = re.compile(r"\b\d{1,2}(?::\d\d)?\s*(?:a\.?m\.?|p\.?m\.?)", re.I)
RELEASE_WORDS = re.compile(r"drop|release|open|starting|advance|go live|log on", re.I)
DAYS_AHEAD = re.compile(r"(\d+)\s*days?\s*(?:in advance|ahead)", re.I)

headers = {
    "Authorization": f'ResyAPI api_key="{API_KEY}"',
    "User-Agent": "Mozilla/5.0",
    "Origin": "https://resy.com",
    "Referer": "https://resy.com/",
}


def call(path, params=None, form=None, body=None):
    """GET a Resy API path, or POST when form or a JSON body is given, and return the JSON."""
    url = API + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req_headers = dict(headers)
    data = None
    if form:
        data = urllib.parse.urlencode(form).encode()
    elif body:
        data = json.dumps(body).encode()
        req_headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=req_headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def login(email, password):
    try:
        user = call("/3/auth/password", form={"email": email, "password": password})
    except urllib.error.HTTPError as e:
        sys.exit(f"Login failed: {e.code} {e.reason}")

    print(f"Logged in as {user['first_name']} {user['last_name']}")
    headers["X-Resy-Auth-Token"] = user["token"]


def ask_yes(question):
    return input(f"{question} (y/n): ").strip().lower().startswith("y")


# ---- Phone alerts ----

def load_config():
    try:
        with open(CONFIG_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_config(config):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)


def send_text(phone, message):
    """Text a US phone number through Textbelt (needs a paid TEXTBELT_KEY). Returns True if sent."""
    form = urllib.parse.urlencode(
        {"phone": phone, "message": message, "key": os.environ["TEXTBELT_KEY"]}).encode()
    try:
        with urllib.request.urlopen("https://textbelt.com/text", data=form, timeout=30) as resp:
            result = json.load(resp)
    except urllib.error.URLError as e:
        print(f"Couldn't send text: {e}")
        return False
    if not result.get("success"):
        print(f"Couldn't send text: {result.get('error')}")
        return False
    return True


def alert(title, message, link=None):
    """Push a notification to your phone with ntfy (plus a text if TEXTBELT_KEY is set)."""
    config = load_config()
    sent = False

    topic = os.environ.get("NTFY_TOPIC") or config.get("topic")
    if topic:
        note = {"topic": topic, "title": title, "message": message,
                "priority": 5, "tags": ["fork_and_knife"]}  # priority 5 = loud, pops up on screen
        if link:
            note["click"] = link  # tapping the alert opens this page
        req = urllib.request.Request("https://ntfy.sh/", data=json.dumps(note).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=30).close()
            sent = True
        except urllib.error.URLError as e:
            print(f"Couldn't send phone alert: {e}")

    phone = os.environ.get("ALERT_PHONE") or config.get("phone")
    if os.environ.get("TEXTBELT_KEY") and phone:
        # No link in the text: Textbelt blocks links unless your key is approved for them
        sent = send_text(phone, f"{title}: {message}") or sent
    return sent


def set_up_alerts():
    """First run: connect the ntfy app on your phone and send a test alert."""
    config = load_config()
    if os.environ.get("NTFY_TOPIC") or (config.get("topic") and config.get("tested")):
        return

    print("\n--- Phone alert sign-up ---")
    print("1. On your phone, install the free 'ntfy' app from the Google Play Store.")
    print("2. Open it, tap +, and subscribe to the topic name below (keep 'Use another server' off).")
    print("   Anyone who knows the topic name can see your alerts, so keep it hard to guess.")
    suggested = config.get("topic") or "resy-" + secrets.token_hex(5)
    config["topic"] = input(f"Topic name (Enter for {suggested}): ").strip() or suggested
    input("Press Enter once you've subscribed in the app...")

    print("Sending a test alert...")
    config["tested"] = (alert("Resy finder", "Alerts are on. You'll get one here when a table opens up.")
                        and ask_yes("Did the test alert show up on your phone?"))
    if not config["tested"]:
        print("Check the topic name in the app matches exactly. It will try again next run.")
    save_config(config)
    print()


# ---- Time (Resy release times are New York time) ----

def eastern_offset(day):
    # US daylight saving: second Sunday in March to first Sunday in November
    march_8, nov_1 = date(day.year, 3, 8), date(day.year, 11, 1)
    dst_start = march_8 + timedelta(days=(6 - march_8.weekday()) % 7)
    dst_end = nov_1 + timedelta(days=(6 - nov_1.weekday()) % 7)
    return timedelta(hours=-4 if dst_start <= day < dst_end else -5)


def now_eastern():
    """The current date and time in New York."""
    utc = datetime.now(timezone.utc)
    return (utc + eastern_offset((utc - timedelta(hours=5)).date())).replace(tzinfo=None)


def eastern_to_local(day, at):
    """Turn a date + New York clock time into this computer's local time."""
    return datetime.combine(day, at, tzinfo=timezone(eastern_offset(day))).astimezone()


def parse_clock(text):
    """'11 a.m.' or '10:00 AM' -> time(11, 0). None if it can't be read."""
    m = re.match(r"\s*(\d{1,2})(?::(\d\d))?\s*([ap])", text or "", re.I)
    if not m or not 1 <= int(m.group(1)) <= 12:
        return None
    hour = int(m.group(1)) % 12 + (12 if m.group(3).lower() == "p" else 0)
    return clock(hour, int(m.group(2) or 0))


def keep_awake(on):
    """Stop Windows from sleeping while we wait for a release time."""
    if sys.platform == "win32":
        import ctypes
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0))


# ---- Restaurant search and tips ----

def search_restaurants(name):
    res = call("/3/venuesearch/search", body={
        "query": name, "per_page": 8, "types": ["venue"], "geo": SEARCH_NEAR,
    })
    return res["search"]["hits"]


def pick_restaurant():
    """Ask for a restaurant name and return the matching Resy venue."""
    while True:
        name = input("Restaurant name: ").strip()
        if not name:
            continue

        hits = search_restaurants(name)
        if not hits:
            print(f"No restaurants on Resy match '{name}'. Try another name.\n")
            continue
        if len(hits) == 1:
            return hits[0]

        print("\nMatches:")
        for i, h in enumerate(hits, 1):
            print(f"  {i}. {h['name']} ({h.get('neighborhood') or h['location']['name']})")
        choice = input("Pick a number (Enter for 1, or 0 to search again): ").strip() or "1"
        if choice.isdigit() and 1 <= int(choice) <= len(hits):
            return hits[int(choice) - 1]
        print()


def get_page(url):
    req = urllib.request.Request(url, headers=BROWSER)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", "replace")


def plain_text(markup):
    markup = re.sub(r"(?is)<(script|style|noscript|nav|footer|header|aside|form)\b.*?</\1>", " ", markup)
    markup = re.sub(r"(?i)</(p|h[1-6]|li|div|td|blockquote|figcaption)>|<br\s*/?>", "\n", markup)
    text = html.unescape(re.sub(r"<[^>]+>", " ", markup))
    return re.sub(r"[ \t\r\f\v]+", " ", text).strip()


def booking_sentences(text):
    """Pick out sentences that are booking tips, most specific (times, days ahead) first."""
    tips, seen = [], set()
    for s in re.split(r"(?<=[.!?])\s+|\s*\n\s*", text):
        s = s.strip()
        key = s.lower()[:40]
        if (30 < len(s) < 350 and not s.endswith("?") and key not in seen
                and BOOKING_WORDS.search(s) and TIMING_WORDS.search(s)):
            seen.add(key)
            tips.append(s)
    return sorted(tips, key=lambda s: not (CLOCK_TIME.search(s) or DAYS_AHEAD.search(s)))


def find_guide(name):
    """Return (title, year, url, booking sentences) from Resy's 'How to Get Into' guide, or None."""
    try:
        results = json.loads(get_page(f"{GUIDES}/search?" + urllib.parse.urlencode(
            {"search": f"how to get into {name}", "per_page": 10})))
        for r in results:
            title = html.unescape(r["title"])
            if "how to get into" in title.lower() and name.lower() in title.lower():
                year = re.search(r"/(20\d\d)/", r["url"])
                tips = booking_sentences(plain_text(get_page(r["url"])))
                return title, year.group(1) if year else "", r["url"], tips
    except (urllib.error.URLError, KeyError, ValueError):
        pass  # the guide is a bonus; carry on without it
    return None


def share_tips(venue, release_override=None):
    """Print tips on how and when to book this restaurant, and return when to try next."""
    name = venue["name"]
    print(f"\n===== Tips for booking {name} =====")
    sources = []

    # 1. The restaurant's own "Need to know" notes on Resy
    details = call("/3/venue", {"id": venue["id"]["resy"]})
    need_to_know = " ".join(c.get("body") or "" for c in details.get("content", [])
                            if c.get("name") == "need_to_know")
    policy = booking_sentences(need_to_know)
    if policy:
        print("\nFrom the restaurant's Resy page:")
        for s in policy[:5]:
            print(f"  - {s}")
        sources.append(f"https://resy.com/cities/{venue['location']['url_slug']}/venues/{venue['url_slug']}")

    # 2. Resy's "How to Get Into..." guide, if there is one
    guide_tips = []
    guide = find_guide(name)
    if guide and guide[3]:
        title, year, url, guide_tips = guide
        print(f"\nFrom Resy's guide \"{title}\" ({year}, so details may have changed):")
        for s in guide_tips[:6]:
            print(f"  - {s}")
        sources.append(url)

    # 3. The rule as written ("20 days in advance at 11 a.m."), restaurant's page first
    release_text = stated_days = None
    for s in policy + guide_tips:
        if not release_text and RELEASE_WORDS.search(s) and CLOCK_TIME.search(s):
            release_text = CLOCK_TIME.search(s).group(0)
        if not stated_days and DAYS_AHEAD.search(s):
            stated_days = int(DAYS_AHEAD.search(s).group(1))
    release = parse_clock(release_override) or parse_clock(release_text)

    # 4. Check that against how far ahead the live calendar is open
    now = now_eastern()
    today = now.date()
    cal = call("/4/venue/calendar", {
        "venue_id": venue["id"]["resy"], "num_seats": PARTY_SIZE,
        "start_date": today.isoformat(), "end_date": (today + timedelta(days=365)).isoformat(),
    })
    last_day = date.fromisoformat(cal["last_calendar_day"])
    calendar_days = (last_day - today).days

    if stated_days and abs(stated_days - calendar_days) <= 2:
        window = stated_days
    elif release and now.time() < release:
        window = calendar_days + 1  # today's release hasn't happened yet, so the calendar is a day short
    else:
        window = calendar_days

    def already_released(day):
        opens = day - timedelta(days=window)
        return opens < today or (opens == today and release is not None and now.time() >= release)

    next_day = last_day + timedelta(days=1)
    while next_day.strftime("%A") not in DAYS or already_released(next_day):
        next_day += timedelta(days=1)
    opens = next_day - timedelta(days=window)

    print("\nWhen to try:")
    at = f" at {release:%I:%M %p} ET" if release else ""
    print(f"  - Tables open {window} days ahead{at}. Resy's calendar is open through {last_day:%a %b %d}.")
    if stated_days and abs(stated_days - calendar_days) > 2:
        print(f"  - Note: the tips above say {stated_days} days, but the live calendar is open "
              f"{calendar_days} days ahead. Going by the calendar; check the Resy page.")
    when = {today: "TODAY, ", today + timedelta(days=1): "tomorrow, "}.get(opens, "")
    print(f"  - Next {'/'.join(d[:3] for d in DAYS)} date to open: {next_day:%a %b %d}. "
          f"Expect it {when}{opens:%a %b %d}{at or ' (release time not listed)'}.")
    print("  - Be logged in with a card saved a few minutes early. Popular spots can sell out in seconds.")
    print("  - Tap \"Notify\" on the dates you want. Resy alerts you when someone cancels.")
    print("  - Check again 24-48 hours before. Cancellations spike when deposit deadlines hit.")

    if sources:
        print("\nSources:")
        for url in sources:
            print(f"  {url}")
    print()

    return {"day": next_day, "opens": opens, "release": release}


# ---- Finding tables ----

def slots_on(venue, day):
    """Every open table on one date (YYYY-MM-DD), at any time of day."""
    res = call("/4/find", {
        "lat": 0, "long": 0, "day": day,
        "party_size": PARTY_SIZE, "venue_id": venue["id"]["resy"],
    })
    venues = res["results"]["venues"]
    return [{
        "start": datetime.strptime(slot["date"]["start"], "%Y-%m-%d %H:%M:%S"),
        "table": slot["config"]["type"],
        "link": f"https://resy.com/cities/{venue['location']['url_slug']}"
                f"/venues/{venue['url_slug']}?date={day}&seats={PARTY_SIZE}",
    } for slot in (venues[0]["slots"] if venues else [])]


def in_window(slots):
    """Just the tables between EARLIEST and LATEST."""
    return [{"when": s["start"].strftime("%a %b %d  %I:%M %p"), "table": s["table"], "link": s["link"]}
            for s in slots if EARLIEST <= s["start"].time() <= LATEST]


def tables_on(venue, day):
    """Open tables in the time window on one date (YYYY-MM-DD)."""
    return in_window(slots_on(venue, day))


def find_tables(venue):
    today = now_eastern().date()

    # The calendar says which days are bookable at all (most places open a few weeks out)
    cal = call("/4/venue/calendar", {
        "venue_id": venue["id"]["resy"],
        "num_seats": PARTY_SIZE,
        "start_date": today.isoformat(),
        "end_date": (today + timedelta(days=90)).isoformat(),
    })

    # Only look closer at dates with something available, to go easy on Resy (it rate-limits)
    your_days = [d for d in cal["scheduled"] if date.fromisoformat(d["date"]).strftime("%A") in DAYS]
    candidate_days = [d["date"] for d in your_days if d["inventory"]["reservation"] == "available"]
    print(f"{len(candidate_days)} of {len(your_days)} {'/'.join(d[:3] for d in DAYS)} dates "
          f"through {cal['last_calendar_day']} have openings. Checking times...")

    found = []
    for day in candidate_days:
        found += tables_on(venue, day)
    return found


def show_tables(venue, tables):
    print(f"\nFound {len(tables)} table(s) at {venue['name']}:")
    for t in tables:
        print(f"  {t['when']}  {t['table']:<15} {t['link']}")
    print("\a")  # beep


def alert_tables(venue, tables):
    more = f" (+{len(tables) - 1} more)" if len(tables) > 1 else ""
    first = " ".join(tables[0]["when"].split())
    if alert(f"Table open at {venue['name']}!",
             f"Table for {PARTY_SIZE}: {first}{more}. Tap to book on Resy, fast.", tables[0]["link"]):
        print("Phone alert sent.")


def try_at_release(venue, day, at):
    """Wait for the release time, then check that date every few seconds. Returns tables found."""
    start, stop = at - TRY_EARLY, at + TRY_FOR
    print(f"\nWaiting until {at:%a %b %d %I:%M %p} to check {day:%a %b %d}.")
    print("Leave this running (computer on, plugged in, lid open). Ctrl+C to cancel.")
    keep_awake(True)
    try:
        while datetime.now().astimezone() < start:
            remaining = (start - datetime.now().astimezone()).total_seconds()
            time.sleep(min(max(remaining, 0), 60))  # short naps, so the clock is rechecked often

        print(f"Trying now: every {FAST_EVERY_SECONDS}s until {at + FAST_FOR:%I:%M %p}, "
              f"then every {TRY_EVERY_SECONDS}s until {stop:%I:%M %p}...")
        while (now := datetime.now().astimezone()) < stop:
            every = FAST_EVERY_SECONDS if now < at + FAST_FOR else TRY_EVERY_SECONDS
            try:
                slots = slots_on(venue, day.isoformat())
            except urllib.error.HTTPError as e:
                wait = 10 if e.code == 429 else every  # 429 = Resy says slow down
                print(f"  {now:%H:%M:%S}  check failed: {e}; retrying in {wait}s")
                time.sleep(wait)
                continue
            except urllib.error.URLError as e:
                print(f"  {now:%H:%M:%S}  check failed: {e}; retrying")
                slots = []
            tables = in_window(slots)
            print(f"  {now:%H:%M:%S}  {len(slots)} open at any time, {len(tables)} in your window")
            if tables:
                show_tables(venue, tables)
                alert_tables(venue, tables)
                return tables
            time.sleep(every)

        print(f"No tables showed up for {day:%a %b %d}. They may have gone in seconds. "
              f"Try Notify on Resy, or run with --watch to catch cancellations.")
        return []
    finally:
        keep_awake(False)


def main():
    parser = argparse.ArgumentParser(description="Find a table on Resy.")
    parser.add_argument("--watch", action="store_true", help="keep checking until a table opens up")
    parser.add_argument("--interval", type=int, default=5, help="minutes between checks (default 5)")
    args = parser.parse_args()

    email, password = os.environ.get("RESY_EMAIL"), os.environ.get("RESY_PASSWORD")
    if email and password:
        login(email, password)
    else:
        print("Searching as a guest (no Resy login needed to find tables).")

    set_up_alerts()
    venue = pick_restaurant()
    plan = None
    try:
        plan = share_tips(venue)
    except (urllib.error.URLError, KeyError, ValueError) as e:
        print(f"\n(Couldn't load booking tips: {e})\n")

    print(f"Looking for a table at {venue['name']}...")
    tables = find_tables(venue)
    if tables:
        show_tables(venue, tables)  # you're watching the screen, so no alert needed
        return
    print(f"No tables for {PARTY_SIZE} in your days and times right now.")

    # Offer to try again the moment the next date is released
    if plan:
        release = plan["release"]
        when = f"{plan['opens']:%a %b %d}" + (f" at {release:%I:%M %p} ET" if release else "")
        if ask_yes(f"\nWant me to try again when {plan['day']:%a %b %d} opens ({when}) and alert your phone?"):
            while not release:
                release = parse_clock(input("What time (New York time) should I try? (Enter for 10:00 AM): ")
                                      or "10:00 AM")
            try_at_release(venue, plan["day"], eastern_to_local(plan["opens"], release))
            return

    while args.watch:
        print(f"Checking again in {args.interval} minute(s). Press Ctrl+C to stop.\n")
        time.sleep(args.interval * 60)
        tables = find_tables(venue)
        if tables:
            show_tables(venue, tables)
            alert_tables(venue, tables)
            return
        print(f"No tables for {PARTY_SIZE} in your days and times.")


if __name__ == "__main__":
    main()
