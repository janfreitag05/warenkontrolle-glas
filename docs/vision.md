# Vision: KI-gestützte Qualitätskontrolle für Glas (Prototyp)

Zweck dieses Dokuments: Kontext aus der Design-Thinking-Phase für den Coding-Agenten.
Der Prototyp dient ausschließlich dem Nutzertest, nicht dem Produktivbetrieb.

## 1. Personas

### Thomas Becker (38) – Mitarbeiter Qualitätssicherung / Sichtprüfung
- Prüft seit Jahren Glasteile visuell auf Fehler.
- Will fehlerhafte Teile zuverlässig erkennen und hohe Qualität sichern.
- Empfindet die manuelle Sichtprüfung als monoton und ermüdend; Konzentration lässt bei langen Prüfphasen nach.
- Hoher Zeit- und Dokumentationsaufwand, andere Aufgaben bleiben liegen.
- Hoher Verantwortungsdruck: übersehene Fehler führen zu ausgelieferten Mängeln.
- Wunsch: zuverlässige Unterstützung, weniger Monotonie, Fokus auf auffällige und schwierige Fälle.

### Martin Schneider (52) – Geschäftsführer eines mittelständischen Produktionsunternehmens
- Verantwortlich für wirtschaftlichen und qualitativ zuverlässigen Produktionsprozess.
- Prüfung hängt von erfahrenen Mitarbeitern ab; Fluktuation führt zu Wissensverlust.
- Begrenzte Arbeitszeiten, hohe Personalkosten, Prüfung kaum skalierbar.
- Ergebnisse schwanken mit Erfahrung, Konzentration und Tagesform.
- Bisher werden nur Stichproben geprüft.
- Wunsch: automatisierte, skalierbare, konsistente Prüfung aller Teile, weniger Abhängigkeit von Fachpersonal.

## 2. Point of View
- **Thomas** braucht zuverlässige Unterstützung in der Qualitätskontrolle, weil er die aktuelle Prüfung als ermüdend und monoton empfindet, wodurch seine Konzentration beeinträchtigt wird.
- **Martin** braucht eine effiziente und skalierbare Qualitätskontrolle, weil die manuelle Prüfung hohe Personalkosten verursacht und stark von der Verfügbarkeit qualifizierter Mitarbeiter abhängt.

## 3. Problem Statements
- **Thomas:** Die Prüfarbeit ist monoton und ermüdend, was zu Konzentrationsabfällen führt. Die manuelle Prüfung beansprucht viel Zeit, andere Tätigkeiten bleiben liegen. Ziel: ihn entlasten, Konzentration erhalten, Kapazität schaffen.
- **Martin:** Die Qualitätskontrolle verursacht hohe Personalkosten, Mitarbeitende sind unmotiviert, die Produktivität sinkt. Ziel: Produktivität und Qualität der Kontrolle steigern, um wettbewerbsfähig zu bleiben.

## 4. How-Might-We-Fragen
- **HMW 1 (Thomas):** Wie können wir Thomas bei der Qualitätskontrolle unterstützen, damit er entlastet wird und sich stärker auf andere Aufgaben konzentrieren kann?
- **HMW 2 (Martin):** Wie können wir Martin unterstützen, die Mitarbeitenden effizienter einzusetzen, um Personalkosten zu sparen?
- **HMW 3 (Martin):** Wie können wir Martin ermöglichen, die Qualitätskontrolle weniger abhängig von qualifiziertem Fachpersonal zu machen, um auch zukünftig eine gute und konstante Prüfung zu gewährleisten?

## 5. Soll User Journeys

