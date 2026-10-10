# Konzept: Prototyp Warenkontrolle Glas

Status: freigegeben am 2026-10-09. Dieses Dokument ist die Referenz für die Umsetzung.
Kontext (Personas, Journeys, Fehlerkatalog, Bewertungsregeln) steht in `docs/vision.md`.

## 1. Datenmodell (SQLite)

Grundsatz: KI-Ergebnis und Entscheidung von Thomas liegen in getrennten Tabellen. Das
KI-Ergebnis wird nie überschrieben.

| Tabelle | Felder | Zweck |
|---|---|---|
| `production_line` | `id`, `code` („L1“), `name` („Linie 1 – Flachglas“) | Fiktive Linien (3 Stück) |
| `batch` | `id`, `code` („CH-2610-07“), `line_id` → production_line, `produced_on` | Fiktive Chargen |
| `part` | `id`, `serial` („GT-0001234“), `batch_id` → batch, `image_path`, `created_at` | Ein Glasteil mit Bild |
| `gt_defect` | `id`, `part_id` → part, `defect_type`, `x`, `y`, `w`, `h`, `length_px` | Wahre Fehler aus dem Generator. Nur Mock und Tests lesen sie, die Oberfläche nie |
| `inspection` | `id`, `part_id` → part, `inspected_at`, `detector_version`, `ai_verdict` (OK/NOK/MANUAL), `ai_reason`, `status` (AUTO_DONE / PENDING_REVIEW / REVIEWED) | Ein Prüfvorgang |
| `detection` | `id`, `inspection_id` → inspection, `defect_type`, `x`, `y`, `w`, `h`, `length_px`, `confidence`, `severity` (NONE/MINOR/MAJOR/CRITICAL), `injected_error` | Erkennungen der KI. `injected_error` dient nur der Testauswertung und wird nie angezeigt |
| `review` | `id`, `inspection_id` → inspection (1:0..1), `decision` (CONFIRMED/CORRECTED), `final_verdict` (OK/NOK), `comment`, `reviewed_at` | Entscheidung von Thomas |
| `review_change` | `id`, `review_id` → review, `detection_id` (NULL bei übersehenem Fehler), `change` (REJECTED / TYPE_CHANGED / MISSED), `new_defect_type` | Korrekturen an einzelnen Fehlern |

Beziehungen: Linie 1:n Charge 1:n Teil 1:n Prüfung. Eine Prüfung hat n Erkennungen und
höchstens eine Review mit n Änderungen.

Endgültiges Urteil = `review.final_verdict`, falls vorhanden, sonst `ai_verdict`.
MANUAL ohne Review gilt als „offen“.

Prüfstatus und Endurteil werden **ausschließlich** über `inspection_status()` und
`final_verdict()` in `app/repository.py` ermittelt (auch als SQL-Funktionen registriert):
AUTO_DONE bei KI-Urteil OK, REVIEWED sobald eine Review existiert, sonst PENDING_REVIEW.
Die Spalte `inspection.status` wird beim Einlauf einmal gefüllt, aber von keiner Abfrage
gelesen. Ein Test durchsucht `app/` und `tools/` danach.

## 2. Ordnerstruktur

```
warenkontrolle-glas/
├── app/
│   ├── main.py              # FastAPI-App, Router, Static, Templates
│   ├── config.py            # ALLE Grenzwerte, Regeln, Mock-Fehlerraten, Pfade
│   ├── db.py                # Verbindung, Schema anlegen
│   ├── schema.sql
│   ├── repository.py        # SQL-Abfragen
│   ├── rules.py             # Erkennungen → Schweregrad + Urteil (reine Funktionen)
│   ├── detector.py          # Schnittstelle Detector + MockDetector
│   ├── routes/              # station.py, dashboard.py, api.py
│   ├── templates/           # base.html, index.html, station/, dashboard/
│   └── static/              # css/, js/, vendor/chart.umd.min.js (+ Lizenz)
├── tools/
│   ├── generate_images.py   # Testbild-Generator (Pillow)
│   ├── seed_history.py      # 30 Tage Vergangenheit für das Dashboard
│   └── reset_demo.py        # Startzustand für den nächsten Testteilnehmer
├── data/                    # generiert, nicht versioniert (images/, labels/, *.db)
├── tests/
├── docs/                    # vision.md, konzept.md
└── requirements.txt
```

## 3. Seiten und Komponenten

### Prüfstation (Thomas)

