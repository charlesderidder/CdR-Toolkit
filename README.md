# CdR-Toolkit

Windows-desktopapp voor één-klik-systeemonderhoud: app-updates (Winget),
Windows Updates, drivers/firmware en opschonen — zonder handmatige bevestigingen.

## Functies

De app heeft een zijbalk met drie secties; elke pagina heeft één duidelijke actieknop.

### Onderhoud

| Pagina | Wat gebeurt er |
|---|---|
| **Update alles** | Winget-updates → Windows Updates → driver/firmware-updates |
| **Alleen apps** | `winget upgrade --all --silent --accept-source-agreements --accept-package-agreements` |
| **Apps** | Bijwerken (winget upgrade --all) én installeren: top-25-lijst + zoeken/installeren op winget-ID |
| **Alleen Windows** | Windows Update forceren (zoeken, downloaden, installeren) |
| **Drivers** | OEM-tool indien aanwezig (Dell Command Update, HP Image Assistant), anders drivers via Windows Update |
| **Cleanup** | Temp-mappen (user + system), Windows Update-cache, prullenbak; toont vrijgemaakte ruimte |

### Netwerk (IP-tools)

Bewust gescheiden van de onderhoudstools: dit zijn handmatige hulpmiddelen voor
bij netwerkproblemen — ze draaien nooit automatisch mee.

| Pagina | Wat gebeurt er |
|---|---|
| **Netwerk (IP-tools)** | Eén tabblad met: netwerkinfo (`ipconfig /all` + extern IP), DNS-cache opschonen, netwerkdiagnose (ping gateway + 8.8.8.8 + DNS-test) en netwerk reset (release/renew, Winsock- en TCP/IP-reset) |

### Remote desktop (ingebouwde webserver)

Neem deze computer over vanaf een telefoon, tablet of andere pc — **volledig
ingebouwd, geen RustDesk, RDP of andere software nodig**. De app start een
webserver (poort 8765) die het scherm als MJPEG-stream toont en
muis/toetsenbord-invoer terugstuurt naar Windows.

- **Start webserver**: toont LAN-adres + sessie-wachtwoord; de firewall-regel
  wordt automatisch gezet (en bij stoppen weer verwijderd).
- **Ook via internet**: probeert via UPnP een routerpoort te openen en toont
  het internet-adres (lukt dat niet, dan blijft het bij LAN).
- **Webapp**: moderne interface met bovenbalk — schermkeuze ("Alle schermen"
  of één beeldscherm, wisselbaar tijdens de sessie), volledig-schermknop,
  vernieuw-knop en een toetsenbordknop die ook op mobiel werkt.
- **Tablet-geoptimaliseerd**: tik = linksklik, sleep = cursor verplaatsen,
  dubbeltik = dubbelklik, lange druk of tik met twee vingers = rechtsklik,
  sleep met twee vingers = scrollen.
- **Vast wachtwoord**: optioneel een eigen vast wachtwoord instellen
  (opgeslagen in `config.json`); leeg = willekeurig wachtwoord per sessie.
- **File Transfer**: tab "📁 Bestanden" in de webapp — twee panelen: extern
  (volledige verkenner van de host: bladeren, downloaden, map aanmaken,
  hernoemen, verwijderen) en lokaal (bestanden kiezen en uploaden naar de
  huidige externe map).
- **Logboek**: tekst is selecteerbaar; kopiëren via Ctrl+C of het
  rechtermuisknopmenu (Kopiëer / Alles selecteren / Logboek legen).
- Let op: HTTP zonder TLS — bedoeld voor vertrouwde netwerken. Het UAC-scherm
  (secure desktop) is op afstand niet zichtbaar/bedienbaar.

### Media

| Pagina | Wat gebeurt er |
|---|---|
| **Ziggo TV** | Opent ziggogo.tv in een eigen WebView2-venster (apart proces). In het venster rechtsboven een knoppenbalk: Live TV, TV Gids, Zap ◀/▶, 📌 vastzetten op de voorgrond en ✕ sluiten. Login blijft bewaard; opnemen via Ziggo's eigen cloud-opname in de speler (DRM-streams zijn niet lokaal op te nemen). |

