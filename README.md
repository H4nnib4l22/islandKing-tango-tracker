# islandKing-tango-tracker

Trackt Online-Status und Punkteverlauf ausgewählter Spieler ("Tangos") auf
[islandking.ch](https://islandking.ch) automatisiert über GitHub Actions –
und liefert damit die Datenbasis für Heatmap, Punkte-Graph und
"beste Angriffsfenster" in der zugehörigen Browser-Extension
(*Islandking Tango Tracker*).

## Wie es funktioniert (kurz)

1. `tracker.py` loggt sich mit einem eigenen Islandking-Account ein
2. Für jeden Namen in `data/tracked_users.json` wird `GET /api/rankings?q=<name>`
   abgefragt (exakter Namenstreffer, sonst `found: false`)
3. Der aktuelle Stand landet in `data/tracked_users.json`, ein neuer
   Verlaufs-Eintrag `{ts, score, online}` wird an `data/history.json`
   angehängt
4. GitHub Actions committet beide Dateien automatisch zurück ins Repo

Ausführlicheres Schema dazu: [`HEATMAP-SCHEMA.md`](./HEATMAP-SCHEMA.md).

## Dateien

| Datei | Inhalt |
|---|---|
| `tracker.py` | Das eigentliche Skript (Login, Abfrage, Speichern) |
| `.github/workflows/tracker.yml` | GitHub-Actions-Workflow, der `tracker.py` ausführt |
| `data/tracked_users.json` | Liste der zu beobachtenden Spielernamen + aktueller Stand |
| `data/history.json` | Wachsender Verlauf `{name: [{ts, score, online}, ...]}` |

## Einrichtung

### 1. Repo-Secrets

Repo → Settings → Secrets and variables → Actions:

| Secret | Bedeutung |
|---|---|
| `IK_USER` | Islandking-Benutzername (eigener Account) |
| `IK_PASS` | Islandking-Passwort |

### 2. Workflow-Berechtigungen

Repo → Settings → Actions → General → "Workflow permissions" →
**Read and write permissions** (nötig, damit der Workflow die Daten-Dateien
zurückcommitten darf).

### 3. Auslösung

Der Workflow hat drei Trigger (`schedule`, `workflow_dispatch`,
`repository_dispatch`). Der eingebaute `schedule`-Trigger von GitHub
Actions ist "best effort" und in der Praxis unzuverlässig für einen engen
Takt – produktiv läuft die Auslösung daher über einen externen Dienst wie
[cron-job.org](https://cron-job.org), der per API einen `workflow_dispatch`
auslöst:

- URL: `https://api.github.com/repos/<owner>/<repo>/actions/workflows/tracker.yml/dispatches`
- Methode: POST
- Headers: `Accept: application/vnd.github+json`,
  `Authorization: Bearer <GitHub Personal Access Token>`,
  `X-GitHub-Api-Version: 2022-11-28`
- Body: `{"ref": "main"}`
- Empfohlenes Intervall: alle 2 Minuten

Der GitHub-Token (Fine-grained, nur `Actions: Read and write` auf dieses
eine Repo) läuft nach der gewählten Frist ab und muss dann erneuert werden.

## Spieler hinzufügen/entfernen

Einfach einen Eintrag in `data/tracked_users.json` ergänzen bzw. entfernen:

```json
{ "name": "Spielername", "found": false }
```

Alles Weitere (Score, Allianz, Rang, Online-Status, `lastChecked`) füllt der
nächste Lauf automatisch aus.

## Datenaufbewahrung

`data/history.json` behält nur die letzten **90 Tage** pro Name (älteres
wird beim nächsten Eintrag für diesen Namen automatisch rausgefiltert, kein
separater Aufräum-Job nötig). Wird ein Name aus `tracked_users.json`
entfernt, bleibt seine bisherige Historie unverändert stehen – die
Bereinigung läuft nur, wenn für den Namen noch ein neuer Eintrag angehängt
wird.

Kein eingebauter Langzeit-Export – wer ältere Daten dauerhaft behalten
will, muss sie vor Ablauf der 90 Tage selbst sichern.