`/station` – Arbeitsansicht
- Kopfleiste: „Heute geprüft“ (zählt live hoch), „Automatisch freigegeben“, „Zu prüfen“,
  Link zur Historie. Kein Einlaufband, keine Bedienelemente für den Einlauf.
- Einlauf: startet beim Öffnen der Seite automatisch im Takt `STATION_INFEED_INTERVAL_S`
  (0,5 s) und stoppt nach `STATION_MAX_PARTS` Teilen („Alle Teile geprüft“). OK-Teile laufen
  unsichtbar durch, nur NOK- und MANUAL-Teile kommen in die Warteschlange „Zu prüfen“.
- Strom (450 Teile): die 60 Demo-Teile gleichmäßig verteilt zwischen 390 fehlerfreien
  Füllteilen. Jedes Füllteil ist ein eigener Datensatz (Seed `FILLER_SEED`, eigene
  Seriennummer, Linie, Charge), wird echt durch den Mock geprüft und gespeichert; das Bild
  ist ein wiederverwendetes fehlerfreies Demo-Bild. Bei Seed 42 landen 18 Demo-Teile in
  „Zu prüfen“, also 4 % des Stroms. Reihenfolge deterministisch: Die Pflichtteile des Mocks
  liegen auf Demo-Plätzen innerhalb der ersten `DEMO_PREINSPECTED` Positionen, die übrigen
  Demo-Teile in fester Zufallsreihenfolge.
- Bildanzeige: Bild mit SVG-Ebene. Boxen nach Schweregrad gefärbt, Spiegelungen grau
  gestrichelt mit dem Hinweis „kein Fehler“. Markierungen ein- und ausblendbar.
- Ergebnisbereich: großes Urteil (OK / NOK / Manuelle Prüfung), Begründung in einem Satz,
  Liste der Erkennungen mit Typ, Schweregrad, Konfidenzbalken (Grenze 0,7 markiert), Länge in mm.
- Aktionen: „Bestätigen“ (Enter), „Korrigieren“ (K).
- Korrekturdialog: Endurteil OK/NOK, je Erkennung „falsch erkannt“ oder „anderer Typ“,
  „Fehler übersehen“ mit Typauswahl, Kommentar. Kein Zeichnen neuer Boxen.

`/station/history` – Tabelle (Zeit, Seriennummer, Linie, Charge, KI-Urteil, Endurteil, Status),
neueste zuerst, seitenweise (`HISTORY_PAGE_SIZE`). Filter: Umfang (Standard „Nur auffällige“,
damit die Füllteile nicht alles überdecken; oder „Alle“), Status, Endurteil (OK / NOK / offen)
und „nur korrigierte“. Korrigierte Prüfungen sind hervorgehoben.

`/station/inspection/{id}` – Detailansicht, nur lesend: Bild mit Markierungen, KI-Ergebnis und
Review (Entscheidung, Endurteil, Korrekturen, Kommentar) nebeneinander.
**Keine nachträgliche Korrektur von OK-Teilen.**

### Testsitzung zurücksetzen (`tools/reset_demo.py`)
Löscht nur Prüfungen und Reviews der Demo- und Füllteile (Vergangenheitsdaten bleiben), legt die
Füllteile neu an und lässt die ersten `DEMO_PREINSPECTED` (150) Teile des Stroms sofort durch den
Mock prüfen, Zeitstempel über die letzte Stunde verteilt. Alle Pflichtteile des Mocks liegen
darunter, Reviews gibt es danach keine. Deterministisch, Laufzeit deutlich unter 10 s.
Tageswechsel: Reichen die Vergangenheitsdaten nicht genau bis gestern (oder fehlen sie), führt
`reset_demo` vorher automatisch `seed_history` aus und gibt das aus.

### Vergangenheitsdaten (`tools/seed_history.py`)
30 Tage vor heute (heute selbst bleibt der Station), 3 Linien, Menge pro Tag und Linie aus
`HISTORY_PARTS_PER_DAY` (180 / 150 / 120 = 450 pro Tag, so viel wie ein kompletter Stationslauf).
Seriennummern mit fünfstelligem Index (`HISTORY_SERIAL_TEMPLATE`), da 13.500 Teile. Eigener Bildvorrat (`HISTORY_POOL_SEED`, `data/history_pool/`),
getrennt von den Demo-Teilen. Je Linie sind genau `HISTORY_DEFECT_RATE` (L1 3 %, L2 6 %,
L3 4 %) der Teile echt fehlerhaft. Jedes Teil wird vom Mock geprüft (natürliche Fehler, keine
Pflichtfälle). NOK- und MANUAL-Teile erhalten eine plausible Review: Endurteil nach Ground
Truth; absichtliche Mock-Fehler werden korrigiert (Spiegelung → „anderer Typ: Spiegeleffekt“,
verwechselter Typ → richtiger Typ, übersehene Fehler → „Fehler übersehen“). Ein erneuter Lauf
ersetzt die Vergangenheitsdaten.

