# Eufy Lokaal

Eufy-beveiliging (HomeBase, deurbel, camera's) rechtstreeks in Home Assistant,
met een eigen implementatie van het Eufy-protocol. Geen add-on, geen
eufy-security-ws, geen integratie van derden.

## Stand van zaken

| Stap | Wat | Status |
|---|---|---|
| 1 | Apparaten en P2P-gegevens uit de cloud (eenmalig inloggen), stations lokaal vinden | **v0.1** |
| 2 | Lokale, versleutelde P2P-verbinding met het basisstation: status, batterijen | gepland |
| 3 | Gebeurtenissen lokaal: beweging, aanbellen, persoon, pakket | gepland |
| 4 | Alarmstand lezen en zetten | gepland |
| 5 | Snapshots en opnames | gepland |
| 6 | Livebeeld (via go2rtc) | gepland |

Elke stap leest eerst mee naast de bestaande Eufy-koppeling en vergelijkt;
pas als het klopt komt de volgende stap.

## Installeren

1. HACS → Integraties → ⋮ → Aangepaste repositories → `Roedie84/eufy-lokaal`
   (categorie Integratie) → downloaden → Home Assistant herstarten.
2. Instellingen → Apparaten & diensten → Integratie toevoegen → **Eufy Lokaal**.
3. Land (NL), e-mail en wachtwoord. **Gebruik bij voorkeur een tweede
   Eufy-account** waarmee je hoofdaccount de apparaten deelt: Eufy logt een
   account elders uit zodra er opnieuw wordt ingelogd. Vraagt Eufy om een
   e-mailcode of captcha, dan volgt die stap vanzelf.

Het token wordt bewaard; een herstart logt niet opnieuw in.

## Wat je ziet (stap 1)

- **Eufy lokaal** (diagnose): hoeveel stations en apparaten, of de stations
  lokaal te vinden zijn, antwoordtijd, laatste fout.
- Per station: **Lokaal** (gevonden / niet gevonden, met IP en poort),
  **Firmware**, **IP-adres**.
- Per apparaat: **Batterij** en **Wifi-signaal**, als de cloud die kent.

## Herkomst

Het protocol is nagebouwd naar de openbare implementatie
[eufy-security-client](https://github.com/bropat/eufy-security-client)
(MIT, © bropat). De versleuteling is getoetst tegen Node.js-crypto, dezelfde
bibliotheek die eufy-security-client gebruikt. Zie NOTICE.
