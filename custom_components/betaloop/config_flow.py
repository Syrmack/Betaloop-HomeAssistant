"""Configuration via l'interface : URL du serveur + clé API, puis options."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_API_KEY, CONF_URL
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import BetaloopApi, BetaloopAuthError, BetaloopError
from .const import (
    CONF_HISTORY_HOURS,
    CONF_SCAN_INTERVAL,
    DEFAULT_HISTORY_HOURS,
    DEFAULT_SCAN_INTERVAL_MIN,
    DOMAIN,
    MAX_HISTORY_HOURS,
    MAX_SCAN_INTERVAL_MIN,
    MIN_SCAN_INTERVAL_MIN,
)

_PASSWORD = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


class BetaloopConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def _validate(self, url: str, api_key: str) -> str | None:
        """Renvoie la clé d'erreur à afficher, ou None si le serveur accepte la clé."""
        api = BetaloopApi(async_get_clientsession(self.hass), url, api_key)
        try:
            await api.check()
        except BetaloopAuthError:
            return "invalid_auth"
        except BetaloopError:
            return "cannot_connect"
        return None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            url = user_input[CONF_URL].rstrip("/")
            await self.async_set_unique_id(url)
            self._abort_if_unique_id_configured()
            if (error := await self._validate(url, user_input[CONF_API_KEY])) is None:
                return self.async_create_entry(
                    title="Betaloop",
                    data={CONF_URL: url, CONF_API_KEY: user_input[CONF_API_KEY]},
                )
            errors["base"] = error

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required(CONF_URL): TextSelector(TextSelectorConfig(type=TextSelectorType.URL)),
                        vol.Required(CONF_API_KEY): _PASSWORD,
                    }
                ),
                user_input or {CONF_URL: "http://"},
            ),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            if (error := await self._validate(entry.data[CONF_URL], user_input[CONF_API_KEY])) is None:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_API_KEY: user_input[CONF_API_KEY]}
                )
            errors["base"] = error

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_API_KEY): _PASSWORD}),
            errors=errors,
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Bouton « Reconfigurer » de l'intégration : changer l'URL du serveur ou la clé API à tout
        moment, sans attendre que le serveur refuse l'ancienne. Clé laissée vide = clé actuelle."""
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()
        if user_input is not None:
            url = user_input[CONF_URL].rstrip("/")
            api_key = user_input.get(CONF_API_KEY) or entry.data[CONF_API_KEY]
            if url != entry.unique_id:
                await self.async_set_unique_id(url)
                self._abort_if_unique_id_configured()
            if (error := await self._validate(url, api_key)) is None:
                return self.async_update_reload_and_abort(
                    entry, unique_id=url, data_updates={CONF_URL: url, CONF_API_KEY: api_key}
                )
            errors["base"] = error

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required(CONF_URL): TextSelector(TextSelectorConfig(type=TextSelectorType.URL)),
                        vol.Optional(CONF_API_KEY): _PASSWORD,
                    }
                ),
                {CONF_URL: (user_input or entry.data)[CONF_URL]},
            ),
            errors=errors,
        )

    @staticmethod
    def async_get_options_flow(config_entry) -> BetaloopOptionsFlow:
        return BetaloopOptionsFlow()


class BetaloopOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data={k: int(v) for k, v in user_input.items()})

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required(CONF_SCAN_INTERVAL): NumberSelector(
                            NumberSelectorConfig(
                                min=MIN_SCAN_INTERVAL_MIN,
                                max=MAX_SCAN_INTERVAL_MIN,
                                unit_of_measurement="min",
                                mode=NumberSelectorMode.BOX,
                            )
                        ),
                        vol.Required(CONF_HISTORY_HOURS): NumberSelector(
                            NumberSelectorConfig(
                                min=1,
                                max=MAX_HISTORY_HOURS,
                                unit_of_measurement="h",
                                mode=NumberSelectorMode.BOX,
                            )
                        ),
                    }
                ),
                {
                    CONF_SCAN_INTERVAL: self.config_entry.options.get(
                        CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MIN
                    ),
                    CONF_HISTORY_HOURS: self.config_entry.options.get(
                        CONF_HISTORY_HOURS, DEFAULT_HISTORY_HOURS
                    ),
                },
            ),
        )
