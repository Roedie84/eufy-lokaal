# Changelog — Eufy Lokaal

## v0.1.1 — de HomeBase lokaal zoeken op het goede adres

Eerste meting 11-10 13:00: ingelogd, 1 station (HomeBase T8030) en 3
apparaten gevonden, maar "niet lokaal gevonden". Oorzaak: `ip_addr` uit de
cloud is het publieke IP (86.88.x.x), dus de zoekvraag ging het internet op.
Nu: het LAN-adres uit stationparameter 1176 (CMD_GET_HUB_LAN_IP, alleen als
het een thuisnetadres is), de broadcast daarvan, de broadcastadressen van
Home Assistant zelf en 255.255.255.255 - nooit een publiek adres. De sensor
IP-adres toont het LAN-adres (het publieke staat als attribuut). De
gebruikte zoekadressen staan in de sensor "Eufy lokaal".

## v0.1.0 — stap 1: inventaris uit de cloud en de stations lokaal vinden

Gevraagd 11-10-2026: "Kun je ook de eufy zaken lokaal implementeren? Dit zou
een aparte integratie kunnen worden" - "Alles, ook al wordt de integratie
groot."

- **Cloud** (`cloud.py`): API-adres per land, inloggen met
  `v2/passport/login_sec` (wachtwoord AES-256-CBC met een ECDH-geheim,
  P-256), 2FA per e-mail en captcha, apparaat vertrouwen na een code,
  versleutelde station- en apparatenlijst. Token en sleutels bewaard in de
  config entry (zonder wachtwoord in de sessie), dus geen nieuwe login bij
  een herstart; bij een verlopen login vraagt HA om opnieuw in te loggen.
  Getoetst tegen Node.js-crypto (zelfde publieke sleutel, zelfde geheim,
  zelfde versleuteling).
- **Lokaal vinden** (`p2p.py`): elke minuut een LOCAL_LOOKUP (`f1 30`) naar
  het IP van elk station en het broadcastadres op UDP 32108; het antwoord
  (`f1 41`) bevat de p2p_did, die met de cloud wordt vergeleken. Verder
  stuurt deze versie het station niets.
- **Sensoren**: "Eufy lokaal" (samenvatting), per station Lokaal, Firmware
  en IP-adres, per apparaat Batterij en Wifi-signaal waar bekend.
- **Diagnose** zonder wachtwoord, token, sleutels of licenties.

Tests: 14 (versleuteling tegen Node-vectoren, inloggen tegen een
nagebootste cloud, zoeken tegen een nagebootst station).
