import os
import json
import time
from playwright.sync_api import sync_playwright

USERNAME = os.environ.get("IK_USER")
PASSWORD = os.environ.get("IK_PASS")
TARGETS = ["ZielSpieler1", "ZielSpieler2"] # Hier deine Tangos eintragen

os.makedirs("data", exist_ok=True)

with sync_playwright() as p:
    # Browser im Hintergrund starten
    browser = p.chromium.launch(headless=True)
    page = browser.new_context().new_page()

    try:
        # 1. Zur Login-Seite navigieren
        page.goto("https://islandking.ch/login", timeout=60000)
        
        # 2. Einloggen (Die Selektoren [name="email"] etc. müsstest du ggf. anpassen, falls die IDs anders heißen)
        page.fill('input[name="email"]', USERNAME) # Oder input[name="username"]
        page.fill('input[name="password"]', PASSWORD)
        page.click('button[type="submit"]')
        
        # Warten, bis der Login durch ist und man auf der Dashboard-/Spielseite landet
        page.wait_for_url("**/dashboard**", timeout=15000) # URL-Muster nach dem Login anpassen

        # 3. Daten für jeden Tango abgreifen (nutzt die echte Browser-Session)
        for name in TARGETS:
            # Entweder direkt über die API der Seite fetch-en, während man eingeloggt ist:
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

    except Exception as e:
        print(f"Fehler beim Tracking: {e}")
        traceback.print_exc()
        raise e
    finally:
        browser.close()
