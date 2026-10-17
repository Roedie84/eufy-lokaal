# Changelog — Eufy Lokaal

## v0.7.19 — livestream blijft bereikbaar na herladen

- Gevonden bij de HA-controle van 17-10: de stream van de deurbel gaf sinds 16-10 elke ~15 minuten "404 Not Found" (113 keer). Het livestream-token hoorde bij de coördinator; na het herladen van de integratie kwam er een nieuw token, terwijl de stream van Home Assistant de oude URL bleef gebruiken.
- Nu één token per Home Assistant-run (in `hass.data`): herladen houdt hetzelfde token.

## v0.7.18 — diagnose niet meer in de database

- De diagnosesensoren *Eufy lokaal* (~8,5 KB) en *P2P* van de HomeBase (~4,4 KB, pakket- en hexdumps) schreven hun attributen bij elke update naar de recorder: samen ongeveer 60 MB per dag. Die attributen gaan niet meer naar de database; live blijven ze gewoon zichtbaar, net als in de diagnostiek.

## v0.7.17 — live momentopname overleeft een herstart

- *Live momentopname* stond alleen in het geheugen. Na elke herstart van Home Assistant waren de momentopnames van deurbel en camera's onbeschikbaar, tot er weer werd aangebeld of de dienst `eufy_lokaal.momentopname` liep. Het laatste beeld per camera wordt nu ook bewaard in `.storage/eufy_lokaal/` en bij het opstarten teruggezet, met tijd en reden. Het attribuut `bewaard` zegt of het beeld van voor de herstart is.

## v0.7.16 — alleen zinvolle parameters

- De HomeBase stuurt per camera ook alle instellingen van andere apparaattypen mee. Nog maar 12 parameters worden een eigen entiteit, met Nederlandse naam en eenheid: PIR-sensor, status-LED, batterijtemperatuur, dagen in gebruik, detecties en opnames totaal, luidsprekervolume, microfoon/luidspreker uit, clipduur, bewegingsgevoeligheid en nachtzichtmodus. De rest wordt bij het opstarten opgeruimd en blijft zichtbaar in *Lokale parameters*.

## v0.7.15 — gefilterde opnamevraag weer uit

- De HomeBase 3 negeert het filter `detection_type`: elke soort gaf dezelfde 20 opnames, waardoor alle recente opnames ten onrechte als persoon, huisdier én voertuig werden gemeld. De gefilterde vragen zijn weer weg. Lokaal is alleen beweging (trigger 2) en aanbellen (8) te bepalen.

## v0.7.14 — detectiesoorten via gefilterde opnamevraag

- De HomeBase 3 kent de lokale vraag met uitsneden (10017) niet (ERROR_NO_SUPPORT). Nu vraagt de integratie per soort (persoon, voertuig, huisdier) de opnames van vandaag op met het filter `detection_type`; opnames die terugkomen krijgen die soort. Negeert de HomeBase het filter (dezelfde lijst als zonder filter), dan gebeurt er niets; de uitkomst staat in de diagnostiek onder `detectiefilter`.

## v0.7.13 — live kijken is geen persoon

- `trigger_type` 4 blijkt bij de HomeBase 3 een opname door live kijken (app of HA), niet een persoon. Zulke opnames geven geen gebeurtenis meer; beweging komt nu uit de opname zelf (2 = beweging, 8 = aanbellen) in plaats van alleen uit de teller.
- Persoon, huisdier, voertuig, geluid en pakket komen alleen uit de uitsneden (detection_type); die staan nu per opname in het attribuut `opnames` van *Opnames*.

## v0.7.12 — persoon, huisdier, voertuig, geluid en pakket

- Uit de database van de HomeBase: `trigger_type` (4 = persoon, 8 = aanbellen) van elke opname en de uitsneden (`detection_type`: 1/2 persoon, 4 voertuig, 8 huisdier, 128 geluid, 256 huilen, 512/1024 pakket) via een lokale databasevraag (10017). Elke nieuwe opname (laatste 10 minuten) zet de bijbehorende detecties aan en geeft een gebeurtenis, één keer per soort.
- Een bekende persoon (person_id) wordt *bekend_persoon*.

## v0.7.11 — beelden direct na het opstarten

- Zodra de lokale sessie staat, worden laatste beelden en opnames meteen opgevraagd. Lukte de eerste verbinding niet, dan bleven ze tot 5 minuten onbeschikbaar.

## v0.7.10 — meetversie voor detectiesoorten

- *Opnames* toont nu het volledige record van elke opname en de ruwe laatste info van de HomeBase (attribuut `laatste_info_ruw`, niet in de recorder). Daarmee worden de codes voor persoon, dier en voertuig herleid.

## v0.7.9 — beweging sneller in HA

- Na elk bericht van een camera (wakker, beweging, live) wordt de laatste info na 3, 15, 40 en 75 s opgevraagd. Een opname staat pas in de database als hij klaar is; zo zie je beweging en beeld binnen ongeveer een minuut in plaats van tot 5 minuten later.

## v0.7.8 — beweging van camera's

- De HomeBase stuurt lokaal geen bewegingsbericht (2037) voor camera's; alleen aanbellen komt zo binnen. Beweging wordt nu herkend aan de gebeurtenisteller in de database: loopt die op, dan gaat *Beweging* 30 s aan en verschijnt het nieuwe beeld.
- Een camera die wakker wordt (bericht 1400) laat na 3 en 12 s de laatste info opvragen, zodat beweging en beeld snel binnen zijn.

