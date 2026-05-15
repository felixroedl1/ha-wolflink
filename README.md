# Wolf Smartset HACS

Home Assistant Custom Integration fuer WOLF SmartSet.

Version: `0.1.0.0`

## Hinweis zum Ursprung

Diese Integration basiert auf der offiziellen Home-Assistant-Integration `homeassistant/components/wolflink` und wurde fuer HACS sowie zusaetzliche Einstellmoeglichkeiten erweitert und angepasst.

## Features

- Config Flow in Home Assistant UI (Benutzername + Passwort, danach Geraeteauswahl)
- Automatisches Anlegen von Sensoren fuer verfuegbare Parameter
- Polling-Intervall: 60 Sekunden
- Schreibzugriff auf relevante WOLF-Parameter ueber `number`, `select`, `switch`, `button`
- Robuster Write-Pfad mit Bundle-Fallback fuer SmartSet-API-Inkonsistenzen

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
- Heizung Zeitprogramm
- Warmwasser Programmwahl
- Warmwasser Zeitprogramm

### Switch

- Partymodus
- Urlaubsmodus

### Button

- 1x Warmwasser

## Fachmann-Ebene

Die Fachmann-Ebene ist aktuell nicht vollstaendig als eigener Modus in der Integration umgesetzt. Verfuegbarkeit und Schreibbarkeit haengen vom gelieferten Parameterumfang der SmartSet-API und der Anlage/Firmware ab.

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