### Dashboard (Martin)

`/dashboard` – Filter stehen als Parameter in der URL (`period`, `date_from`, `date_to`,
`line`, `by_line`). Alle Zahlen kommen aus `app/repository.py` und nutzen die Statusfunktion.
- Filterleiste: Zeitraum (Heute / 7 Tage / 30 Tage / frei wählbar; Zeiträume enthalten heute,
  „7 Tage“ = heute und die 6 Tage davor), Linie (Alle / L1 / L2 / L3), Schalter „Fehlerquote je Linie“.
- Kennzahlen-Kacheln (Zeitraum nach Prüfdatum, Linie nach Charge):
  - Geprüfte Teile: Prüfungen im Zeitraum.
  - Prüfabdeckung: geprüfte / produzierte Teile (Produktionsdatum der Charge im Zeitraum). Als
    „produziert“ zählen nur Teile, die an der Station eingelaufen sind: Teile des Stroms erst ab
    ihrem Einlauf, nicht alle beim Reset angelegten.
  - Fehlerhafte Teile: Endurteil NOK; für offene Prüfungen vorläufig das KI-Urteil.
  - Fehlerquote = Endurteil NOK / Prüfungen mit Urteil. Für offene Prüfungen zählt vorläufig das
    KI-Urteil (`final_verdict()`); offene MANUAL-Fälle haben kein Urteil und zählen weder als NOK
    noch im Nenner. Solange offene Prüfungen im Zeitraum liegen, ist der Wert „vorläufig“
    (Kachel mit Kennzeichen, im Diagramm gestrichelte Linie zum hohlen Punkt). Ohne offene
    Prüfungen entspricht das der Definition NOK / abgeschlossene Prüfungen.
  - Offene manuelle Prüfungen: alle Prüfungen mit Status PENDING_REVIEW (KI-NOK und unsicher).
  - KI-Korrekturquote = Reviews mit geändertem Urteil (KI NOK, Endurteil OK) / überprüfte
    Prüfungen mit KI-Urteil NOK. Typkorrekturen bei gleichbleibendem NOK zählen nicht. MANUAL-Fälle
    zählen nicht, weil die KI dort kein Urteil abgegeben hat.
- Diagramm „Fehlerquote pro Tag“ (Linie, gleiche Definition wie die Kachel, vorläufige Tage
  gestrichelt), bei „Alle Linien“ optional je Linie.
- Diagramm „Fehlertypen“ (horizontale Balken): Teile je Fehlertyp nach den Korrekturen der Prüfer
  (`review_change`: falsch erkannt entfernt, anderer Typ ersetzt, übersehen ergänzt),
  abgeschlossene Prüfungen. Spiegelungen getrennt als „gefiltert, kein Fehler“.
- Chart.js 4.4.6 lokal in `app/static/vendor/` (Lizenz daneben), keine externe URL.
- Je Diagramm eine Tabellenansicht („Daten als Tabelle“).
- **Kein Diagramm „Linienvergleich“**, der Linienfilter reicht.

## 4. Testbild-Generator und Mock-KI

### Generator (`tools/generate_images.py`, Pillow, fester Seed)
- 800×600 PNG: dunkles Förderband, darauf eine Glasplatte (300×200 mm) mit leicht blaugrünem
  Verlauf, dunklerer Kante und feinem Rauschen.
- Fehler werden als Geometrie gezeichnet, die Box ergibt sich daraus:
  - RISS: verzweigter Zufallspfad, dünn, hell und dunkel im Wechsel.
  - KRATZER: helle, fast gerade Linie, entweder kurz oder lang (Längenbereiche in config.py).
  - DRUCKSTELLE: weichgezeichnete dunkle Ellipse mit hellem Ring.
  - BLAESCHEN: 1–8 kleine Kreise mit Glanzpunkt, je Blase ein Label.
  - SPIEGELUNG: breiter, halbtransparenter weißer Diagonalstreifen.
