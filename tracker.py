import os
import sys
import json
import time
import random
import base64
from datetime import datetime, timezone

import requests

# Konfiguration über Umgebungsvariablen (bisher GitHub Actions Secrets,
# jetzt identisch als Env-Vars im bot-hosting.net-Panel gesetzt - s.
# [[project_tango_tracker_vpn_fix]])
USERNAME = os.getenv("IK_USER")
PASSWORD = os.getenv("IK_PASS")
# data/ liegt seit der Trennung public/private in einem eigenen Repo.
# Braucht einen eigenen Token, da ein automatischer GITHUB_TOKEN (GitHub
# Actions) nur Zugriff auf das Repo haette, in dem der Workflow laeuft -
# hier ausserhalb von Actions ohnehin nur ein normaler PAT.
DATA_REPOSITORY = os.getenv("DATA_REPOSITORY", "H4nnib4l22/islandKing-tango-tracker-data")
DATA_GITHUB_TOKEN = os.getenv("DATA_REPO_TOKEN")

BASE_URL = "https://islandking.ch"
# Beide Pfade sind API-Pfade *innerhalb* von DATA_REPOSITORY - kein lokaler
# Checkout mehr noetig (Umstellung 2026-09-10: lief vorher als GitHub Action
# mit lokalem Checkout+git-commit fuer tracked_users.json; jetzt Dauerlauf
# auf bot-hosting.net ohne Checkout, daher beide Dateien konsequent ueber
# die Contents-API wie history.json es schon vorher tat).
TRACKED_PATH = "data/tracked_users.json"
HISTORY_PATH = "data/history.json"  # GETEILT mit den Browser-Extensions
REQUEST_DELAY_SECONDS = 0.3  # kleine, höfliche Pause zwischen Rangliste-Seiten innerhalb eines Zyklus

# Müssen mit dem übereinstimmen, was die Browser-Extension für denselben,
# jetzt gemeinsam genutzten Ort verwendet (Absprache siehe Chat).
HISTORY_RETENTION_DAYS = 30
HISTORY_MAX_PER_NAME = 5000
SCORE_CHECKPOINT_INTERVAL_MS = 20 * 60 * 1000  # Punkte nur alle ~20 Min. neu festhalten
MAX_MERGE_RETRIES = 3

# Dauerlauf statt Einmal-Ausführung (Nutzerwunsch 2026-09-10: Umzug von
# GitHub Actions/cron-job.org auf eine dauerhaft laufende Instanz auf
# bot-hosting.net - macht beides obsolet, kein Runner-Spin-up/Warteschlangen-
# Varianz mehr, Login nur noch einmal statt pro Zyklus). RUN_ONCE=1 behält
# das alte Einmal-Verhalten für manuelles Testen/GitHub-Actions-Fallback.
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "60"))
RUN_ONCE = os.getenv("RUN_ONCE", "").lower() in ("1", "true", "yes")

if not USERNAME or not PASSWORD:
    print("Fehler: Zugangsdaten (IK_USER / IK_PASS) sind nicht gesetzt.")
    sys.exit(1)

if not DATA_GITHUB_TOKEN:
    print("Fehler: DATA_REPO_TOKEN ist nicht gesetzt (wird für die tracked_users.json/history.json-API im privaten Daten-Repo gebraucht).")
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


class IslandkingClient:
    """Haelt Session+Token ueber mehrere Zyklen des Dauerlaufs hinweg (statt
    wie bisher pro GitHub-Actions-Run neu einzuloggen). Loggt sich bei einer
    abgelaufenen Session (401) automatisch einmal neu ein und wiederholt den
    Request - identisch zum apiFetch()-Muster der Browser-Extensions."""

    def __init__(self):
        self.session = requests.Session()
        self.headers = {"Accept": "application/json"}
        self._login()

    def _login(self):
        token = login(self.session)
        self.headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    def get(self, path, params=None):
        res = self.session.get(f"{BASE_URL}{path}", params=params, headers=self.headers)
        if res.status_code == 401:
            print("Islandking-Session abgelaufen (401) - logge erneut ein.")
            self._login()
            res = self.session.get(f"{BASE_URL}{path}", params=params, headers=self.headers)
        return res


