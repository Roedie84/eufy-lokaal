"""Instellen: land, e-mail en wachtwoord van (bij voorkeur) een tweede
Eufy-account, met de apparaten gedeeld. Vraagt Eufy om een e-mailcode of een
captcha, dan volgt die stap."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .cloud import CaptchaNodig, CloudFout, EMailCodeNodig, EufyCloud
from .const import CONF_EMAIL, CONF_LAND, CONF_SESSIE, CONF_WACHTWOORD, DOMAIN, STANDAARD_LAND


class EufyLokaalFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._cloud: EufyCloud | None = None
        self._invoer: dict[str, Any] = {}
        self._captcha: CaptchaNodig | None = None
        self._herauth = None

    async def _probeer(self, e_mailcode: str | None = None, captcha: tuple[str, str] | None = None) -> tuple[ConfigFlowResult | None, dict]:
        """(resultaat, {}) als het verder kan, (None, fouten) als niet."""
        assert self._cloud is not None
        try:
            await self._cloud.inloggen(e_mailcode=e_mailcode, captcha=captcha)
            await self._cloud.stations()
        except EMailCodeNodig:
            return await self.async_step_e_mailcode(), {}
        except CaptchaNodig as c:
            self._captcha = c
            return await self.async_step_captcha(), {}
        except CloudFout as err:
            return None, {"base": "e_mailcode" if err.code in (26050, 26051) else
                          "inloggen" if err.code in (26006, 10019, 100056) else "cloud"}
        except Exception:  # noqa: BLE001
            return None, {"base": "verbinding"}
        data = {**self._invoer, CONF_SESSIE: self._cloud.sessie.naar()}
        if self._herauth is not None:
            return self.async_update_reload_and_abort(self._herauth, data=data), {}
        return self.async_create_entry(title=f"Eufy ({self._invoer[CONF_EMAIL]})", data=data), {}

    async def async_step_user(self, user_input: dict | None = None) -> ConfigFlowResult:
        fouten: dict = {}
        if user_input is not None:
            self._invoer = {
                CONF_LAND: user_input[CONF_LAND].strip().upper(),
                CONF_EMAIL: user_input[CONF_EMAIL].strip(),
                CONF_WACHTWOORD: user_input[CONF_WACHTWOORD],
            }
            if self._herauth is None:
                await self.async_set_unique_id(self._invoer[CONF_EMAIL].lower())
                self._abort_if_unique_id_configured()
            self._cloud = EufyCloud(
                async_get_clientsession(self.hass), self._invoer[CONF_LAND],
                self._invoer[CONF_EMAIL], self._invoer[CONF_WACHTWOORD],
            )
            uit, fouten = await self._probeer()
            if uit is not None:
                return uit
        standaard = self._invoer or {CONF_LAND: STANDAARD_LAND}
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_LAND, default=standaard.get(CONF_LAND, STANDAARD_LAND)): str,
                vol.Required(CONF_EMAIL, default=standaard.get(CONF_EMAIL, "")): str,
                vol.Required(CONF_WACHTWOORD): str,
            }),
            errors=fouten,
        )

    async def async_step_e_mailcode(self, user_input: dict | None = None) -> ConfigFlowResult:
        fouten: dict = {}
        if user_input is not None:
            uit, fouten = await self._probeer(e_mailcode=user_input["code"].strip())
            if uit is not None:
                return uit
        return self.async_show_form(
            step_id="e_mailcode", data_schema=vol.Schema({vol.Required("code"): str}), errors=fouten
        )

    async def async_step_captcha(self, user_input: dict | None = None) -> ConfigFlowResult:
        fouten: dict = {}
        assert self._captcha is not None
        if user_input is not None:
            uit, fouten = await self._probeer(captcha=(self._captcha.captcha_id, user_input["antwoord"].strip()))
            if uit is not None:
                return uit
        return self.async_show_form(
            step_id="captcha",
            data_schema=vol.Schema({vol.Required("antwoord"): str}),
            description_placeholders={"afbeelding": self._captcha.afbeelding},
            errors=fouten,
        )

    async def async_step_reauth(self, _data: dict) -> ConfigFlowResult:
        self._herauth = self._get_reauth_entry()
        self._invoer = {k: self._herauth.data[k] for k in (CONF_LAND, CONF_EMAIL)}
        return await self.async_step_user()
