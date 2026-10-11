"""De Eufy-cloud: alleen voor het eenmalig ophalen van apparaten en sleutels.

Stap 1 van Eufy Lokaal (v0.1). De cloud is nodig om te weten welke
apparaten er zijn en met welke P2P-gegevens (p2p_did, sleutels) het
basisstation lokaal te bereiken is. Daarna gaat zoveel mogelijk lokaal.

Het protocol volgt de openbare beschrijving zoals eufy-security-client (MIT,
bropat) het implementeert; geen code overgenomen:

- het API-adres per land via `https://extend.eufylife.com/domain/<LAND>`;
- inloggen met `v2/passport/login_sec`: het wachtwoord AES-256-CBC
  versleuteld met het ECDH-gedeelde geheim (P-256) tussen een eigen sleutel
  en de publieke sleutel van de server (sleutel = geheim, iv = eerste 16
  bytes);
- antwoorden in `data` zijn op dezelfde manier versleuteld, met de
  publieke sleutel die de server bij het inloggen terugstuurt;
- 2FA per e-mail (code 26052) en captcha (100032/100033).

Het token is wekenlang geldig en wordt bewaard (`sessie`), zodat een
herstart van Home Assistant niet opnieuw inlogt - opnieuw inloggen kan een
nieuwe e-mailcode of captcha vragen.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from cryptography.hazmat.primitives import padding, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

_LOGGER = logging.getLogger(__name__)

DOMEIN_BASIS = "https://extend.eufylife.com"
SERVER_PUBLIEKE_SLEUTEL = (
    "04c5c00c4f8d1197cc7c3167c52bf7acb054d722f0ef08dcd7e0883236e0d72a38"
    "68d9750cb47fa4619248f3d83f0f662671dadc6e2d31c2f41db0161651c7c076"
)

CODE_OK = 0
CODE_E_MAILCODE_NODIG = 26052
CODE_E_MAILCODE_FOUT = 26050
CODE_E_MAILCODE_VERLOPEN = 26051
CODE_CAPTCHA_NODIG = 100032
CODE_CAPTCHA_FOUT = 100033
CODE_WACHTWOORD_FOUT = {26006, 10019, 100056}

VERIFICATIE_E_MAIL = 2


class CloudFout(Exception):
    """De cloud gaf geen bruikbaar antwoord."""

    def __init__(self, bericht: str, code: int | None = None) -> None:
        super().__init__(bericht)
        self.code = code


class EMailCodeNodig(CloudFout):
    """Eufy stuurde een code per e-mail; opnieuw inloggen met die code."""


class CaptchaNodig(CloudFout):
    """Eufy vraagt een captcha; `afbeelding` is een data-URL."""

    def __init__(self, captcha_id: str, afbeelding: str) -> None:
        super().__init__("captcha nodig", CODE_CAPTCHA_NODIG)
        self.captcha_id = captcha_id
        self.afbeelding = afbeelding


# --- versleuteling -------------------------------------------------------------


def nieuwe_prive_sleutel() -> str:
    sleutel = ec.generate_private_key(ec.SECP256R1())
    return format(sleutel.private_numbers().private_value, "064x")


def _prive(hex_sleutel: str) -> ec.EllipticCurvePrivateKey:
    return ec.derive_private_key(int(hex_sleutel, 16), ec.SECP256R1())


def publieke_sleutel_hex(prive_hex: str) -> str:
    """Ongecomprimeerd punt (04 || x || y), zoals Node `getPublicKey("hex")`."""
    return _prive(prive_hex).public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    ).hex()


def gedeeld_geheim(prive_hex: str, publiek_hex: str) -> bytes:
    """ECDH: de x-coördinaat van het gedeelde punt (32 bytes)."""
    publiek = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), bytes.fromhex(publiek_hex))
    return _prive(prive_hex).exchange(ec.ECDH(), publiek)


def versleutel(tekst: str, geheim: bytes) -> str:
    vuller = padding.PKCS7(128).padder()
    data = vuller.update(tekst.encode("utf-8")) + vuller.finalize()
    e = Cipher(algorithms.AES(geheim), modes.CBC(geheim[:16])).encryptor()
    return base64.b64encode(e.update(data) + e.finalize()).decode()


def ontsleutel(data_b64: str, geheim: bytes) -> str:
    d = Cipher(algorithms.AES(geheim), modes.CBC(geheim[:16])).decryptor()
    ruw = d.update(base64.b64decode(data_b64)) + d.finalize()
    ontvuller = padding.PKCS7(128).unpadder()
    tekst = (ontvuller.update(ruw) + ontvuller.finalize()).decode("utf-8", errors="replace")
    return tekst.split("\x00", 1)[0]


def gmt_tekst(offset_min: int) -> str:
    teken = "+" if offset_min >= 0 else "-"
    m = abs(offset_min)
    return f"GMT{teken}{m // 60:02d}:{m % 60:02d}"


def _offset_min() -> int:
    """De UTC-offset van nu in minuten (+120 in de Nederlandse zomer)."""
    from datetime import datetime

    offset = datetime.now().astimezone().utcoffset()
    return int(offset.total_seconds() // 60) if offset is not None else 0


# --- sessie ----------------------------------------------------------------------


@dataclass
class Sessie:
    """Wat bewaard wordt tussen herstarts. Bevat geen wachtwoord."""

    prive_sleutel: str = field(default_factory=nieuwe_prive_sleutel)
    server_sleutel: str = SERVER_PUBLIEKE_SLEUTEL
    openudid: str = field(default_factory=lambda: secrets.token_hex(8))
    api_basis: str | None = None
    token: str | None = None
    token_tot: float | None = None
    user_id: str | None = None

    @classmethod
    def uit(cls, data: Any) -> "Sessie":
        s = cls()
        if isinstance(data, dict):
            for k in ("prive_sleutel", "server_sleutel", "openudid", "api_basis", "token", "token_tot", "user_id"):
                if data.get(k):
                    setattr(s, k, data[k])
        return s

    def naar(self) -> dict:
        return {
            "prive_sleutel": self.prive_sleutel, "server_sleutel": self.server_sleutel,
            "openudid": self.openudid, "api_basis": self.api_basis, "token": self.token,
            "token_tot": self.token_tot, "user_id": self.user_id,
        }

    @property
    def geldig(self) -> bool:
        return bool(self.token and self.token_tot and time.time() < self.token_tot - 24 * 3600)


class EufyCloud:
    """Een dunne cliënt voor de paar cloudaanroepen die nodig zijn."""

    def __init__(self, websessie, land: str, email: str, wachtwoord: str, sessie: Sessie | None = None) -> None:
        self._web = websessie
        self.land = land.upper()
        self.email = email
        self._wachtwoord = wachtwoord
        self.sessie = sessie or Sessie()

    # algemeen -----------------------------------------------------------------

    def _koppen(self) -> dict:
        offset = _offset_min()
        koppen = {
            "App_version": "v4.6.0_1630", "Os_type": "android", "Os_version": "31",
            "Phone_model": "ONEPLUS A3003", "Country": self.land, "Language": "nl",
            "Openudid": self.sessie.openudid, "Net_type": "wifi", "Mnc": "02", "Mcc": "204",
            "Sn": self.sessie.openudid[:12], "Model_type": "PHONE", "Timezone": gmt_tekst(offset),
            "timezone": gmt_tekst(offset), "country": self.land, "Cache-Control": "no-cache",
        }
        if self.sessie.token:
            koppen["X-Auth-Token"] = self.sessie.token
        if self.sessie.user_id:
            koppen["gtoken"] = hashlib.md5(self.sessie.user_id.encode()).hexdigest()
        return koppen

    async def _api_basis(self) -> str:
        if self.sessie.api_basis:
            return self.sessie.api_basis
        async with self._web.get(f"{DOMEIN_BASIS}/domain/{self.land}") as antwoord:
            data = await antwoord.json(content_type=None)
        if not isinstance(data, dict) or data.get("code") != CODE_OK:
            raise CloudFout(f"geen API-adres voor land {self.land}", (data or {}).get("code"))
        self.sessie.api_basis = f"https://{data['data']['domain']}"
        return self.sessie.api_basis

    async def _post(self, pad: str, data: dict) -> dict:
        basis = await self._api_basis()
        async with self._web.post(f"{basis}/{pad}", json=data, headers=self._koppen()) as antwoord:
            if antwoord.status == 401:
                self.sessie.token = None
                raise CloudFout("niet (meer) ingelogd", 401)
            uit = await antwoord.json(content_type=None)
        if not isinstance(uit, dict):
            raise CloudFout(f"{pad}: geen JSON")
        return uit

    def _ontsleutel_data(self, data: str) -> Any:
        geheim = gedeeld_geheim(self.sessie.prive_sleutel, self.sessie.server_sleutel)
        return json.loads(ontsleutel(data, geheim))

    # inloggen -----------------------------------------------------------------

    async def inloggen(self, e_mailcode: str | None = None, captcha: tuple[str, str] | None = None) -> None:
        geheim = gedeeld_geheim(self.sessie.prive_sleutel, SERVER_PUBLIEKE_SLEUTEL)
        verzoek: dict = {
            "ab": self.land,
            "client_secret_info": {"public_key": publieke_sleutel_hex(self.sessie.prive_sleutel)},
            "enc": 0,
            "email": self.email,
            "password": versleutel(self._wachtwoord, geheim),
            "time_zone": _offset_min() * 60 * 1000,
            "transaction": str(int(time.time() * 1000)),
        }
        if e_mailcode:
            verzoek["verify_code"] = e_mailcode
        elif captcha:
            verzoek["captcha_id"], verzoek["answer"] = captcha
        # Inloggen gaat altijd met de vaste serversleutel; het antwoord geeft
        # de sleutel voor de rest van de sessie.
        self.sessie.server_sleutel = SERVER_PUBLIEKE_SLEUTEL
        uit = await self._post("v2/passport/login_sec", verzoek)
        code, data = uit.get("code"), uit.get("data") or {}
        if code == CODE_OK:
            self._ingelogd(data)
            if e_mailcode:
                await self._vertrouw_dit_apparaat(e_mailcode)
            return
        if code == CODE_E_MAILCODE_NODIG:
            self.sessie.token = data.get("auth_token")
            await self._post("v1/sms/send/verify_code", {
                "message_type": VERIFICATIE_E_MAIL, "transaction": str(int(time.time() * 1000)),
            })
            raise EMailCodeNodig("e-mailcode nodig", code)
        if code in (CODE_CAPTCHA_NODIG, CODE_CAPTCHA_FOUT):
            raise CaptchaNodig(str(data.get("captcha_id", "")), str(data.get("item", "")))
        if code in CODE_WACHTWOORD_FOUT:
            raise CloudFout("e-mailadres of wachtwoord klopt niet", code)
        if code in (CODE_E_MAILCODE_FOUT, CODE_E_MAILCODE_VERLOPEN):
            raise CloudFout("e-mailcode klopt niet of is verlopen", code)
        raise CloudFout(f"inloggen mislukt: {uit.get('msg')}", code)

    def _ingelogd(self, data: dict) -> None:
        sleutel = (data.get("server_secret_info") or {}).get("public_key")
        if sleutel:
            self.sessie.server_sleutel = sleutel
        self.sessie.user_id = str(data.get("user_id") or "")
        self.sessie.token = data.get("auth_token")
        self.sessie.token_tot = float(data.get("token_expires_at") or 0) or None

    async def _vertrouw_dit_apparaat(self, code: str) -> None:
        """Na een e-mailcode: dit "apparaat" vertrouwen, zodat er niet bij
        elke nieuwe login weer een code nodig is."""
        try:
            await self._post("v1/app/trust_device/add", {
                "verify_code": code, "transaction": str(int(time.time() * 1000)),
            })
        except CloudFout as err:
            _LOGGER.debug("Eufy: vertrouwen mislukt: %s", err)

    async def zorg_ingelogd(self) -> None:
        if not self.sessie.geldig:
            await self.inloggen()

    # gegevens -------------------------------------------------------------------

    async def _lijst(self, pad: str) -> list:
        await self.zorg_ingelogd()
        verzoek = {
            "device_sn": "", "num": 1000, "orderby": "", "page": 0, "station_sn": "",
            "time_zone": _offset_min() * 60 * 1000, "transaction": str(int(time.time() * 1000)),
        }
        uit = await self._post(pad, verzoek)
        if uit.get("code") != CODE_OK:
            raise CloudFout(f"{pad}: {uit.get('msg')}", uit.get("code"))
        data = uit.get("data")
        if isinstance(data, str):
            data = self._ontsleutel_data(data)
        return data if isinstance(data, list) else []

    async def stations(self) -> list[dict]:
        return await self._lijst("v2/house/station_list")

    async def apparaten(self) -> list[dict]:
        return await self._lijst("v2/house/device_list")


def parameter(item: dict, soort: int) -> Any:
    """Een waarde uit `params` (param_type -> param_value), als getal waar het kan."""
    for p in item.get("params") or []:
        if p.get("param_type") == soort:
            w = p.get("param_value")
            try:
                return int(w)
            except (TypeError, ValueError):
                return w
    return None


PARAM_BATTERIJ = 1101
PARAM_WIFI_RSSI = 1142
PARAM_ALARMSTAND = 1224