def fetch_all_players(client):
    """Läd die komplette Rangliste über GET /api/rankings?page=N (ohne q)
    und baut ein {name.lower(): player}-Mapping.

    Nutzerwunsch (2026-09-10): ersetzt die bisherige Einzel-Suche pro
    Spieler (1 Request + Sleep PRO getracktem Namen). Aus
    RankingsView-DnNik556.js verifiziert: derselbe Endpoint, ohne q wird
    paginiert ({page:i} statt {q:r}), Antwort enthält players/page/
    pageSize/playersTotal. Skaliert mit der Gesamtspielerzahl der
    Rangliste, nicht mit der Watchlist-Größe - bei wachsender Watchlist
    bleibt die Kostenzahl gleich, statt linear mitzuwachsen. Seitenzahl wird
    JEDEN Zyklus frisch aus der Antwort berechnet, nicht hartcodiert -
    wächst die Rangliste über die aktuellen 320 Spieler (16 Seiten à 20)
    hinaus, kommen automatisch weitere Seiten dazu.
    """
    by_name = {}
    page = 1
    total_pages = 1
    while page <= total_pages:
        res = client.get("/api/rankings", params={"page": page})
        if res.status_code != 200:
            print(f"  Warnung: HTTP {res.status_code} bei Rangliste Seite {page}")
            break
        data = res.json()
        for p in data.get("players", []):
            name = p.get("name")
            if name:
                by_name[name.lower()] = p
        if page == 1:
            page_size = data.get("pageSize") or len(data.get("players", [])) or 1
            total = data.get("playersTotal", 0)
            total_pages = max(1, -(-total // page_size))  # ceil
            print(f"  Rangliste: {total} Spieler, {total_pages} Seite(n) (pageSize={page_size}).")
        page += 1
        if page <= total_pages:
            time.sleep(REQUEST_DELAY_SECONDS)
    return by_name


def result_from_player(p):
    """Baut dieselbe {found, id, score, alliance, rank, online}-Form wie
    die bisherige lookup_player(), jetzt aus einem bereits geladenen
    Rangliste-Eintrag statt einer Einzel-Suche."""
    if p is None:
        return {"found": False}
    return {
        "found": True,
        "id": p.get("id"),
        "score": p.get("score"),
        "alliance": p.get("alliance"),
        "rank": p.get("rank"),
        "online": p.get("online"),
    }


def last_score_ts(history, name):
    """Zeitstempel des letzten Eintrags mit echtem (nicht-null) Score für
    diesen Namen, oder None, falls noch keiner existiert."""
    for e in reversed(history.get(name, [])):
        if isinstance(e.get("score"), (int, float)):
            return e["ts"]
    return None


def record_result(name, result, initial_history, pending_entries):
    """Verlaufs-Eintrag nur bei tatsaechlichem Treffer - ohne Treffer gibt
    es weder Score noch Online-Status zum Aufzeichnen. Score-Checkpoint:
    Score nur alle SCORE_CHECKPOINT_INTERVAL_MS neu festhalten, sonst null.
    Gemeinsam von der Haupt-Watchlist und den Extra-Namen aus
    collect_extra_names() genutzt, damit beide identisch behandelt werden."""
    if not result["found"]:
        return
    ts_ms = int(time.time() * 1000)
    last_ts = last_score_ts(initial_history, name)
    include_score = last_ts is None or (ts_ms - last_ts) >= SCORE_CHECKPOINT_INTERVAL_MS
    new_entry = {"ts": ts_ms, "score": result["score"] if include_score else None, "online": result["online"]}
    pending_entries.setdefault(name, []).append(new_entry)


# ---------------------------------------------------------------------
# GitHub-Contents-API-Zugriff auf DATA_REPOSITORY - sowohl history.json
# (GETEILT mit den Browser-Extensions, mehrere unabhängige Schreiber
# gleichzeitig möglich, daher GET->merge->PUT mit sha-Retry) als auch
# tracked_users.json (seit 2026-09-10 ebenfalls über die API statt lokalem
# Checkout+git-commit, s. o. - hier reicht ein einfacher sha-Retry ohne
# inhaltlichen Merge, weil diese Instanz der einzige Schreiber ist) und
# data/users/* (Extension-Watchlists, nur lesend).
# ---------------------------------------------------------------------


def github_api_headers():
    return {
        "Authorization": f"Bearer {DATA_GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _decode_content_response(data):
    """Contents-API embedded ab ~1MB keinen Content mehr (encoding:"none")
    - dann stattdessen über die Blobs-API laden, die unabhängig von der
    Dateigroesse funktioniert. Gemeinsam von history.json und
    tracked_users.json genutzt."""
    if data.get("encoding") == "none":
        blob_res = requests.get(data["git_url"], headers=github_api_headers())
        blob_res.raise_for_status()
        blob = blob_res.json()
        return base64.b64decode(blob["content"]).decode("utf-8")
    return base64.b64decode(data["content"]).decode("utf-8")


def fetch_remote_history():
    """Holt die aktuelle history.json direkt über die GitHub-Contents-API.
    Gibt (history_dict, sha) zurück. sha ist None, falls die Datei noch
    nicht existiert (dann wird beim ersten PUT keins mitgeschickt)."""
    url = f"https://api.github.com/repos/{DATA_REPOSITORY}/contents/{HISTORY_PATH}"
    res = requests.get(url, headers=github_api_headers())
    if res.status_code == 404:
        return {}, None
    res.raise_for_status()
    data = res.json()
    return json.loads(_decode_content_response(data)), data["sha"]


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
    url = f"https://api.github.com/repos/{DATA_REPOSITORY}/contents/{HISTORY_PATH}"
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


def fetch_remote_tracked():
    """Holt tracked_users.json über die Contents-API. Gibt (liste, sha)
    zurück, sha None falls die Datei noch nicht existiert."""
    url = f"https://api.github.com/repos/{DATA_REPOSITORY}/contents/{TRACKED_PATH}"
    res = requests.get(url, headers=github_api_headers())
    if res.status_code == 404:
        return [], None
    res.raise_for_status()
    data = res.json()
    return json.loads(_decode_content_response(data)), data["sha"]


def push_tracked(tracked):
    """Schreibt tracked_users.json komplett neu (kein inhaltlicher Merge
    nötig - diese Instanz ist seit dem Umzug auf bot-hosting.net der
    einzige Schreiber dieser Datei). sha-Retry bei 409 trotzdem, falls
    jemand die Datei manuell im GitHub-Web bearbeitet."""
    url = f"https://api.github.com/repos/{DATA_REPOSITORY}/contents/{TRACKED_PATH}"
    content_str = json.dumps(tracked, indent=2, ensure_ascii=False)

    for attempt in range(1, MAX_MERGE_RETRIES + 1):
        get_res = requests.get(url, headers=github_api_headers())
        sha = get_res.json().get("sha") if get_res.status_code == 200 else None

        payload = {
            "message": "Update tracked_users.json (tracker.py)",
            "content": base64.b64encode(content_str.encode("utf-8")).decode("ascii"),
        }
        if sha:
            payload["sha"] = sha

        res = requests.put(url, headers=github_api_headers(), json=payload)
        if res.status_code in (200, 201):
            return True
        if res.status_code == 409:
            print(f"Konflikt beim Schreiben von tracked_users.json (Versuch {attempt}/{MAX_MERGE_RETRIES}), erneuter Versuch...")
            time.sleep(1 + random.random() * 2)
            continue
        if res.status_code == 404:
            # Kein Datenverlust - dieser Zyklus konnte nur nicht schreiben,
            # naechster Zyklus versucht es automatisch wieder. GitHub gibt
            # bei einem privaten Repo bewusst 404 statt 403 zurueck, wenn
            # der Token keinen Zugriff hat (statt zu verraten, dass es das
            # Repo gibt) - daher meist Token-Berechtigung oder DATA_REPOSITORY,
            # nicht das Repo selbst.
            print(
                f"Hinweis: tracked_users.json konnte nicht geschrieben werden (HTTP 404) - "
                f"DATA_REPO_TOKEN hat vermutlich keinen Zugriff auf {DATA_REPOSITORY!r}, "
                f"oder DATA_REPOSITORY ist falsch gesetzt. Kein Datenverlust, naechster Zyklus versucht es erneut."
            )
            return False

        print(f"Warnung: Unerwarteter Status {res.status_code} beim Schreiben von tracked_users.json: {res.text}")
        return False

    print("Fehler: tracked_users.json konnte nach mehreren Versuchen nicht geschrieben werden.")
    return False


def collect_extra_names(tracked):
    """Namen aus allen data/users/<uuid>/tracked_users.json (Watchlists der
    Browser-Extensions), die noch nicht in der globalen tracked_users.json
    stehen. Läuft unabhängig davon, ob irgendwo ein islandking.ch-Tab offen
    ist - ohne diese Ergaenzung wuerden Namen, die nur eine Extension
    beobachtet, nie ein Hintergrund-Tracking bekommen. Liefert nur die
    Namen zurueck, NICHT die Dateien selbst - data/users/ bleibt exklusiv
    von den Extensions beschrieben, diese Funktion liest nur mit.

    Seit 2026-09-10 über die GitHub-Contents-API statt lokalem Checkout
    (Verzeichnislisting + eine Datei pro UUID) - kein Dateisystem-Zugriff
    mehr nötig, funktioniert genauso auf bot-hosting.net ohne git-Checkout.
    """
    known = {e.get("name", "").lower() for e in tracked if e.get("name")}
    extra = set()

    list_url = f"https://api.github.com/repos/{DATA_REPOSITORY}/contents/data/users"
    res = requests.get(list_url, headers=github_api_headers())
    if res.status_code == 404:
        return []
    res.raise_for_status()

    for entry in res.json():
        if entry.get("type") != "dir":
            continue
        file_url = f"https://api.github.com/repos/{DATA_REPOSITORY}/contents/data/users/{entry['name']}/tracked_users.json"
        file_res = requests.get(file_url, headers=github_api_headers())
        if file_res.status_code != 200:
            continue
        try:
            entries = json.loads(_decode_content_response(file_res.json()))
        except (json.JSONDecodeError, KeyError) as err:
            print(f"  Warnung: data/users/{entry['name']}/tracked_users.json konnte nicht gelesen werden: {err}")
            continue
        for e in entries:
            name = e.get("name")
            if name and name.lower() not in known:
                extra.add(name)

    return sorted(extra)


def run_once(client):
    tracked, _tracked_sha = fetch_remote_tracked()

    # Einmaliger Snapshot zu Beginn nur für die Score-Checkpoint-Entscheidung
    # (ist ein anderer Zweck als der Merge am Ende, der nochmal frisch holt -
    # kleine Ungenauigkeit hier ist unkritisch, siehe Chat).
    initial_history, _ = fetch_remote_history()
    pending_entries = {}  # {name: [entry, ...]} - nur was DIESER Zyklus neu produziert
    extra_names = collect_extra_names(tracked)

    print(f"Lade komplette Rangliste (deckt {len(tracked)} Watchlist- + {len(extra_names)} Extra-Namen ab)...")
    all_players = fetch_all_players(client)
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Nutzerwunsch (2026-09-10): nicht mehr jeden Namen einzeln loggen (bei
    # 30+ Namen unuebersichtlich) - nur noch eine Zusammenfassung am Ende,
    # mit Namensliste nur fuer die NICHT gefundenen (die einzigen, die
    # tatsaechlich Aufmerksamkeit brauchen).
    not_found = []

    for entry in tracked:
        name = entry.get("name")
        if not name:
            continue

        result = result_from_player(all_players.get(name.lower()))
        entry.update(result)
        entry["lastChecked"] = now_iso
        if not result["found"]:
            not_found.append(name)

        record_result(name, result, initial_history, pending_entries)

    if extra_names:
        for name in extra_names:
            result = result_from_player(all_players.get(name.lower()))
            if not result["found"]:
                not_found.append(f"{name} (extra)")
            record_result(name, result, initial_history, pending_entries)
            # Landet jetzt mit in tracked_users.json (nicht mehr nur in
            # history.json) - sonst sieht die Extension nie die von hier
            # abgefragte Allianz/Rang fuer Namen, die nur SIE selbst kennt,
            # weil buildResults() in background.js ausschliesslich die
            # Top-Level tracked_users.json liest, nie data/users/*.
            # origin="extension" markiert das als NICHT Teil der eigenen
            # Watchlist - das GitHub-Dashboard (Flutter) filtert danach,
            # sonst wuerde es die Extension-Watchlists ALLER Nutzer
            # anzeigen, die dieses Repo teilen, nicht nur die eigene.
            entry = {"name": name}
            entry.update(result)
            entry["lastChecked"] = now_iso
            entry["origin"] = "extension"
            tracked.append(entry)

    push_tracked(tracked)
    sync_history(pending_entries)

    total = len(tracked) + len(extra_names)
    found_count = total - len(not_found)
    print(f"Fertig: {found_count}/{total} gefunden, {len(not_found)}/{total} nicht gefunden.")
    if not_found:
        print(f"  Nicht gefunden: {', '.join(not_found)}")


def run_forever():
    """Dauerlauf für bot-hosting.net (Nutzerwunsch 2026-09-10): ersetzt die
    bisherige GitHub-Actions-Trigger-Kette (cron-job.org -> repository_dispatch
    -> frischer Runner pro Zyklus) durch einen einzigen langlebigen Prozess
    mit internem Sleep. Login nur einmal beim Start (IslandkingClient loggt
    bei Bedarf automatisch neu ein), kein wiederholtes VPN/Checkout/pip-
    Overhead pro Zyklus mehr."""
    client = IslandkingClient()
    while True:
        started = time.time()
        try:
            run_once(client)
        except Exception as err:
            print(f"Fehler im Tracker-Zyklus: {err}")
        elapsed = time.time() - started
        sleep_for = max(1.0, POLL_INTERVAL_SECONDS - elapsed)
        print(f"Zyklus in {elapsed:.1f}s, warte {sleep_for:.0f}s bis zum naechsten...")
        time.sleep(sleep_for)


if __name__ == "__main__":
    if RUN_ONCE:
        run_once(IslandkingClient())
    else:
        run_forever()
