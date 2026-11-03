#!/usr/bin/env python3
"""DNT-hytteovervåker.

Sjekker ledige netter for hyttene i cabins.json via DNT sitt
availability-calendar-API og sender e-post når en natt som var booket
blir ledig. Varsler bare om helgenetter (lør–søn, fre–lør) og netten
før en norsk helligdag.

Miljøvariabler:
  SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, EMAIL_FROM, EMAIL_TO
  TEST_EMAIL=true           send kun en test-e-post og avslutt
  NOTIFY_ON_FIRST_RUN=true  varsle også første gang en hytte sjekkes
"""

import json
import os
import smtplib
import sys
import urllib.request
from datetime import date, timedelta
from email.message import EmailMessage
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CABINS_FILE = ROOT / "cabins.json"
STATE_FILE = ROOT / "state.json"

API_URL = "https://hyttebestilling.dnt.no/api/booking/availability-calendar"
CABIN_URL = "https://hyttebestilling.dnt.no/hytte/{id}"
USER_AGENT = "Mozilla/5.0 (compatible; DNT-Monitor/1.0)"
# DNT åpner booking for neste år rundt første arbeidsdag i november, til og med
# nyttårsaften året etter. Fra RELEASE_WATCH_MONTH ser vi derfor også på neste år.
# (API-et takler ca. 15 måneder; to år gir feil.)
RELEASE_WATCH_MONTH = 10
# Åpner flere netter enn dette samtidig for én hytte, regnes det som at et nytt
# bookingår er sluppet, og vi sender en oppsummering i stedet for en lang liste.
RELEASE_THRESHOLD = 30
INCLUDE_FRIDAY = True  # varsle også om fredag–lørdag

NO_MONTHS = [
    "januar", "februar", "mars", "april", "mai", "juni",
    "juli", "august", "september", "oktober", "november", "desember",
]
NO_WEEKDAYS = ["mandag", "tirsdag", "onsdag", "torsdag", "fredag", "lørdag", "søndag"]


def format_no_date(d: date, with_year: bool = True) -> str:
    s = f"{NO_WEEKDAYS[d.weekday()]} {d.day}. {NO_MONTHS[d.month - 1]}"
    return f"{s} {d.year}" if with_year else s


def format_night(d: date) -> str:
    """Ankomst → avreise, f.eks. 'lørdag 5. desember → søndag 6. desember 2026'."""
    dep = d + timedelta(days=1)
    return f"{format_no_date(d, with_year=d.year != dep.year)} → {format_no_date(dep)}"


def easter_sunday(year: int) -> date:
    # Anonym gregoriansk algoritme (Meeus/Jones/Butcher)
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def norwegian_holidays(year: int) -> dict[date, str]:
    easter = easter_sunday(year)
    holidays = {
        date(year, 1, 1): "1. nyttårsdag",
        date(year, 5, 1): "1. mai",
        date(year, 5, 17): "17. mai",
        date(year, 12, 25): "1. juledag",
        date(year, 12, 26): "2. juledag",
    }
    for offset, name in [
        (-3, "Skjærtorsdag"), (-2, "Langfredag"), (0, "1. påskedag"), (1, "2. påskedag"),
        (39, "Kristi himmelfartsdag"), (49, "1. pinsedag"), (50, "2. pinsedag"),
    ]:
        d = easter + timedelta(days=offset)
        holidays[d] = f"{holidays[d]} / {name}" if d in holidays else name  # f.eks. 17. mai 2027
    return holidays


def night_label(d: date) -> str | None:
    """Hvorfor natten med ankomst d er interessant, eller None hvis den ikke er det."""
    dep = d + timedelta(days=1)
    holiday = norwegian_holidays(dep.year).get(dep)
    if holiday:
        return holiday
    if d.weekday() == 5:
        return "helg lør–søn"
    if d.weekday() == 4 and INCLUDE_FRIDAY:
        return "helg fre–lør"
    return None


def window_end(today: date) -> date:
    year = today.year + 1 if today.month >= RELEASE_WATCH_MONTH else today.year
    return date(year, 12, 31)


def next_saturday(d: date) -> date:
    return d + timedelta(days=(5 - d.weekday()) % 7 or 7)


def fetch_available_dates(cabin_id: str, start: date, end: date) -> list[str]:
    """Returnerer sorterte ISO-datoer med ledig plass. Kaster unntak ved feil."""
    url = f"{API_URL}?cabinId={cabin_id}&fromDate={start.isoformat()}&toDate={end.isoformat()}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = json.load(resp)

    data = body.get("data")
    if not data or "availabilityList" not in data:
        raise RuntimeError(f"Uventet svar: {json.dumps(body)[:300]}")

    available = []
    for day in data["availabilityList"]:
        if any((p.get("available") or 0) > 0 for p in day.get("products", [])):
            d = day["date"][:10]
            if start.isoformat() <= d <= end.isoformat():
                available.append(d)
    return sorted(set(available))


