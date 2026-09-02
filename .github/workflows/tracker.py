import os
import json
import time
import traceback
from playwright.sync_api import sync_playwright

USERNAME = os.environ.get("IK_USER")
PASSWORD = os.environ.get("IK_PASS")
TARGETS = ["ZielSpieler1", "ZielSpieler2"] # Hier deine Tangos eintragen

os.makedirs("data", exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_context().new_page()

    try:
        print("Navigiere zur Login-Seite...")
        page.goto("https://islandking.ch/login", timeout=60000)
        
        print("Fülle Login-Daten aus...")
        # Suche über den Platzhalter-Text aus deinem Screenshot
        page.fill('input[placeholder="Benutzername"]', USERNAME)
        page.fill('input[placeholder="Passwort"]', PASSWORD)
        
        print("Klicke auf Einloggen...")
        # Klick auf den blauen Button mit dem Text "Einloggen"
        page.get_by_role("button", name="Einloggen").click()
        
        print("Warte auf erfolgreichen Login...")
        # Warten, bis nach dem Login die Hauptseite/Dashboard erreicht ist
        page.wait_for_url("**/game**", timeout=15000) # Falls die Hauptseite anders heißt, passen wir das an

        print("Lade Tango-Daten über die API...")
        for name in TARGETS:
            api_url = f"https://islandking.ch/api/rankings?q={name}"
            response = page.request.get(api_url)
            
            history_file = f"data/{name}.json"
            history = []
            if os.path.exists(history_file):
                with open(history_file, "r", encoding="utf-8") as f:
                    try: history = json.load(f)
                    except: pass

            if response.status == 200:
                data = response.json()
                players = data.get("players", [])
                match = next((p for p in players if p["name"].lower() == name.lower()), players[0] if players else None)
                
                result = {
                    "found": True,
                    "score": match["score"],
                    "alliance": match.get("alliance"),
                    "rank": match.get("rank"),
                    "online": bool(match.get("online")),
                    "ts": int(time.time() * 1000)
                } if match else {"found": False, "ts": int(time.time() * 1000)}
            else:
                result = {"found": False, "error": f"HTTP {response.status}", "ts": int(time.time() * 1000)}

            history.append(result)
            if len(history) > 5000: history = history[-5000:]
            
            with open(history_file, "w", encoding="utf-8") as f:
                json.dump(history, f, ensure_ascii=False, indent=2)
        print("Tracking erfolgreich abgeschlossen.")

    except Exception as e:
        print(f"Fehler beim Tracking aufgetreten:")
        traceback.print_exc()
        raise e
    finally:
        browser.close()
