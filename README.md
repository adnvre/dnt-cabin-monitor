# DNT-hytteovervåker

Sjekker DNT-hyttene i `cabins.json` hvert 15. minutt og sender e-post når en natt som var booket blir ledig (f.eks. ved avbestilling).

Inspirert av [deagjerde/dnt-cabin-monitor](https://github.com/deagjerde/dnt-cabin-monitor), men skrevet i Python og bruker DNT sitt kalender-API, så det blir ett kall per hytte i stedet for ett per natt.

## Slik virker det

1. GitHub Actions kjører `monitor.py` hvert 15. minutt.
2. For hver hytte hentes ledighet ~13 måneder frem fra `https://hyttebestilling.dnt.no/api/booking/availability-calendar`.
3. Resultatet sammenlignes med `state.json`. Nye ledige netter gir én samlet e-post.
4. `state.json` committes tilbake bare når ledigheten har endret seg.

## Hvilke netter varsles

Bare netter du faktisk vil ha:

- **lørdag → søndag**
- **fredag → lørdag** (slå av med `INCLUDE_FRIDAY = False` i `monitor.py`)
- **natten før en norsk helligdag**: avreise dagen før, hjemreise på helligdagen. Gjelder nyttårsdag, skjærtorsdag, langfredag, 1. og 2. påskedag, 1. mai, 17. mai, Kristi himmelfartsdag, 1. og 2. pinsedag, og 1. og 2. juledag. Datoene beregnes automatisk hvert år.

`state.json` lagrer fortsatt alle ledige netter. Filteret brukes bare når varselet lages.

Første gang en hytte sjekkes lagres bare tilstanden, uten varsel. Sett `NOTIFY_ON_FIRST_RUN=true` hvis du vil få varsel om alt som allerede er ledig.

## Legge til hytter

Finn ID-en i URL-en på hyttesiden, f.eks. `https://hyttebestilling.dnt.no/hytte/101209` → `101209`, og legg den inn i `cabins.json`:

```json
[
  {"id": "101209", "name": "Fuglemyrhytta"},
  {"id": "123456", "name": "En annen hytte"}
]
```

## Oppsett

1. Repoet bør være **offentlig**. Da er GitHub Actions-minutter gratis. Ingen hemmeligheter ligger i koden.
2. Lag et Gmail-app-passord (krever totrinnsbekreftelse): <https://myaccount.google.com/apppasswords>
3. Legg til secrets under **Settings → Secrets and variables → Actions**:

| Secret | Verdi |
|---|---|
| `SMTP_HOST` | `smtp.gmail.com` |
| `SMTP_PORT` | `587` |
| `SMTP_USER` | din Gmail-adresse |
| `SMTP_PASS` | app-passordet |
| `EMAIL_FROM` | din Gmail-adresse |
| `EMAIL_TO` | adressen varslene skal til |

Eller fra terminalen: `gh secret set SMTP_PASS` (og tilsvarende for de andre).

4. Test: **Actions → DNT Cabin Monitor → Run workflow** med «Send kun en test-e-post» avkrysset.

## Kjøre lokalt

```bash
python3 monitor.py                     # sjekk uten e-post (hvis SMTP-variabler ikke er satt)
TEST_EMAIL=true SMTP_HOST=smtp.gmail.com SMTP_PORT=587 \
  SMTP_USER=deg@gmail.com SMTP_PASS=... EMAIL_TO=deg@gmail.com python3 monitor.py
```

På macOS med Python fra python.org kan du få `CERTIFICATE_VERIFY_FAILED`. Kjør da `export SSL_CERT_FILE=/etc/ssl/cert.pem` først, eller «Install Certificates.command».

## Merk

- GitHub kan forsinke planlagte kjøringer, og deaktiverer dem i repoer uten aktivitet på 60 dager. Commits av `state.json` teller ikke alltid, så sjekk Actions-fanen av og til.
- API-et er ikke offisielt dokumentert og kan endres uten varsel.