### Configuratie

De instellingenpagina open je via het **tandwiel rechtsboven**: auto-reboot
toggle, geplande taken, log-export, herstelacties en de updatecontrole.

### Gegevens

Alle gegevens staan centraal in **`Documenten\CharlesOnderhoud`**:
`config.json` (o.a. het vaste remote-wachtwoord) en `logs\` (onderhoudslogs en
`updater.log` van de zelf-update). Bestaande config naast de exe wordt
eenmalig gemigreerd.

Verder:

- **Automatische herstelacties**: bij een winget-fout worden de bronnen automatisch
  gereset (`winget source reset --force`) en wordt opnieuw geprobeerd. Losse knoppen
  voor *Herstel Winget* en *Herstel Windows Update* (services + cache reset).
- **Status per taak**: Wachten / Bezig... / Geslaagd / Mislukt (gekleurd).
- **Logvenster** onderin + wegschrijven naar `Documenten\CharlesOnderhoud\logs`.
- **Exporteer log** naar een zelfgekozen locatie.
- **Reboot-detectie**: waarschuwing in de kopbalk als een herstart nodig is.
- **Toggle** "Automatisch herstarten indien nodig" (uit = alleen melden).
- **Plan automatisch**: maakt een geplande taak (dagelijks of wekelijks, 09:00)
  die de app met `--auto` start: alle taken draaien en het venster sluit vanzelf.
- **Admin-check** bij het starten met UAC-herstartaanbod.
- **Zelf-update**: controleert bij opstarten stilletjes op een nieuwe versie op
  `charlesderidder.nl/downloads/` en kan zichzelf downloaden, verifiëren
  (SHA256) en vervangen via de knop **Zoek naar update**.

## Eisen

- Windows 10/11
- Voor uitvoeren vanuit broncode: Python 3.10+ en `pip install -r requirements.txt`
  (Pillow wordt gebruikt voor schermcapture in de remote-webserver)
- Winget ("App Installer" uit de Microsoft Store) voor app-updates

## Gebruik

### Direct de exe

`CdR-Toolkit.exe` starten — de UAC-prompt verschijnt automatisch
(de exe is gebouwd met `--uac-admin`). Logs komen in
`Documenten\CharlesOnderhoud\logs`.

### Vanuit broncode draaien

```bat
python main.py
```

De app biedt aan zichzelf als administrator te herstarten als dat nodig is.

### Zelf de exe bouwen (of herbouwen na aanpassingen)

```bat
build_exe.bat
```

Resultaat: `dist\CdR-Toolkit.exe` (één bestand, geen consolevenster,
vraagt zelf om admin-rechten).

## Structuur (modulair — makkelijk uit te breiden)

```
main.py                 UI + taakorchestratie (pipelines, statussen, knoppen)
core/
  admin.py              admin-check, UAC-herstart, reboot-detectie
  logger.py             logging naar bestand + UI (thread-safe via queue)
  runner.py             subprocess/PowerShell uitvoeren met live output
  updater.py            zelf-update via charlesderidder.nl (version.txt + SHA256)
  settings.py           persistente instellingen (Documenten\CharlesOnderhoud)
tasks/
  winget_task.py        winget-updates met retry + auto-herstel
  windows_update.py     Windows Update via COM-API (geen extra module nodig)
  drivers.py            OEM-detectie + drivers via Windows Update
  cleanup.py            temp/cache/prullenbak + vrijgemaakte ruimte
  repair.py             winget source reset, Windows Update reset
  network.py            IP-tools: ipconfig, DNS flush, diagnose, netwerk-reset
  remote.py             remote-webserver: start/stop, adressen, wachtwoord
version.py              versienummer van de app
core/
  remoteserver.py       ingebouwde webserver: MJPEG-stream + invoerinjectie
  upnp.py               UPnP poort-forwarding (internet-toegang)
