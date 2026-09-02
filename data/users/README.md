# data/users/

Pro Installation der Islandking-Tango-Tracker-Browser-Extension liegt hier
ein Unterordner mit ihrer eigenen `tracked_users.json` (Watchlist +
aktueller Status dieser Installation):

```
data/users/<uuid>/tracked_users.json
```

Die `<uuid>` wird einmalig pro Extension-Installation per
`crypto.randomUUID()` erzeugt und lokal in `storage.local` gespeichert -
kein Personenbezug, keine IP/Fingerprint-basierte Zuordnung.

Nur die jeweilige Extension-Installation schreibt in ihren eigenen
Unterordner. `data/history.json` (Punkteverlauf) bleibt weiterhin
gemeinsam genutzt - Go-Core, alle Extensions und `tracker.py` mergen dort
rein (GET → merge → PUT mit sha-Retry), damit derselbe Name nicht mehrfach
getrennt aufgezeichnet wird.