def send_email(subject: str, body: str) -> bool:
    host = os.environ.get("SMTP_HOST", "")
    port = int(os.environ.get("SMTP_PORT") or 587)
    user = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_PASS", "")
    sender = os.environ.get("EMAIL_FROM") or user
    recipient = os.environ.get("EMAIL_TO", "")

    if not host or not recipient:
        print("[WARN] SMTP-variabler mangler, hopper over e-post.")
        return False

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = subject
    msg.set_content(body)

    print(f"[EMAIL] Sender til {recipient} ...")
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=30) as s:
                s.login(user, password)
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=30) as s:
                s.starttls()
                s.login(user, password)
                s.send_message(msg)
    except Exception as e:
        print(f"[ERROR] Kunne ikke sende e-post: {e}")
        return False
    print("[EMAIL] Sendt.")
    return True


def format_line(d: str) -> str:
    night = date.fromisoformat(d)
    return f"  - {format_night(night)} ({night_label(night)})"


def build_email(openings: dict[str, tuple[dict, list[str]]], released: set[str] = frozenset()) -> tuple[str, str]:
    names = ", ".join(cabin["name"] for cabin, _ in openings.values())
    if released:
        subject = f"DNT: Booking har åpnet – {names}"
    else:
        total = sum(len(dates) for _, dates in openings.values())
        subject = f"DNT: {total} ny(e) ledig(e) natt/netter – {names}"

    parts = []
    for cid, (cabin, dates) in openings.items():
        link = f"Bestill her: {CABIN_URL.format(id=cabin['id'])}\n"
        if cid in released:
            holidays = [d for d in dates if not night_label(date.fromisoformat(d)).startswith("helg ")]
            weekends = len(dates) - len(holidays)
            text = (
                f"{cabin['name']}: ny bookingperiode har åpnet, med {weekends} ledige helgenetter"
                f" og {len(holidays)} netter før helligdager.\n"
            )
            if holidays:
                text += "Helligdager:\n" + "\n".join(format_line(d) for d in holidays) + "\n"
            parts.append(text + link)
        else:
            lines = "\n".join(format_line(d) for d in dates)
            parts.append(f"{cabin['name']} ({len(dates)} ny(e)):\n{lines}\n{link}")
    body = "\n".join(parts) + "\n--\nSendt automatisk av dnt-cabin-monitor."
    return subject, body


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        print(f"[WARN] Kunne ikke lese {path.name}, starter med tom tilstand.")
        return default


def main() -> int:
    cabins = load_json(CABINS_FILE, [])
    if not cabins:
        print("[ERROR] Ingen hytter i cabins.json.")
        return 1

    if os.environ.get("TEST_EMAIL", "").lower() == "true":
        print("[TEST] Sender test-e-post ...")
        fake = {c["id"]: (c, [next_saturday(date.today()).isoformat()]) for c in cabins}
        subject, body = build_email(fake)
        return 0 if send_email("[TEST] " + subject, body) else 1

    notify_first = os.environ.get("NOTIFY_ON_FIRST_RUN", "").lower() == "true"
    state = load_json(STATE_FILE, {})
    cabin_states = state.get("cabins", {})
    prev_until = state.get("checked_until", "")

    today = date.today()
    end = window_end(today)
    print(f"Sjekker {len(cabins)} hytte(r) fra {today} til {end}")

    openings: dict[str, tuple[dict, list[str]]] = {}
    released: set[str] = set()
    for cabin in cabins:
        cid = str(cabin["id"])
        try:
            available = fetch_available_dates(cid, today, end)
        except Exception as e:
            print(f"  [WARN] {cabin['name']} ({cid}): {e} – beholder forrige tilstand")
            continue

        prev = cabin_states.get(cid)
        print(f"  {cabin['name']} ({cid}): {len(available)} ledige netter")
        if prev is None and not notify_first:
            print("    Første kjøring for denne hytta – lagrer tilstand uten varsel.")
            new = []
        else:
            # Bare datoer som var innenfor forrige vindu teller; datoer som
            # nettopp har rullet inn i slutten av vinduet er ikke "avbestillinger".
            new = sorted(d for d in set(available) - set(prev or []) if d <= prev_until)
        if len(new) > RELEASE_THRESHOLD:
            print(f"    {len(new)} nye netter samtidig – regnes som ny bookingperiode.")
            released.add(cid)
        else:
            for d in new:
                label = night_label(date.fromisoformat(d))
                print(f"    [NY] {d} ({label or 'ikke helg/helligdag, ignoreres'})")
        # state.json lagrer alle ledige netter, men vi varsler bare om helger/helligdager.
        new = [d for d in new if night_label(date.fromisoformat(d))]
        if new:
            openings[cid] = (cabin, new)
        else:
            released.discard(cid)
        cabin_states[cid] = available

    ok = True
    if openings:
        subject, body = build_email(openings, released)
        print(body)
        ok = send_email(subject, body)
        if not ok:
            # Ikke lagre åpningene som "sett", så de varsles ved neste kjøring.
            for cid, (_, new) in openings.items():
                cabin_states[cid] = sorted(set(cabin_states[cid]) - set(new))
    else:
        print("Ingen nye ledige netter.")

    # Ingen tidsstempel her: da endres state.json bare når ledigheten endres (og én gang i døgnet).
    state = {"checked_until": end.isoformat(), "cabins": cabin_states}
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
