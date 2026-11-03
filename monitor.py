#!/usr/bin/env python3
"""DNT-hytteovervåker.

Sjekker ledige netter for hyttene i cabins.json via DNT sitt
availability-calendar-API og sender e-post når en natt som var booket
blir ledig.

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
DAYS_AHEAD = 400  # ca. 13 måneder

NO_MONTHS = [
    "januar", "februar", "mars", "april", "mai", "juni",
    "juli", "august", "september", "oktober", "november", "desember",
]
NO_WEEKDAYS = ["mandag", "tirsdag", "onsdag", "torsdag", "fredag", "lørdag", "søndag"]


def format_no_date(d: date) -> str:
    return f"{NO_WEEKDAYS[d.weekday()]} {d.day}. {NO_MONTHS[d.month - 1]} {d.year}"


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


def build_email(openings: dict[str, tuple[dict, list[str]]]) -> tuple[str, str]:
    total = sum(len(dates) for _, dates in openings.values())
    names = ", ".join(cabin["name"] for cabin, _ in openings.values())
    subject = f"DNT: {total} ny(e) ledig(e) natt/netter – {names}"

    parts = []
    for cabin, dates in openings.values():
        lines = "\n".join(f"  - {format_no_date(date.fromisoformat(d))}" for d in dates)
        parts.append(
            f"{cabin['name']} ({len(dates)} ny(e)):\n{lines}\n"
            f"Bestill her: {CABIN_URL.format(id=cabin['id'])}\n"
        )
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
        fake = {c["id"]: (c, [(date.today() + timedelta(days=7)).isoformat()]) for c in cabins}
        subject, body = build_email(fake)
        return 0 if send_email("[TEST] " + subject, body) else 1

    notify_first = os.environ.get("NOTIFY_ON_FIRST_RUN", "").lower() == "true"
    state = load_json(STATE_FILE, {})
    cabin_states = state.get("cabins", {})

    today = date.today()
    end = today + timedelta(days=DAYS_AHEAD)
    print(f"Sjekker {len(cabins)} hytte(r) fra {today} til {end}")

    openings: dict[str, tuple[dict, list[str]]] = {}
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
            new = sorted(set(available) - set(prev or []))
        for d in new:
            print(f"    [NY] {d}")
        if new:
            openings[cid] = (cabin, new)
        cabin_states[cid] = available

    # Ingen tidsstempel her: da endres state.json (og committes) bare når ledigheten endres.
    state = {"cabins": cabin_states}
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if openings:
        subject, body = build_email(openings)
        print(body)
        send_email(subject, body)
    else:
        print("Ingen nye ledige netter.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
