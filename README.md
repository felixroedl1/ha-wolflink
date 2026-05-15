# Wolf Smartset HACS

Home Assistant Custom Integration fuer WOLF SmartSet.

Die Integration verbindet dein WOLF SmartSet Konto mit Home Assistant, legt Sensoren fuer verfuegbare Parameter an und bietet schreibbare Warmwasser-Solltemperatur-Entitaeten als `number`.

## Features

- Config Flow in Home Assistant UI (Benutzername + Passwort, danach Geraeteauswahl)
- Automatisches Anlegen von Sensoren fuer verfuegbare Parameter
- Zustands-Mapping fuer List-Parameter (z. B. `Auto`, `Standby`, `Stoerung`)
- Schreibbare Warmwasser-Solltemperatur als Number-Entitaet
- Polling-Intervall: 60 Sekunden

## Voraussetzungen

- Home Assistant mit Unterstuetzung fuer Custom Integrations
- WOLF SmartSet Konto
- Internetzugriff auf die WOLF Cloud

Abhaengigkeit der Integration:

- `wolf-comm==0.0.48`

## Installation mit HACS

1. HACS -> Integrations -> Drei-Punkte-Menue -> Custom repositories.
2. Dieses Repository als `Integration` hinzufuegen.
3. `Wolf Smartset HACS` in HACS installieren.
4. Home Assistant neu starten.
5. Integration in Home Assistant hinzufuegen:
   - Einstellungen -> Geraete & Dienste -> Integration hinzufuegen -> `Wolf Smartset HACS`

## Manuelle Installation

1. Ordner `custom_components/wolflink` nach `<config>/custom_components/wolflink` kopieren.
2. Home Assistant neu starten.
3. Integration ueber UI hinzufuegen (Einstellungen -> Geraete & Dienste).

## Konfiguration

Beim Einrichten werden zwei Schritte durchlaufen:

1. SmartSet Zugangsdaten eingeben (`username`, `password`)
2. Ein Geraet aus der vom Konto gefundenen Geraeteliste auswaehlen

Hinweis: Pro WOLF Geraet wird eine eindeutige Config Entry angelegt.

## Entitaeten

### Sensoren

Fuer die vom API gemeldeten Parameter werden Sensoren erstellt. Unterstuetzte Typen sind unter anderem:

- Temperatur (`degC`)
- Druck (`bar`)
- Energie (`kWh`)
- Leistung (`kW`)
- Prozent (`%`)
- Laufzeit (`h`)
- Volumenstrom (`L/min`)
- Frequenz (`Hz`)
- Drehzahl (`rpm`)
- Allgemeine Zustands-/Textwerte

### Number (schreibbar)

Schreibbare Number-Entitaeten werden nur fuer Warmwasser-Solltemperatur erstellt, wenn der Parameter:

- ein Temperatur-Parameter ist
- nicht read-only ist
- in Parent/Name die Begriffe `warmwasser` und `solltemperatur` enthaelt

Wertebereich:

- Minimum: `20`
- Maximum: `75`
- Schrittweite: `1`

## Bekannte Einschraenkungen

- Es werden nur vom WOLF API verfuegbare Parameter angezeigt.
- Der Parameter `Reglertyp` wird absichtlich ausgefiltert.
- Bei Schreibfehlern (API/Netzwerk/Auth) wird der Write-Vorgang in Home Assistant mit Fehler beendet.

## Fehlerbehebung

- `cannot_connect`: Verbindung zur WOLF API fehlgeschlagen (Netzwerk/API pruefen)
- `invalid_auth`: Zugangsdaten ungueltig
- `no_devices`: Im Konto wurden keine kompatiblen Geraete gefunden

Fuer detaillierte Diagnose in Home Assistant Logs:

- Logger: `wolf_comm`

## Support / Issues

- Issue Tracker: <https://github.com/felixroedl1/ha-wolflink/issues>
