import os
import sys
import requests

# Konfiguration über Umgebungsszenarien (z. B. GitHub Secrets)
USERNAME = os.getenv("IK_USER")
PASSWORD = os.getenv("IK_PASS")
BASE_URL = "https://islandking.ch"

if not USERNAME or not PASSWORD:
    print("Fehler: Zugangsdaten (ISLANDKING_USER / ISLANDKING_PASS) sind nicht gesetzt.")
    sys.exit(1)

def run_tracker():
    session = requests.Session()
    
    # 1. API-Login durchführen
    print("Authentifiziere bei Islandking...")
    login_response = session.post(
        f"{BASE_URL}/api/auth/login",
        json={"username": USERNAME, "password": PASSWORD},
        headers={"Content-Type": "application/json"}
    )
    
    if login_response.status_code != 200:
        print(f"Login fehlgeschlagen. Status Code: {login_response.status_code}")
        print(login_response.text)
        sys.exit(1)
        
    data = login_response.json()
    token = data.get("token")
    
    if not token:
        print("Fehler: Kein Auth-Token in der Antwort erhalten.")
        sys.exit(1)
        
    print("Login erfolgreich. Token erhalten.")
    
    # 2. Authentifizierte Requests vorbereiten
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    
    # 3. Daten abrufen / Tracking ausführen
    print("Rufe Spieldaten ab...")
    data_response = session.get(f"{BASE_URL}/api/me", headers=headers)
    
    if data_response.status_code != 200:
        print(f"Fehler beim Abrufen der Daten. Status Code: {data_response.status_code}")
        sys.exit(1)
        
    user_data = data_response.json()
    print("Daten erfolgreich abgerufen:")
    print(user_data)
    
    # Hier kannst du die Logik für das Speichern oder Verarbeiten der Daten ergänzen

if __name__ == "__main__":
    run_tracker()

import json
from datetime import datetime

# Nach erfolgreichem user_data Abruf:
os.makedirs("data", exist_ok=True)
filename = f"data/tracking_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.json"

with open(filename, "w", encoding="utf-8") as f:
    json.dump(user_data, f, indent=4, ensure_ascii=False)

print(f"Daten erfolgreich in {filename} gespeichert.")
