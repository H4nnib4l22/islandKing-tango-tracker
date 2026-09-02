import os
import sys
import json
import time
import random
import base64
from datetime import datetime, timezone

import requests

# Konfiguration über Umgebungsvariablen (GitHub Secrets bzw. von GitHub
# Actions automatisch bereitgestellt)
USERNAME = os.getenv("IK_USER")
PASSWORD = os.getenv("IK_PASS")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")  # automatisch von Actions, muss im Workflow durchgereicht werden
GITHUB_REPOSITORY = os.getenv("GITHUB_REPOSITORY")  # "owner/repo", von Actions automatisch gesetzt

BASE_URL = "https://islandking.ch"
TRACKED_FILE = "data/tracked_users.json"  # exklusiv fürs Go-Core, normaler Git-Commit
HISTORY_PATH = "data/history.json"  # GETEILT mit den Browser-Extensions, läuft über die GitHub-API
REQUEST_DELAY_SECONDS = 1  # kleine, höfliche Pause zwischen den Islandking-Abfragen

# Müssen mit dem übereinstimmen, was die Browser-Extension für denselben,
# jetzt gemeinsam genutzten Ort verwendet (Absprache siehe Chat).
HISTORY_RETENTION_DAYS = 30
HISTORY_MAX_PER_NAME = 5000
SCORE_CHECKPOINT_INTERVAL_MS = 20 * 60 * 1000  # Punkte nur alle ~20 Min. neu festhalten
MAX_MERGE_RETRIES = 3

if not USERNAME or not PASSWORD:
    print("Fehler: Zugangsdaten (IK_USER / IK_PASS) sind nicht gesetzt.")
    sys.exit(1)

if not GITHUB_TOKEN or not GITHUB_REPOSITORY:
    print("Fehler: GITHUB_TOKEN / GITHUB_REPOSITORY sind nicht gesetzt (werden für den history.json-Merge über die API gebraucht).")
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


def last_score_ts(history, name):
    """Zeitstempel des letzten Eintrags mit echtem (nicht-null) Score für
    diesen Namen, oder None, falls noch keiner existiert."""
    for e in reversed(history.get(name, [])):
        if isinstance(e.get("score"), (int, float)):
            return e["ts"]
    return None


# ---------------------------------------------------------------------
# history.json über die GitHub-Contents-API - GETEILT mit den
# Browser-Extensions (mehrere unabhängige Schreiber gleichzeitig möglich).
#
# Anders als tracked_users.json NICHT mehr über das lokale, ausgecheckte
# Dateisystem + git commit lesen/schreiben: history.json wurde bisher als
# eine einzige JSON-Zeile ohne Einrückung geschrieben, dadurch hätte selbst
# ein reiner Git-Rebase bei zwei gleichzeitigen Änderungen an dieser Datei
# praktisch immer einen Konflikt gemeldet, egal wie inhaltlich sinnvoll die
# Änderungen eigentlich gewesen wären (git-Merges sind zeilenbasiert). Statt
# dessen: GET mit sha -> inhaltlich mergen -> PUT mit sha, bei 409 (Konflikt,
# jemand war schneller) neu GET+merge+PUT - exakt dasselbe Muster, das die
# Extension für denselben Ort verwendet.
# ---------------------------------------------------------------------


def github_api_headers():
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def fetch_remote_history():
    """Holt die aktuelle history.json direkt über die GitHub-Contents-API.
    Gibt (history_dict, sha) zurück. sha ist None, falls die Datei noch
    nicht existiert (dann wird beim ersten PUT keins mitgeschickt)."""
    url = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/contents/{HISTORY_PATH}"
    res = requests.get(url, headers=github_api_headers())
    if res.status_code == 404:
        return {}, None
    res.raise_for_status()
    data = res.json()
    content = base64.b64decode(data["content"]).decode("utf-8")
    return json.loads(content), data["sha"]