- Verteilung in config.py: etwa 55 % fehlerfrei, 15 % nur Spiegelung, Rest 1–3 Fehler.
  Die Linien haben unterschiedliche Profile (z. B. L2 mit mehr Kratzern).
- Pflichtfälle: Ab 8 Bildern legt der Generator je einen Pflichtfall an zufälliger Stelle an:
  fehlerfrei, nur Spiegelung, Riss, kurzer Kratzer, langer Kratzer, Druckstelle, wenige
  Bläschen (< `BUBBLE_NOK_COUNT`), viele Bläschen (≥ `BUBBLE_NOK_COUNT`). Damit sind alle
  Fehlertypen und beide Seiten der Kratzer- und Bläschenregel in jedem Lauf abgedeckt,
  unabhängig vom Seed. Liste in `GENERATOR_REQUIRED_SCENARIOS` (config.py).
- Seed in der Seriennummer: Seed 42 erzeugt `GT-0420001`, `GT-0420002` … (Seed dreistellig,
  Index vierstellig), Chargen entsprechend `CH-042-L1-01`. Ein erneuter Lauf mit gleichem Seed
  ersetzt genau diese Teile, Chargen, Bilder und Labels; Daten anderer Seeds bleiben erhalten.
  Seed daher 0–999, Anzahl 1–9999.
- Ausgabe: `data/images/{serial}.png` und `data/labels/{serial}.json`, Import in `gt_defect`.
- **Keine Übersichtsseite für den Generator.**

### Mock-KI (`app/detector.py`)
- Schnittstelle `Detector.analyze(part) -> list[Detection]`, einzige Umsetzung `MockDetector`.
- Deterministisch: Zufall hängt an Seriennummer und Seed.
- Konfidenz grundsätzlich 0,80–0,99, bei kleinen oder blassen Fehlern niedriger. Box ±3 px Versatz.
- Absichtliche Fehler (Raten in config.py, vermerkt in `injected_error`): Spiegelung als
  Kratzer (≈5 %), Fehler übersehen (≈3 %), Typ verwechselt Kratzer ↔ Riss (≈3 %),
  Konfidenz unter 0,7 gedrückt (≈8 %).
- Pflichtfälle des Mocks (`MOCK_REQUIRED_SCENARIOS` in config.py): Kennt der Mock die Liste
  aller Teile, tritt jeder Pflichtfall genau einmal auf, und zwar an einem Teil, dessen Urteil
  sich dadurch tatsächlich ändert. Die Raten bleiben unverändert.
  - Spiegelung als Kratzer gemeldet: OK-Teil wird NOK.
  - Kurzer Kratzer als Riss gemeldet (Typ verwechselt): OK-Teil wird NOK.
  - Konfidenz gedrückt bei einem echten Fehler: NOK-Teil wird MANUAL.
  - Konfidenz gedrückt bei einem harmlosen Befund: OK-Teil wird MANUAL.
  - Kein Pflichtfall für „Fehler übersehen“.
  Auswahl deterministisch über Seed und Seriennummer. Teile, bei denen der Fall schon zufällig
  eintritt, werden bevorzugt; sonst wird der Fehler gezielt an einem passenden Befund gesetzt.
  Jeder Pflichtfall ist in `injected_error` vermerkt. Zufällige Fehler können bei manchen Seeds
  zusätzlich weitere Urteile ändern (auch dann vermerkt); bei Seed 42 weichen genau die vier
  Pflichtteile ab.

### Regeln (`app/rules.py`), reine Funktion `evaluate(detections) -> (verdict, reason)`
- RISS → CRITICAL → NOK.
- DRUCKSTELLE → MAJOR → NOK.
- KRATZER: Länge ≥ `SCRATCH_MAX_MM` → MAJOR → NOK, sonst MINOR.
- BLAESCHEN: ab `BUBBLE_NOK_COUNT` = 3 Stück → NOK.
- SPIEGELUNG → NONE, wird nie als Fehler gewertet.
- Erkennung mit Konfidenz < `CONFIDENCE_MIN` (0,7) gilt als unsicher.
- **Vorrang:** Führt mindestens eine *sichere* Erkennung (Konfidenz ≥ 0,7) zu NOK, ist das Teil
  NOK. Nur wenn nichts sicher zu NOK führt und mindestens eine Erkennung unsicher ist → MANUAL.
