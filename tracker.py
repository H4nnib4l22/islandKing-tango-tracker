import os
import sys
import json
import time
from datetime import datetime, timezone

import requests

# Konfiguration über Umgebungsvariablen (GitHub Secrets)
USERNAME = os.getenv("IK_USER")
PASSWORD = os.getenv("IK_PASS")
BASE_URL = "https://islandking.ch"
TRACKED_FILE = "data/tracked_users.json"
HISTORY_FILE = "data/history.json"
REQUEST_DELAY_SECONDS = 1  # kleine, höfliche Pause zwischen den Abfragen
HISTORY_RETENTION_DAYS = 90  # alte Verlaufs-Einträge werden danach entfernt
HISTORY_MIN_INTERVAL_MS = 15 * 60 * 1000  # neuer Verlaufs-Eintrag höchstens alle 15 Min. pro Name

if not USERNAME or not PASSWORD:
    print("Fehler: Zugangsdaten (IK_USER / IK_PASS) sind nicht gesetzt.")
    sys.exit(1)


def login(session):
    print("Authentifiziere bei Islandking...")
    res = session.post(
        f"{BASE_URL}/api/auth/login",
        json={"username": USERNAME, "password": PASSWORD},
        headers={"Content-Type": "application/json"},
    )
    if res.status_code != 200:
        print(f"Login fehlgeschlagen. Status Code: {res.status_code}")
        print(res.text)
        sys.exit(1)

    token = res.json().get("token")
    if not token:
        print("Fehler: Kein Auth-Token in der Antwort erhalten.")
        sys.exit(1)

    print("Login erfolgreich.")
    return token


def lookup_player(session, headers, name):
    """Sucht einen Spieler über GET /api/rankings?q=<name>.

    Bestätigt per HAR-Aufnahme (islandking_ch_rangliste.har): Antwort ist
    {"players": [{id, name, score, alliance, rank, online, ...}], ...}.

    Nur ein EXAKTER (Groß-/Kleinschreibung ignorierender) Namenstreffer
    zählt als gefunden. Die bereits existierende "Tango Tracker"-Extension
    akzeptiert bei fehlendem Exakt-Treffer notfalls auch den ersten
    Suchtreffer (players[0]) - das übernehmen wir hier bewusst NICHT,
    sonst könnte ein falscher Spieler unter einem fremden Namen in
    tracked_users.json landen.
    """
    res = session.get(
        f"{BASE_URL}/api/rankings",
        params={"q": name},
        headers=headers,
    )

    if res.status_code != 200:
        print(f"  Warnung: HTTP {res.status_code} bei Suche nach '{name}'")
        return {"found": False}

    players = res.json().get("players", [])
    match = next(
        (p for p in players if p.get("name", "").lower() == name.lower()), None
    )

    if not match:
        return {"found": False}

    return {
        "found": True,
        "id": match.get("id"),
        "score": match.get("score"),
        "alliance": match.get("alliance"),
        "rank": match.get("rank"),
        "online": match.get("online"),
    }


def load_history():
    """Lädt data/history.json - dieselbe Struktur, die schon die
    Islandking-Tango-Tracker-Browser-Extension nutzt: {name: [{ts, score,
    online}, ...]}. Existiert die Datei noch nicht, wird mit einem leeren
    Verlauf gestartet."""
    if not os.path.exists(HISTORY_FILE):
        return {}
    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def should_record_history(history, name, ts_ms, online):
    """Ein neuer Verlaufs-Eintrag wird angelegt, wenn ENTWEDER
    HISTORY_MIN_INTERVAL_MS seit dem letzten Eintrag vergangen sind ODER
    sich der Online-Status seit dem letzten Eintrag geändert hat.

    Grund für die Online-Ausnahme: Ohne sie würde auch der Online-Status
    auf die 15-Minuten-Taktung gedrosselt, obwohl die Heatmap gerade von
    möglichst genauen Online/Offline-Übergängen lebt - ein tatsächlicher
    Wechsel soll deshalb immer sofort festgehalten werden, nicht erst
    beim nächsten 15-Minuten-Fenster. Nur das wiederholte Aufzeichnen von
    "ist immer noch online/offline" wird gedrosselt."""
    entries = history.get(name)
    if not entries:
        return True
    last = entries[-1]
    if last.get("online") != online:
        return True
    return (ts_ms - last["ts"]) >= HISTORY_MIN_INTERVAL_MS


def append_history(history, name, ts_ms, score, online):
    """Hängt einen neuen Verlaufs-Eintrag an, statt den letzten Stand zu
    überschreiben - genau das, was fürs spätere Bauen von Heatmap/Punkte-
    Graph gebraucht wird (ein einzelner Zeitstempel reicht dafür nicht)."""
    entries = history.setdefault(name, [])
    entries.append({"ts": ts_ms, "score": score, "online": online})

    cutoff_ms = ts_ms - HISTORY_RETENTION_DAYS * 24 * 60 * 60 * 1000
    history[name] = [e for e in entries if e["ts"] >= cutoff_ms]


def run_tracker():
    session = requests.Session()
    token = login(session)
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    if not os.path.exists(TRACKED_FILE):
        print(f"Fehler: {TRACKED_FILE} nicht gefunden.")
        sys.exit(1)

    with open(TRACKED_FILE, "r", encoding="utf-8") as f:
        tracked = json.load(f)

    history = load_history()
    history_changed = False

    print(f"Prüfe {len(tracked)} Spieler...")
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    for entry in tracked:
        name = entry.get("name")
        if not name:
            continue

        result = lookup_player(session, headers, name)
        entry.update(result)
        entry["lastChecked"] = now_iso

        status = "gefunden" if result["found"] else "NICHT gefunden"
        print(f"- {name}: {status}")

        # Verlaufs-Eintrag nur bei tatsächlichem Treffer UND wenn seit dem
        # letzten Eintrag mind. 15 Min. vergangen sind (siehe
        # should_record_history) - ohne Treffer gibt es ohnehin weder Score
        # noch Online-Status zum Aufzeichnen.
        if result["found"]:
            ts_ms = int(time.time() * 1000)
            if should_record_history(history, name, ts_ms, result["online"]):
                append_history(history, name, ts_ms, result["score"], result["online"])
                history_changed = True
            else:
                print(f"    (Verlauf übersprungen, letzter Eintrag noch keine 15 Min. alt & Status unverändert)")

        time.sleep(REQUEST_DELAY_SECONDS)

    with open(TRACKED_FILE, "w", encoding="utf-8") as f:
        json.dump(tracked, f, indent=2, ensure_ascii=False)

    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False)

    found_count = sum(1 for e in tracked if e.get("found"))
    print(f"Fertig: {found_count}/{len(tracked)} gefunden.")
    print(f"{TRACKED_FILE} und {HISTORY_FILE} aktualisiert.")
    print(f"history.json inhaltlich verändert: {history_changed}")

    # Für die Commit-Nachricht im Workflow: sichtbar machen, ob history.json
    # diesmal wirklich einen neuen Eintrag bekommen hat, oder ob es (wegen
    # der 15-Minuten-Drosselung) nur beim alten Stand blieb - sonst sieht es
    # in der Commit-Historie so aus, als würde history.json "nicht
    # funktionieren", obwohl es einfach nur noch nicht dran war.
    github_output = os.getenv("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as f:
            f.write(f"history_changed={'true' if history_changed else 'false'}\n")


if __name__ == "__main__":
    run_tracker()