def merge_and_trim(remote_history, pending_entries):
    """pending_entries: {name: [entry, ...]} - nur die in diesem Lauf neu
    gesammelten Einträge. Merged sie in remote_history (Duplikate mit
    identischem name+ts werden verworfen), wendet danach Retention (30
    Tage) und Cap (max. 5000 Einträge pro Name) an."""
    merged = {name: list(entries) for name, entries in remote_history.items()}
    now_ms = int(time.time() * 1000)
    cutoff_ms = now_ms - HISTORY_RETENTION_DAYS * 24 * 60 * 60 * 1000

    touched_names = set(pending_entries.keys()) | set(merged.keys())

    for name in touched_names:
        existing = merged.get(name, [])
        existing_ts = {e["ts"] for e in existing}

        for entry in pending_entries.get(name, []):
            if entry["ts"] not in existing_ts:
                existing.append(entry)
                existing_ts.add(entry["ts"])

        existing.sort(key=lambda e: e["ts"])
        existing = [e for e in existing if e["ts"] >= cutoff_ms]
        if len(existing) > HISTORY_MAX_PER_NAME:
            existing = existing[-HISTORY_MAX_PER_NAME:]

        merged[name] = existing

    return merged


def push_history(merged_history, sha):
    url = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/contents/{HISTORY_PATH}"
    content_str = json.dumps(merged_history, ensure_ascii=False)
    payload = {
        "message": "Merge history.json (tracker.py)",
        "content": base64.b64encode(content_str.encode("utf-8")).decode("ascii"),
    }
    if sha:
        payload["sha"] = sha
    return requests.put(url, headers=github_api_headers(), json=payload)


def sync_history(pending_entries):
    """GET aktuelle history.json, mergen, PUT mit sha - bei 409 neu
    GET+merge+PUT, bis zu MAX_MERGE_RETRIES mal mit kurzem Jitter-Delay."""
    if not pending_entries:
        print("Keine neuen Verlaufs-Einträge in diesem Lauf, history.json wird nicht angefasst.")
        return None

    for attempt in range(1, MAX_MERGE_RETRIES + 1):
        remote_history, sha = fetch_remote_history()
        merged = merge_and_trim(remote_history, pending_entries)
        res = push_history(merged, sha)

        if res.status_code in (200, 201):
            print(f"history.json gemerged & gepusht (Versuch {attempt}/{MAX_MERGE_RETRIES}).")
            return merged

        if res.status_code == 409:
            print(f"Konflikt beim Schreiben von history.json (Versuch {attempt}/{MAX_MERGE_RETRIES}), erneuter Versuch...")
            time.sleep(1 + random.random() * 2)
            continue

        print(f"Warnung: Unerwarteter Status {res.status_code} beim Schreiben von history.json: {res.text}")
        return None

    print("Fehler: history.json konnte nach mehreren Versuchen nicht gemerged werden (dauerhafter Konflikt).")
    return None


def run_tracker():
    session = requests.Session()
    token = login(session)
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    if not os.path.exists(TRACKED_FILE):
        print(f"Fehler: {TRACKED_FILE} nicht gefunden.")
        sys.exit(1)

    with open(TRACKED_FILE, "r", encoding="utf-8") as f:
        tracked = json.load(f)

    # Einmaliger Snapshot zu Beginn nur für die Score-Checkpoint-Entscheidung
    # (ist ein anderer Zweck als der Merge am Ende, der nochmal frisch holt -
    # kleine Ungenauigkeit hier ist unkritisch, siehe Chat).
    initial_history, _ = fetch_remote_history()
    pending_entries = {}  # {name: [entry, ...]} - nur was DIESER Lauf neu produziert

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

        # Verlaufs-Eintrag nur bei tatsächlichem Treffer - ohne Treffer gibt
        # es weder Score noch Online-Status zum Aufzeichnen.
        if result["found"]:
            ts_ms = int(time.time() * 1000)
            last_ts = last_score_ts(initial_history, name)
            include_score = last_ts is None or (ts_ms - last_ts) >= SCORE_CHECKPOINT_INTERVAL_MS

            new_entry = {"ts": ts_ms, "score": result["score"] if include_score else None, "online": result["online"]}
            pending_entries.setdefault(name, []).append(new_entry)

        time.sleep(REQUEST_DELAY_SECONDS)

    # tracked_users.json: unverändert exklusiv fürs Go-Core, normaler
    # lokaler Schreibvorgang + Git-Commit im Workflow.
    with open(TRACKED_FILE, "w", encoding="utf-8") as f:
        json.dump(tracked, f, indent=2, ensure_ascii=False)

    # history.json: geteilt, läuft komplett über die API (siehe oben).
    sync_history(pending_entries)

    found_count = sum(1 for e in tracked if e.get("found"))
    print(f"Fertig: {found_count}/{len(tracked)} gefunden.")
    print(f"{TRACKED_FILE} aktualisiert (lokal/Git), history.json über API gemerged.")


if __name__ == "__main__":
    run_tracker()
