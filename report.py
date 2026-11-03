#!/usr/bin/env python3
"""Rapport over alle ledige helgenetter og netter før helligdager.

Kjøres manuelt (workflowen report.yml). Rører ikke state.json.

Miljøvariabler:
  SELECTED  hytter som skal være med: JSON fra workflowen ({"c101297": true, ...})
            eller kommaseparerte ID-er (101297,10772). Tomt = alle.
  SMTP_*, EMAIL_FROM, EMAIL_TO  som i monitor.py (uten disse sendes ingen e-post)
  GITHUB_STEP_SUMMARY  settes av GitHub Actions; rapporten skrives dit som tabell
"""

import json
import os
import sys
from collections import defaultdict
from datetime import date

import monitor


def selected_cabins(cabins: list[dict]) -> list[dict]:
    raw = os.environ.get("SELECTED", "").strip()
    if raw.startswith("{"):
        ids = {k[1:] for k, v in json.loads(raw).items() if k.startswith("c") and str(v).lower() == "true"}
    else:
        ids = {x.strip() for x in raw.split(",") if x.strip()}
    if not ids:
        return cabins
    unknown = ids - {str(c["id"]) for c in cabins}
    if unknown:
        print(f"[WARN] Ukjente hytte-ID-er (mangler i cabins.json): {', '.join(sorted(unknown))}")
    return [c for c in cabins if str(c["id"]) in ids]


def build_text(nights: dict[str, list[dict]], cabins: list[dict], failed: list[dict]) -> str:
    lines = []
    month = None
    for d in sorted(nights):
        night = date.fromisoformat(d)
        if (night.year, night.month) != month:
            month = (night.year, night.month)
            lines.append(f"\n{monitor.NO_MONTHS[night.month - 1].upper()} {night.year}")
        lines.append(f"{monitor.format_night(night)} ({monitor.night_label(night)})")
        lines.append("    " + ", ".join(c["name"] for c in nights[d]))
    if not nights:
        lines.append("Ingen ledige helgenetter eller netter før helligdager.")
    if failed:
        lines.append("\nKunne ikke hentes: " + ", ".join(c["name"] for c in failed))
    lines.append("\nBestill her:")
    lines += [f"  {c['name']}: {monitor.CABIN_URL.format(id=c['id'])}" for c in cabins]
    return "\n".join(lines).lstrip("\n")


def build_markdown(nights: dict[str, list[dict]], cabins: list[dict], failed: list[dict], end: date) -> str:
    names = ", ".join(c["name"] for c in cabins)
    md = [f"## Ledige helger og helligdager til og med {end.strftime('%d.%m.%Y')}", f"Hytter: {names}", ""]
    if nights:
        md += ["| Natt | Type | Ledige hytter |", "|---|---|---|"]
        for d in sorted(nights):
            night = date.fromisoformat(d)
            links = ", ".join(f"[{c['name']}]({monitor.CABIN_URL.format(id=c['id'])})" for c in nights[d])
            md.append(f"| {monitor.format_night(night)} | {monitor.night_label(night)} | {links} |")
    else:
        md.append("Ingen ledige helgenetter eller netter før helligdager.")
    if failed:
        md += ["", "⚠️ Kunne ikke hentes: " + ", ".join(c["name"] for c in failed)]
    return "\n".join(md) + "\n"


def main() -> int:
    cabins = selected_cabins(monitor.load_json(monitor.CABINS_FILE, []))
    if not cabins:
        print("[ERROR] Ingen hytter valgt.")
        return 1

    today = date.today()
    end = monitor.window_end(today)
    print(f"Henter {len(cabins)} hytte(r) fra {today} til {end}\n")

    nights: dict[str, list[dict]] = defaultdict(list)
    failed = []
    for cabin in cabins:
        try:
            available = monitor.fetch_available_dates(str(cabin["id"]), today, end)
        except Exception as e:
            print(f"[WARN] {cabin['name']} ({cabin['id']}): {e}")
            failed.append(cabin)
            continue
        for d in available:
            if monitor.night_label(date.fromisoformat(d)):
                nights[d].append(cabin)

    text = build_text(nights, cabins, failed)
    print(text + "\n")

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(build_markdown(nights, cabins, failed, end))

    names = ", ".join(c["name"] for c in cabins)
    subject = f"DNT-rapport: {len(nights)} ledige helge-/helligdagsnetter – {names}"
    body = text + "\n\n--\nSendt av dnt-cabin-monitor (manuell rapport)."
    sent = monitor.send_email(subject, body)
    return 0 if sent or not os.environ.get("SMTP_HOST") else 1


if __name__ == "__main__":
    sys.exit(main())
