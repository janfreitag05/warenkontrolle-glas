# Projektregeln: Prototyp Warenkontrolle Glas

- Kontext steht in docs/vision.md. Bei Unklarheiten dort nachlesen, nicht raten.
- Prototyp für Nutzertests, kein Produktivsystem: kein Login, keine Cloud, keine Skalierung.
- Stack: Python 3, FastAPI, Jinja2-Templates, SQLite (Standardbibliothek sqlite3), Chart.js lokal eingebunden, Pillow für Testbilder, pytest.
- Keine neuen Abhängigkeiten ohne Rückfrage.
- Nur fiktive Daten. Keine echten Firmen-, Personen- oder Produktionsdaten.
- Keine API-Schlüssel, keine externen KI-Dienste. Die KI ist ein Mock.
- Grenzwerte und Regeln nur in config.py.
- Ein Schritt pro Auftrag. Nur die im Auftrag genannten Dateien und Ordner ändern.
- "Fertig" nur mit Beleg: Testausgabe oder Beschreibung, wie man es im Browser prüft.
- Oberfläche auf Deutsch, Code und Bezeichner auf Englisch.
