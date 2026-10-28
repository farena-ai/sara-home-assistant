"""Config flow: the Sara address, then a device code the owner approves in Sara."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import DeviceStart, SaraClient, SaraError, normalize_url
from .const import (
    APPROVAL_TIMEOUT,
    CONF_COMPANION,
    CONF_DEVICE_ID,
    CONF_ONE_CONVERSATION,
    CONF_SHARE_CONTEXT,
    CONF_TOKEN,
    CONF_URL,
    DEFAULT_ONE_CONVERSATION,
    DEFAULT_SHARE_CONTEXT,
    DEVICE_NAME,
    DOMAIN,
    LOGGER,
    VERSION,
)

STEP_USER = vol.Schema({vol.Required(CONF_URL): str})
# Example addresses for the address form (hassfest wants URLs as placeholders, not in strings.json).
EXAMPLES = {"example_lan": "http://192.168.1.20:7860", "example_domain": "https://sara.example.com"}


class SaraConfigFlow(ConfigFlow, domain=DOMAIN):
    """Signs Home Assistant in to Sara with a device code."""

    VERSION = 1

    def __init__(self) -> None:
        self._url: str | None = None
        self._start: DeviceStart | None = None
        self._task: asyncio.Task[tuple[str, str | None]] | None = None
        self._token: str | None = None
        self._who: dict[str, Any] = {}
        self._failure = "expired"

    def _client(self, token: str | None = None) -> SaraClient:
        assert self._url
        return SaraClient(async_get_clientsession(self.hass), self._url, token, version=VERSION)

    # ---------- address ----------
    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                self._url = normalize_url(user_input[CONF_URL])
            except ValueError:
                errors["base"] = "invalid_url"
            else:
                await self.async_set_unique_id(self._url.lower())
                self._abort_if_unique_id_configured()
                return await self._begin()
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(STEP_USER, user_input or {}),
            errors=errors,
            description_placeholders=EXAMPLES,
        )

    async def _begin(self) -> ConfigFlowResult:
        """Asks Sara for a code, then shows it while waiting."""
        try:
            self._start = await self._client().device_start(DEVICE_NAME)
        except SaraError as err:
            LOGGER.debug("Sara device start failed: %s", err)
            form_id = "reauth_confirm" if self.source == "reauth" else "user"
            return self.async_show_form(
                step_id=form_id,
                data_schema=None if form_id == "reauth_confirm" else self.add_suggested_values_to_schema(STEP_USER, {CONF_URL: self._url}),
                errors={"base": "cannot_connect"},
                description_placeholders={"url": self._url or "", **EXAMPLES},
            )
        self._task = None
        return await self.async_step_approve()

    # ---------- waiting for the owner ----------
    async def async_step_approve(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        assert self._start
        if self._task is None:
            self._task = self.hass.async_create_task(self._client().wait_for_approval(self._start, max_wait=APPROVAL_TIMEOUT))
        if not self._task.done():
            return self.async_show_progress(
                step_id="approve",
                progress_action="wait_for_approval",
                description_placeholders={
                    "code": self._start.code,
                    "verify_url": self._start.verify_url,
                    "approve_in": self._start.approve_in,
                },
                progress_task=self._task,
            )
        try:
            status, token = self._task.result()
        except SaraError as err:
            LOGGER.debug("Polling Sara failed: %s", err)
            status, token = "error", None
        if status == "approved" and token:
            self._token = token
            return self.async_show_progress_done(next_step_id="finish")
        self._failure = status if status in ("denied", "expired") else "cannot_connect"
        return self.async_show_progress_done(next_step_id="failed")

    async def async_step_failed(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_abort(reason=self._failure)

    # ---------- signed in ----------
    async def async_step_finish(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        try:
            self._who = await self._client(self._token).whoami()
        except SaraError:
            return self.async_abort(reason="cannot_connect")
        if not self._who.get("disclosureAccepted"):
            return await self.async_step_disclosure()
        return await self._done()

    async def async_step_disclosure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Sara's AI disclosure must be accepted once before she answers (as in her own apps)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get("accept"):
                try:
                    await self._client(self._token).accept_disclosure()
                except SaraError:
                    return self.async_abort(reason="cannot_connect")
                return await self._done()
            errors["base"] = "must_accept"
        return self.async_show_form(
            step_id="disclosure",
            data_schema=vol.Schema({vol.Required("accept", default=False): bool}),
            description_placeholders={"disclosure": str(self._who.get("disclosure") or "")},
            errors=errors,
        )

    async def _done(self) -> ConfigFlowResult:
        assert self._url and self._token
        device = self._who.get("device") or {}
        data = {CONF_URL: self._url, CONF_TOKEN: self._token, CONF_DEVICE_ID: device.get("id")}
        if self.source == "reauth":
            return self.async_update_reload_and_abort(self._get_reauth_entry(), data_updates=data)
        return self.async_create_entry(title=str(self._who.get("companion") or "Sara"), data=data)

    # ---------- reauth ----------
    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        self._url = entry_data[CONF_URL]
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is None:
            return self.async_show_form(step_id="reauth_confirm", description_placeholders={"url": self._url or ""})
        return await self._begin()

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> SaraOptionsFlow:
        return SaraOptionsFlow()


class SaraOptionsFlow(OptionsFlow):
    """Which companion, whether Home Assistant says where you are, one conversation or many."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            if user_input.get(CONF_COMPANION) == "main":
                user_input[CONF_COMPANION] = ""
            return self.async_create_entry(data=user_input)
        entry = self.config_entry
        companions: list[dict[str, Any]] = []
        try:
            client = SaraClient(async_get_clientsession(self.hass), entry.data[CONF_URL], entry.data[CONF_TOKEN], version=VERSION)
            companions = await client.companions()
        except SaraError as err:
            LOGGER.debug("Couldn't list Sara's companions: %s", err)
        opts = entry.options
        fields: dict[Any, Any] = {}
        if len(companions) > 1:
            choices = [selector.SelectOptionDict(value=str(c["id"]), label=str(c.get("name") or c["id"])) for c in companions]
            fields[vol.Optional(CONF_COMPANION, default=opts.get(CONF_COMPANION) or "main")] = selector.SelectSelector(
                selector.SelectSelectorConfig(options=choices, mode=selector.SelectSelectorMode.DROPDOWN)
            )
        fields[vol.Optional(CONF_SHARE_CONTEXT, default=opts.get(CONF_SHARE_CONTEXT, DEFAULT_SHARE_CONTEXT))] = bool
        fields[vol.Optional(CONF_ONE_CONVERSATION, default=opts.get(CONF_ONE_CONVERSATION, DEFAULT_ONE_CONVERSATION))] = bool
        return self.async_show_form(step_id="init", data_schema=vol.Schema(fields))
