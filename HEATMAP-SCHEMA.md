# Datenfluss & Heatmap-Berechnung (Schema)

## 1. Gesamter Datenfluss

```
┌───────────────────────────┐
│  data/tracked_users.json   │   Liste der zu beobachtenden Namen
│  [{name, found, ...}]      │
└─────────────┬───────────────┘
              │
              ▼
┌─────────────────────────────────────────────┐
│  tracker.py (GitHub Actions,                  │
│  ausgelöst alle ~2 Min. über cron-job.org)    │
│                                                │
│   1. POST /api/auth/login  →  Bearer-Token     │
│   2. je Name:                                  │
│        GET /api/rankings?q=<name>              │
│        exakter Namenstreffer?                   │
│          ja   → {score, alliance, rank, online} │
│          nein → found:false                     │
└──────────┬─────────────────────────┬───────────┘
           │                         │
           ▼                         ▼
┌───────────────────────┐   ┌──────────────────────────────────┐
│ tracked_users.json      │   │ history.json                       │
│ (wird JEDES MAL           │   │ { "Name": [                        │
│  überschrieben - nur       │   │     {ts, score, online},           │
│  der AKTUELLE Stand)       │   │     {ts, score, online},           │
│                            │   │     ...  (ein Eintrag PRO           │
│  {name, found, score,      │   │     Abfrage, 90 Tage Rückhalt)      │
│   alliance, rank, online,  │   │   ] }                               │
│   lastChecked}             │   └───────────────┬─────────────────────┘
└───────────────────────┘                     │
                                                ▼
                              ┌──────────────────────────────────┐
                              │  Browser-Extension (options.js)     │
                              │  liest history.json / storage.local │
                              │  → Punkte-Graph                      │
                              │  → Aktivitäts-Heatmap                │
                              │  → Beste Angriffsfenster             │
                              └──────────────────────────────────┘
```

## 2. Heatmap: Einträge → 168 Zeit-Buckets

Jeder Verlaufs-Eintrag wird anhand seines Zeitstempels genau einem von
168 Feldern zugeordnet (7 Wochentage × 24 Stunden):

```
   ts (Unix-Millisekunden)
        │
        ▼
   new Date(ts)
        │
        ├── getDay()    → Wochentag (0=So ... 6=Sa)
        └── getHours()  → Stunde (0-23)
                │
                ▼
   Bucket-Index = Wochentag * 24 + Stunde
```

## 3. Pro Bucket: Offline-Wahrscheinlichkeit berechnen

Beispiel Bucket "Mittwoch, 15 Uhr" mit 3 gesammelten Beobachtungen:

```
  Eintrag A:  online = false   ─┐
  Eintrag B:  online = false    ├──  total = 3
  Eintrag C:  online = true    ─┘    online = 1

  offline-Score = 1 - (online / total)
                = 1 - (1 / 3)
                = 0,67  →  "67% offline"
```

```
                        total < 3 ?
              ┌─────────────┴─────────────┐
              │ ja                        │ nein
              ▼                           ▼
      Bucket = null                Bucket = {score, total}
      Zelle bleibt LEER            Zelle wird eingefärbt:
      ("zu wenig Daten")           höherer offline-Score → grüner
                                    niedrigerer offline-Score → röter
```

## 4. Heatmap-Raster (7×24)

```
        0   1   2   3   4  ...  14  15  16 ...  23
  So  [ · ] [ · ] [ · ] [ · ] [ · ] ... [ · ] [ · ] [ · ] ... [ · ]
  Mo  [ · ] [ · ] [ · ] [ · ] [ · ] ... [ · ] [ · ] [ · ] ... [ · ]
  Di  [ · ] [ · ] [ · ] [ · ] [ · ] ... [ · ] [ · ] [ · ] ... [ · ]
  Mi  [ · ] [ · ] [ · ] [ · ] [ · ] ... [ · ] [▓67%] [ · ] ... [ · ]
  Do  [ · ] [ · ] [ · ] [ · ] [ · ] ... [ · ] [ · ] [ · ] ... [ · ]
  Fr  [ · ] [ · ] [ · ] [ · ] [ · ] ... [ · ] [ · ] [ · ] ... [ · ]
  Sa  [ · ] [ · ] [ · ] [ · ] [ · ] ... [ · ] [ · ] [ · ] ... [ · ]

  [ · ]  = leer, < 3 Beobachtungen
  [▓67%] = eingefärbt, hier: Mi 15 Uhr, 67% offline (n=3)
```

## 5. Beste Angriffsfenster: Ranking

```
  Alle 168 Buckets
        │
        ▼
  Buckets mit total < 3 rausfiltern
        │
        ▼
  Nach offline-Score absteigend sortieren
        │
        ▼
  Top 3 nehmen
        │
        ▼
  ┌───────────────────────────────────────┐
  │ Mi 22:00 - 23:00   75% offline (n=4)     │
  │ Mi 15:00 - 16:00   67% offline (n=3)     │
  │ Mi 23:00 - 0:00    60% offline (n=5)     │
  └───────────────────────────────────────┘
```

## Wichtig zu wissen

- **Mehr Beobachtungen pro Bucket = genaueres Bild.** Deshalb schreibt
  `tracker.py` inzwischen bei *jeder* Abfrage einen Eintrag, nicht gedrosselt
  auf ein festes Zeitintervall (das würde die Stichprobenzahl künstlich
  verkleinern und die Prozentwerte ungenauer machen).
- **Ein Bucket braucht über mehrere verschiedene Tage hinweg Daten**, um
  wirklich aussagekräftig zu sein - mehrere Beobachtungen innerhalb
  derselben Stunde am selben Tag zählen zwar formal auch als "≥ 3", sagen
  aber weniger über ein echtes Wochenmuster aus als Beobachtungen von
  mehreren unterschiedlichen Mittwochen.
