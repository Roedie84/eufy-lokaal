# Changelog — Eufy Lokaal

## v0.5.1 — vragen één voor één

- De HomeBase beantwoordt één databasevraag of afbeelding tegelijk: vragen gaan nu in een rij, de volgende pas na het antwoord (of na 15 s).
- Base64 van afbeeldingen robuuster (witruimte, ontbrekende opvulling); bij een mislukt beeld staat de oorzaak in `laatste_fout`.
- `antwoorden`: de laatste returncodes van de HomeBase.

## v0.5.0 — stap 5: laatste beeld en opnames, lokaal

- Per camera een **Laatste beeld** (image-entiteit): de HomeBase geeft het pad van het laatste gebeurtenisbeeld (database, 10013), de integratie haalt de JPEG lokaal op (CMD_DATABASE_IMAGE, 1308). Beelden met de kop "eufysecurity" worden ontsleuteld (getoetst tegen eufy-security-client).
- Per camera **Opnames**: opnames van gisteren en vandaag op de HomeBase (database, 10017), de laatste 10 met tijden en pad in de attributen.
- Elke 5 minuten bijgewerkt, en 8 s na elke melding of wisseling van de alarmstand.
- Naar de database gaan alleen leesvragen (10013, 10017); wissen of wijzigen wordt geweigerd (toets).

## v0.4.0 — stap 4: bewakingsstand lokaal instellen

- Nieuw: **Bewakingsstand** (select: weg, thuis, schema, aangepast 1-3, geofence, uitgeschakeld) en **Alarm** (alarmpaneel: weg, thuis, aangepast, uitschakelen) per HomeBase.
- Zoals de app op firmware ≥ 2.0.7.9: CMD_SET_PAYLOAD (1350) met CMD_SET_ARMING (1224), versleuteld. De integratie wacht op de returncode van de HomeBase en meldt een weigering als fout; daarna worden de parameters ververst.
- Een melding van een nieuwe alarmstand (ook vanuit de app) ververst de parameters, zodat de bewakingsstand meeloopt.
- Het enige dat de integratie verandert is de bewakingsstand; de broncodetoets bewaakt dat.
- RSA-fout bij de sessiesleutel geeft nu de oorzaak.

## v0.3.0 — stap 3: lokale berichten en parameters (alleen lezen)

- Na de sessiesleutel vraagt de integratie CMD_CAMERA_INFO (1103) op: alle parameters van HomeBase en apparaten, lokaal. Elke 10 minuten opnieuw. Verstuurde opdrachten worden herhaald tot de HomeBase ze bevestigt.
- Berichten die de HomeBase uit zichzelf stuurt (alarmstand, vertraagd alarm, meldingen) gaan als gebeurtenis `eufy_lokaal_bericht` de bus op, met station en apparaat.
- Nieuwe sensoren: per station **Alarmstand** en **Laatste melding**; per station en apparaat **Lokale parameters** (diagnose, niet in de recorder).
- Batterij en wifi-signaal nemen de lokale waarde als die er is (attribuut `bron`).
- Nog steeds niets dat iets verandert: alleen GATEWAYINFO en CAMERA_INFO gaan naar de HomeBase (broncodetoets).

## v0.2.4 — sessiesleutel: eerst niveau 1 ontsleutelen

- Het GATEWAYINFO-antwoord op het CONTROL-kanaal is zelf met de sleutel van niveau 1 versleuteld; daarbinnen staan het cipher-id (op de HomeBase: 189) en het RSA-blok. Getoetst op een echt pakket van de HomeBase 3.

## v0.2.3 — sessiesleutel van de HomeBase 3

- De HomeBase 3 (firmware 3.8) antwoordt op CMD_GATEWAYINFO via het CONTROL-kanaal (soort 2); dat kanaal wordt nu ook gelezen.
- Het antwoord (144 bytes) heeft geen returncode: cipher-id, twee lege bytes en één RSA-blok. Beide vormen worden geprobeerd, de cloud beslist.
- `sleutel_pogingen` laat per poging zien wat er gebeurde.

## v0.2.2 — sessie blijft staan, DATA-inhoud zichtbaar

- Niet meer elke minuut opnieuw verbinden: de poort in het zoekantwoord wisselt per ronde, alleen het IP telt nog.
- `laatste_data`: inhoud van binnenkomende DATA-pakketten met wat ermee gebeurde; `voorbeelden`: het eerste pakket van elke soort.

## v0.2.1 — P2P-diagnose

- Elk P2P-pakket wordt geteld per soort, in en uit (`pakketten_in`, `pakketten_uit`), met de koppen van de laatste pakketten (`laatste_pakketten`).
- De meting loopt door over herverbindingen; `uitval` telt nu correct.
- Een sessie geldt als levend zolang er binnen 30 s iets binnenkomt (niet alleen PONG).

## v0.2.0 — stap 2: een lokale, versleutelde P2P-sessie met de HomeBase

Stap 1 vond de HomeBase lokaal (192.168.2.52, ~20 ms). Nu een eigen sessie:

- **Verbinden**: CHECK_CAM met de p2p_did naar het gevonden adres (en ±3
  poorten), wachten op CAM_ID; daarna elke 5 s PING, en PONG op elke PING
  van het station. Geen PONG in 20 s = uitval; bij de volgende zoekronde
  (elke minuut) wordt opnieuw verbonden.
- **Sessiesleutel**: eerste opdracht CMD_GATEWAYINFO (1100). Het antwoord
  geeft een cipher-id en een RSA-versleutelde sleutel; de RSA-sleutel komt
  één keer uit de cloud (`v2/app/cipher/get_ciphers`) en wordt bewaard
  zolang de integratie draait. Lukt dat niet: niveau 1 (sleutel uit
  serienummer en p2p_did).
- **Berichten**: elk DATA-pakket krijgt een ACK, wordt in volgorde gezet en
  ingelezen (16 bytes kop "XZYH"), ook als het over meerdere pakketten
  loopt; versleutelde inhoud wordt met de sessiesleutel ontsleuteld. Deze
  stap telt per opdracht wat het station stuurt - de basis voor stap 3.
- **Alleen lezen**: buiten CHECK_CAM, PING/PONG, ACK en CMD_GATEWAYINFO
  stuurt deze versie niets (broncodetoets).
- Nieuwe sensor per station: **P2P** (verbonden, versleuteld / niet
  verbonden), met rtt, versleutelingsniveau, aantal berichten per opdracht
  en de laatste berichten.

Tests: 26 (o.a. RSA-vector uit Node.js, berichten over meerdere pakketten,
volledige sessie tegen een nagebootst station).

## v0.1.2 — eigen icoon

Een eigen icoon in `brand/` (schild met cameralens), zodat de integratie in
Home Assistant herkenbaar is. Bewust geen Eufy-logo: dat is een merk van
Anker en dit project is daar niet aan verbonden.

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