- **Eine unsichere Spiegelung allein führt zu MANUAL.**

### Maßstab und Grenzwerte
- `MM_PER_PX` wird aus der gezeichneten Plattenbreite abgeleitet (Platte = 300 mm), nicht frei
  gesetzt: `MM_PER_PX = PLATE_WIDTH_MM / PLATE_WIDTH_PX`.
- `SCRATCH_MAX_MM` liegt zwischen dem längsten kurzen und dem kürzesten langen Kratzer des
  Generators. Ein Test prüft das.
- `BUBBLE_NOK_COUNT = 3`.

## 5. Umsetzung in 6 Schritten

| # | Schritt | Akzeptanzkriterium |
|---|---|---|
| 1 | Grundgerüst: config.py, schema.sql, db.py, main.py mit Startseite, requirements.txt, pytest | `pytest` grün (Tabellen angelegt, config ladbar). Nach `uvicorn app.main:app` zeigt http://127.0.0.1:8000 eine deutsche Startseite mit Links zu Prüfstation und Dashboard |
| 2 | Testbild-Generator | `python -m tools.generate_images --count 60 --seed 42` erzeugt 60 PNG und 60 JSON. Tests: gleicher Seed → gleiche Labels, alle Boxen im Bild, alle Fehlertypen kommen vor |
| 3 | Regeln und Mock-KI | Tabellengetriebene Regeltests inkl. Grenzfälle (0,69 / 0,70, Kratzer am Grenzwert, Blasenzahl, nur Spiegelung → OK, Vorrang NOK vor MANUAL). Mock-Fehlerraten über 1.000 Teile ±2 Prozentpunkte, Läufe reproduzierbar |
| 4 | Prüfstation: Einlauf, Anzeige, Bestätigen/Korrigieren, Speichern | TestClient: Review gespeichert, `inspection`/`detection` unverändert. Browser: 10 Teile laufen ein, nur NOK/MANUAL zu bearbeiten, Korrektur in der DB sichtbar |
| 5 | Historie, Vergangenheitsdaten, Reset | `seed_history.py` erzeugt 30 Tage für 3 Linien, `reset_demo.py` stellt den Startzustand in < 10 s her. Historie filterbar, Detailansicht zeigt KI und Korrektur |
| 6 | Dashboard (inkl. Chart.js lokal) | Kennzahlen stimmen bei festem Seed mit unabhängiger SQL-Zählung überein. Filter ändern Kacheln und Diagramme, Chart.js lädt ohne Internetverbindung |

## 6. Entscheidungen und Annahmen

### Entscheidungen
1. Abhängigkeiten: fastapi, uvicorn, jinja2, pillow, pytest, httpx. Keine weiteren.
   Formulare senden per JSON-Fetch (kein python-multipart).
2. Chart.js wird mit fester Version nach `app/static/vendor/` heruntergeladen, die Lizenzdatei
   liegt daneben.
3. Ein sicheres NOK hat Vorrang vor MANUAL.
4. Eine unsichere Spiegelung allein führt zu MANUAL.
5. `MM_PER_PX` wird aus der Plattenbreite abgeleitet, `SCRATCH_MAX_MM` liegt zwischen den
   Kratzer-Längenbereichen (per Test geprüft), `BUBBLE_NOK_COUNT = 3`.

### Kürzungen
- Kein Diagramm „Linienvergleich“.
- Keine nachträgliche Korrektur von OK-Teilen in der Detailansicht.
- Keine Übersichtsseite für den Generator.
- Kein `duration_ms` in `review`.

### Annahmen
- Thomas sieht OK-Teile nicht (nur im Zähler „Automatisch freigegeben“ und in der Historie)
  und muss sie nicht bestätigen.
- Fehlerquote = endgültige NOK / abgeschlossene Prüfungen. Offene MANUAL-Fälle zählen nicht mit
  und erscheinen als eigene Kachel.
- Kein Personenfeld, nur „Prüfplatz 1“.
- Nur ein Browser gleichzeitig, keine Behandlung von Gleichzeitigkeit.
- Vergangenheitsdaten nutzen einen Pool von etwa 200 Bildern. Zeitangaben relativ zum aktuellen
  Datum, sodass „Heute“ immer Daten zeigt.
- Ein fiktiver Bauteiltyp: „Glasplatte 300×200 mm“.
- `config.py` liegt in `app/config.py`.
