# Prototyp Warenkontrolle Glas

Lauffähiger Prototyp einer KI-gestützten Qualitätskontrolle für Glasbauteile, gebaut für
Nutzertests. Kein Produktivsystem: kein Login, keine Cloud, die KI ist ein Mock auf Basis
künstlich erzeugter Testbilder. Alle Daten sind fiktiv.

- Kontext (Personas, Journeys, Fehlerkatalog): [docs/vision.md](docs/vision.md)
- Umsetzungskonzept und Festlegungen: [docs/konzept.md](docs/konzept.md)
- Grenzwerte und Regeln: [app/config.py](app/config.py)

Alle Befehle im Projektordner ausführen. Sie funktionieren in PowerShell und in Git Bash.

## Einrichten (einmalig)

Voraussetzung: Python 3.14 mit dem Launcher `py`.

```bash
py -3.14 -m venv .venv
```

```bash
.venv/Scripts/python.exe -m pip install -r requirements.txt
```

Demo-Teile und Testbilder erzeugen:

```bash
.venv/Scripts/python.exe -m tools.generate_images --count 60 --seed 42
```

Startzustand herstellen (erzeugt beim ersten Mal auch die Vergangenheitsdaten):

```bash
.venv/Scripts/python.exe -m tools.reset_demo
```

## Server starten

```bash
.venv/Scripts/python.exe -m uvicorn app.main:app --port 8000
```

Beenden mit `Strg+C`.

| Ansicht | Adresse |
|---|---|
| Startseite | http://127.0.0.1:8000 |
| Prüfstation (Thomas) | http://127.0.0.1:8000/station |
| Historie | http://127.0.0.1:8000/station/history |
| Dashboard (Martin) | http://127.0.0.1:8000/dashboard |

An der Prüfstation startet der Einlauf beim Öffnen der Seite von selbst und stoppt nach
450 Teilen.

## Vor jedem Test zurücksetzen

```bash
.venv/Scripts/python.exe -m tools.reset_demo
```

Danach die Prüfstation im Browser neu laden. Der Server darf dabei weiterlaufen.

Der Reset löscht nur die Prüfungen und Reviews der Demo- und Füllteile, legt die Füllteile neu
an und lässt die ersten 150 Teile des Stroms sofort prüfen. Die Vergangenheitsdaten bleiben
erhalten. Reichen sie nicht bis gestern (neuer Tag), werden sie automatisch neu erzeugt.

## Bei Bedarf

Vergangenheitsdaten (30 Tage) von Hand neu erzeugen. Normalerweise übernimmt das `reset_demo`.

```bash
.venv/Scripts/python.exe -m tools.seed_history
```

Ergebnisse der Mock-KI für alle Teile anzeigen (schreibt nichts in die Datenbank):

```bash
.venv/Scripts/python.exe -m tools.mock_report
```

Tests ausführen (dauert etwa 2 Minuten):

```bash
.venv/Scripts/python.exe -m pytest
```

## Daten

Alles Erzeugte liegt in `data/`: Datenbank `warenkontrolle.db`, Demo-Bilder in `images/`,
Labels in `labels/`, Bildvorrat der Vergangenheitsdaten in `history_pool/`. Wird `data/`
gelöscht, mit `generate_images` und `reset_demo` neu aufbauen (siehe „Einrichten“).