### Thomas
1. Startet seine Schicht; Glasteile laufen automatisch am Kamerasystem vorbei.
2. Das System nimmt jedes Teil auf und analysiert die Bilder in Echtzeit.
3. Die KI unterscheidet harmlose Spiegeleffekte von echten Defekten (Risse, Kratzer, Druckstellen, Bläschen) und meldet nur auffällige Teile.
4. Thomas prüft am Bildschirm nur die markierten Fälle und bestätigt oder korrigiert die Einstufung der KI.
5. Prüfergebnisse werden automatisch dokumentiert; manueller Dokumentationsaufwand entfällt.
6. Thomas muss nicht mehr stundenlang monoton prüfen und setzt seine Erfahrung gezielt bei schwierigen Fällen ein.

### Martin
1. Öffnet morgens das Dashboard der Qualitätskontrolle.
2. Sieht auf einen Blick, dass alle gefertigten Glasteile geprüft wurden, nicht nur Stichproben.
3. Das System zeigt Fehlerquote und Fehlertrends übersichtlich an.
4. Die Prüfung läuft mit konstantem, objektivem Maßstab, unabhängig von Arbeitszeiten und Tagesform.
5. Martin erkennt häufige Fehlertypen und kann Qualität nach Zeitraum und Produktionslinie beurteilen.

## 6. Festlegungen für den Prototyp

### Umfang (MVP)
**Enthalten**
- Simulierte Prüfstation für Thomas: Bilder aus einem lokalen Ordner ersetzen die Kamera, Teile "laufen" nacheinander ein.
- Pro Teil: KI-Ergebnis (OK / NOK / manuelle Prüfung), Fehler als Box auf dem Bild markiert, Fehlertyp, Schweregrad, Konfidenz.
- Thomas bestätigt oder korrigiert die Einstufung; Korrektur wird gespeichert.
- Automatische Dokumentation jeder Prüfung, Historie vergangener Prüfungen.
- Dashboard für Martin: Anzahl geprüfter / fehlerhafter Teile, Fehlerquote über Zeit, Verteilung der Fehlertypen, Filter nach Zeitraum und Produktionslinie.

**Nicht enthalten**
- Echte Kamera-, Beleuchtungs- oder Hardwaresteuerung.
- Echtes KI-Modell oder Training.
- Schnittstellen zu Produktionssystemen.
- Login, Rollenverwaltung, Backup, Mandanten, Skalierung.

### Fehlerkatalog
| Code | Fehlertyp | Bedeutung |
|---|---|---|
| RISS | Riss | echter Defekt |
| KRATZER | Kratzer | echter Defekt |
| DRUCKSTELLE | Druckstelle | echter Defekt |
| BLAESCHEN | Bläschen | echter Defekt |
| SPIEGELUNG | Spiegeleffekt | **kein** Defekt, darf nicht zu NOK führen |

### Bewertungsregeln (Annahmen des Teams, im Nutzertest zu prüfen)
- RISS: immer kritisch → NOK.
- DRUCKSTELLE: schwerwiegend → NOK.
- KRATZER: geringfügig, wenn kurz; schwerwiegend → NOK, wenn lang (Grenzwert als Konfiguration).
- BLAESCHEN: geringfügig einzeln; NOK ab einer konfigurierbaren Anzahl.
- SPIEGELUNG: wird angezeigt, aber nie als Fehler gewertet.
- Konfidenz einer Erkennung < 0,7 → Teil geht in "manuelle Prüfung" statt automatischer Einstufung.
- Alle Grenzwerte zentral in einer Konfigurationsdatei, nicht im Code verstreut.

### KI im Prototyp
- Ausschließlich Mock: Ergebnisse stammen aus den Labels der künstlich erzeugten Testbilder.
- Der Mock soll realistisch wirken: schwankende Konfidenzen und einige absichtlich falsche oder unsichere Ergebnisse, damit Thomas im Test etwas korrigieren kann.

### Testdaten
- Ausschließlich künstlich erzeugte Glasbilder (Glasfläche, eingezeichnete Fehler, Spiegelungen).
- Fiktive Bauteilnummern, Chargen und Produktionslinien. Keine echten Firmen- oder Personendaten.
