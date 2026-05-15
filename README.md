# Wolf Smartset HACS

Home Assistant Custom Integration fuer WOLF SmartSet.

Version: `0.2.0.0`

## Hinweis zum Ursprung

Diese Integration basiert auf der offiziellen Home-Assistant-Integration `homeassistant/components/wolflink` und wurde fuer HACS sowie zusaetzliche Einstellmoeglichkeiten erweitert und angepasst.

## Features

- Config Flow in Home Assistant UI (Benutzername + Passwort + optional Fachmann-Modus, danach Geraeteauswahl)
- Options Flow zur nachtraeglichen Aenderung von Fachmann-Modus und PIN
- Automatisches Anlegen von Sensoren fuer verfuegbare Parameter
- Polling-Intervall: 60 Sekunden
- Schreibzugriff auf relevante WOLF-Parameter ueber `number`, `select`, `switch`, `button`
- Robuster Write-Pfad mit Bundle-Fallback fuer SmartSet-API-Inkonsistenzen
- Einheitliches Entitaets-Prefix pro Geraet (`wolflink_<geraet>_...`)

## Voraussetzungen

- Home Assistant mit Unterstuetzung fuer Custom Integrations
- WOLF SmartSet Konto
- Internetzugriff auf die WOLF Cloud
- Abhaengigkeit: `wolf-comm==0.0.48`

## Installation mit HACS

1. HACS -> Integrations -> Drei-Punkte-Menue -> Custom repositories.
2. Dieses Repository als `Integration` hinzufuegen.
3. `Wolf Smartset HACS` in HACS installieren.
4. Home Assistant neu starten.
5. Integration in Home Assistant hinzufuegen unter Einstellungen -> Geraete & Dienste.

## Manuelle Installation

1. Ordner `custom_components/wolflink` nach `<config>/custom_components/wolflink` kopieren.
2. Home Assistant neu starten.
3. Integration ueber UI hinzufuegen.

## Entitaeten und Einstellmoeglichkeiten

### Sensoren

Es werden Sensoren fuer verfuegbare Parameter erzeugt, unter anderem fuer Temperatur, Druck, Energie, Leistung, Prozent, Laufzeit, Volumenstrom, Frequenz, Drehzahl und Statuswerte.

### Number

- Warmwasser Solltemperatur
- Heizung Sollwertkorrektur

### Select

- Heizung Programmwahl
- Warmwasser Programmwahl

### Switch

- Partymodus
- Urlaubsmodus

### Button

- 1x Warmwasser

## Fachmann-Ebene

Der Fachmann-Modus kann beim Setup aktiviert oder spaeter unter den Integrations-Optionen ein-/ausgeschaltet werden.

- Option: `Fachmann-Modus (AN/AUS)`
- Bei `AN` folgt die PIN-Eingabe
- Standard PIN: `1111` (vorbelegt)

Ist der Modus aktiv, werden die erweiterten Parameter ueber die SmartSet-API geladen. Verfuegbarkeit und Schreibbarkeit haengen weiterhin vom gelieferten Parameterumfang der SmartSet-API und der Anlage/Firmware ab.

## Version 0.2.0.0

- Version auf `0.2.0.0` angehoben.
- Doppelte/generische `select`-Entitaeten fuer Programmwahl bereinigt.
- Zeitprogramm-Selects (Heizung/Warmwasser) entfernt, um inaktive bzw. nicht zuverlaessig schaltbare Entitaeten zu vermeiden.
- Fachmann-Modus inkl. PIN-Eingabe im Setup und in den Optionen verfuegbar.
- Entitaetsnamen und Prefix-Verhalten vereinheitlicht.

## Bekannte Einschraenkungen

- Es werden nur vom WOLF API verfuegbare Parameter angezeigt.
- Der Parameter `Reglertyp` wird absichtlich ausgefiltert.
- Parameternamen und Struktur koennen je nach Anlagenkonfiguration/Firmware abweichen.

## Fehlerbehebung

- `cannot_connect`: Verbindung zur WOLF API fehlgeschlagen.
- `invalid_auth`: Zugangsdaten ungueltig oder Login auf Portal-Seite abgelehnt.
- `no_devices`: Im Konto wurden keine kompatiblen Geraete gefunden.

Fuer detaillierte Diagnose:

- Logger: `wolf_comm`
- Logger: `custom_components.wolflink`

## Support / Issues

- Issue Tracker: <https://github.com/felixroedl1/ha-wolflink/issues>