publiceer.bat           maakt dist\version.txt voor publicatie
build_exe.bat           PyInstaller-build
logs/                   automatisch aangemaakte logbestanden
```

### Nieuwe versie publiceren (zelf-update)

1. Verhoog het versienummer in `version.py` (bijv. `1.0.1`).
2. Bouw de exe: `build_exe.bat`.
3. Maak het versiebestand: `publiceer.bat` — dit genereert `dist\version.txt`
   met het versienummer en de SHA256-checksum van de exe.
4. Upload **beide** bestanden naar `https://charlesderidder.nl/toolkit/`:
  - `CdR-Toolkit.exe`
   - `version.txt`

Elke installatie ziet dan bij de volgende start (of via de knop) dat er een
nieuwe versie is en kan zichzelf updaten. Zonder `version.txt` op de server
gebeurt er gewoon niets — de app blijft werken.

### Nieuwe taak toevoegen

1. Maak `tasks/mijn_taak.py` met `def run(log) -> bool:` — `log(bericht, niveau)`
   schrijft naar UI én bestand (`INFO`, `STEP`, `SUCCESS`, `WARNING`, `ERROR`).
2. Voeg in `main.py` een regel toe aan `TAKEN` (statuspaneel), aan `uitvoerders`
   in `_pipeline()` en eventueel een knop in `_build_ui()`.
3. Herbouw de exe met `build_exe.bat`.

## Opmerkingen

- Windows Update gebruikt bewust de **COM-API** (`Microsoft.Update.Session`) en niet
  de PSWindowsUpdate-module: die module is niet standaard geïnstalleerd.
- Alles draait *silent*: geen prompts van winget, installers of Windows Update.
  Installers die tóch iets vragen vallen onder hun eigen silent-gedrag.
- De geplande taak draait zichtbaar (met venster) onder de ingelogde gebruiker;
  tijdstip/dag aanpassen kan via `taskschd.msc`.
- Bij zelf-update: de helper draait als eenmalige geplande taak (ongelevigd
  bij netwerkshares/gebruikersmappen, verhoogd bij beschermde mappen) en logt
  elke stap in `Documenten\CharlesOnderhoud\logs\updater.log`. Start de app
  na ~30 seconden niet vanzelf? Open dan zelf het bestand opnieuw.

## Release Notes

### v2.4.1 (2026-08-10)
- Onderhoudsrelease met kleine verbeteringen en stabiliteitsfixes.

### v2.4.0 (2026-08-10)
- **Remote Desktop framerate boost**: 30 fps (was 5 fps) — snellere weergave en responsiviteit.

### v2.3.0
- Verwijderde onnodig `pip install` stap uit build script (requirements.txt bestaat niet).
- Verwijderde popup-melding bij startup updatecheck; blauwe downloadteken blijft zichtbaar.
- Verwijderde "Rasterweergave instellen" uit menubalk (onderdeel van camera-instellingen).

### v2.2.2
- Executable hernoemd naar `CdR-Toolkit.exe` (was `CdRToolkit.exe`).
- Update URL aangepast naar `https://charlesderidder.nl/toolkit/CdR-Toolkit.exe`.
- Bugfix: veilige herstart bij thema-wissel (geen meer _tcl_data-fout).
- Nieuwe aparte "Camera-instellingen" popup (alleen cameras + Nest + raster); menu's gericht naar deze popup.
- Startup-updatecheck nu met blauwe download-indicator en beschikbaarheidsmelding.

### v2.2.1
- **Volledig UI-redesign**: nieuwe centrale menubalk (Bestand/Bewerken/Weergave/Instellingen/Help).
- Instellingen nu direct bereikbaar via logische submenu's (checkboxes, invoervelden, radio's).
- Verwijderde aparte instellingenknop (tandwiel) — alles via menubalk.
- Thema-ondersteuning: Standaard en Hoog contrast met persistente opslag.
- Help-dialogs: Over, Versie-info, Handleiding, Nieuw in deze versie.
- Snelle menu-acties voor Office ICS-link en remote-wachtwoord.

### v2.2.0
- Initiële release: volledige onderhoudstoolkit met menubalk-UI en camera-dashboard.