## v0.7.7 — geen dubbele ruimtenaam in entiteit-ID's

- Heet een camera net als zijn ruimte (Zolder op de zolder), dan wordt het ID `zolder_…` in plaats van `zolder_zolder_…`.

## v0.7.6 — entiteit-ID's volgen de ruimte

- Eenmalig worden de entiteit-ID's opnieuw gemaakt zoals Home Assistant dat zelf doet (ruimte + apparaat + naam). Na het verplaatsen van een camera naar een andere ruimte gaf Home Assistant de oude ID's terug.

## v0.7.5 — geen dubbele parameters

- Parameters die al een eigen entiteit hebben (batterij, wifi-signaal, alarmstand, bewakingsstand, LAN-IP) worden geen extra parameter-entiteit meer; bestaande worden opgeruimd.

## v0.7.4 — alleen parameters met een naam

- Parameters zonder bekende naam ("Parameter <nummer>") worden geen entiteit meer; bestaande worden bij het opstarten uit het entiteitenregister verwijderd.

## v0.7.3 — gebeurtenisbeeld sneller binnen

- Na een gebeurtenis (beweging, aanbellen) wordt het nieuwe beeld na 4 én na 15 s opgevraagd, zodat **Laatste beeld** vrijwel direct bijgewerkt is (nodig om beelden bij beweging op te slaan).

## v0.7.2 — momentopname robuuster, H.265

- Momentopname: SPS/PPS uit eerdere frames worden voor het keyframe gezet en er worden een paar frames gedecodeerd tot er beeld is; lukt het niet, dan het volgende keyframe.
- H.265 (HEVC) wordt herkend (streamsoort in de kop of de NAL-kop) en zo ook in de livestream en de momentopname gebruikt.

## v0.7.1 — live momentopname bij aanbellen

- Bij aanbellen haalt de integratie meteen een **live beeld** op: livestream kort starten, het eerste keyframe naar JPEG (PyAV + Pillow), stream direct weer stoppen. Te zien als **Live momentopname** (image-entiteit) en als gebeurtenis `eufy_lokaal_momentopname`.
- Dienst `eufy_lokaal.momentopname` (doel: een camera) voor hetzelfde op elk moment.

## v0.7.0 — geluid, aanbellen en detecties, alle parameters als entiteiten

- **Geluid** in de livestream: AAC (ADTS) van de HomeBase gaat mee in de MPEG-TS.
- **Gebeurtenissen**: de HomeBase 3 meldt lokaal beweging, persoon, aanbellen enz. (CMD_NOTIFY_PAYLOAD → 2037). Per camera binaire sensoren **Beweging**, **Persoon**, **Huisdier**, **Voertuig**, **Geluid**, **Pakket** en bij de deurbel **Aangebeld** (30 s aan), een **Gebeurtenis**-entiteit (event) en de bus-gebeurtenis `eufy_lokaal_gebeurtenis`. Direct daarna wordt het nieuwe beeld opgehaald.
- **Alle parameters** die de HomeBase lokaal geeft, zijn nu een eigen (diagnose)entiteit, met een leesbare naam waar die bekend is.

## v0.6.2 — livestream werkt; zuiniger en veiliger

- Getest: Schuur/zolder (S350) 4K 15 fps en de deurbel (E340) 1600×2200, frames ontsleuteld zonder fouten.
- Na de maximale duur (10 min) 5 minuten rust: een lezer die blijft herverbinden kan een batterijcamera niet eindeloos laten streamen.
- Minder rekenwerk per videopakket (honderden per seconde).
- `herverbonden`: waarom de sessie opnieuw werd opgebouwd.

## v0.6.1 — camera's van alle apparaten

- `via_device` weggelaten: Home Assistant weigerde daardoor de camera-entiteiten van twee van de drie camera's.

## v0.6.0 — stap 6: livestream, lokaal

- Per camera een **camera-entiteit**: stilstaand beeld = het laatste gebeurtenisbeeld; live beeld op vraag via de HomeBase (geen cloud).
- Startopdracht per cameratype zoals eufy-security-client op een HomeBase 3; per stream een eigen RSA-sleutel, de HomeBase versleutelt daarmee de AES-sleutel van de frames.
- Frames (H.264) worden per kijker in MPEG-TS verpakt (PyAV) en via `/api/eufy_lokaal/live/<sn>?token=…` geserveerd; dat is de `stream_source`, zodat go2rtc/WebRTC en HLS werken.
- Zonder kijkers stopt de stream na 20 s, en na 10 minuten altijd (batterij). Nog zonder geluid.
- Videopakketten die door elkaar binnenkomen worden weer op volgorde gezet.
- Naar de HomeBase gaat nog steeds alleen wat nodig is: bewakingsstand, leesvragen, livestream starten/stoppen (broncodetoets).

## v0.5.4 — einddatum telt niet mee

- De HomeBase leest de einddatum als "tot": de vraag loopt nu van vandaag tot morgen.

## v0.5.3 — opnames van vandaag

- De HomeBase geeft bij een begindatum de oudste opnames vanaf die dag; de vraag gaat nu over vandaag (tot 50), zodat de nieuwste erin staan.

## v0.5.2 — alle beelden, opnames per datum

- Afbeeldingen in de URL-variant van base64 (`-`/`_`) worden nu ook gelezen; daardoor ontbrak het beeld van één camera.
- Opnames via CMD_DATABASE_QUERY_BY_DATE (10006, platte lijst); 10017 blijft herkend.
- `database_antwoorden` en tekst bij onbekende berichten in de diagnose.

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
