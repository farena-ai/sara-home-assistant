"""Sara: talk to your own self-hosted Sara from Home Assistant.

Sara becomes a conversation agent (Assist, voice satellites), ``sara.send_message`` lets
automations ask her something and get her reply, ``notify.sara`` sends her a note, and a sensor
keeps her last reply. Sign-in uses Sara's device code: the owner approves Home Assistant in Sara.
"""

from __future__ import annotations

from collections.abc import Coroutine
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady, HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util

from .api import SCOPE, SaraAuthError, SaraClient, SaraConnectionError, SaraDisclosureError, SaraError, SaraReply
from .const import (
    ATTR_CONFIG_ENTRY_ID,
    ATTR_CONVERSATION_ID,
    ATTR_TEXT,
    CONF_COMPANION,
    CONF_TOKEN,
    CONF_URL,
    DOMAIN,
    LOGGER,
    SERVICE_SEND_MESSAGE,
    SIGNAL_REPLY,
    VERSION,
)

PLATFORMS: list[Platform] = [Platform.CONVERSATION, Platform.NOTIFY, Platform.SENSOR]
# The Sara conversations this entry made (sara.send_message may only use these): kept per entry.
MAX_CREATED = 500
STORE_VERSION = 1
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

SEND_MESSAGE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_TEXT): vol.All(cv.string, vol.Length(min=1, max=100_000)),
        vol.Optional(ATTR_CONVERSATION_ID): cv.string,
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
    }
)


@dataclass
class SaraData:
    """What a config entry keeps while it's loaded."""

    client: SaraClient
    name: str
    last_reply: SaraReply | None = None
    last_reply_at: datetime | None = None
    # HA conversation id -> Sara conversation id (HA's ids last minutes; this stays small).
    conversations: dict[str, str] = field(default_factory=dict)
    # Every Sara conversation this entry made (saved, so it survives restarts).
    created: list[str] = field(default_factory=list)
    store: Store | None = None


type SaraConfigEntry = ConfigEntry[SaraData]


def make_client(hass: HomeAssistant, entry: ConfigEntry) -> SaraClient:
    return SaraClient(
        async_get_clientsession(hass),
        entry.data[CONF_URL],
        entry.data[CONF_TOKEN],
        companion=entry.options.get(CONF_COMPANION) or None,
        version=VERSION,
    )


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Registers sara.send_message."""

    async def send_message(call: ServiceCall) -> ServiceResponse:
        entry = _pick_entry(hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
        cid = call.data.get(ATTR_CONVERSATION_ID)
        # Security review M4: any Home Assistant user or automation can call this, so it only talks
        # in the conversations this integration made (or its shared one), never another Sara chat.
        if cid and not await _own_conversation(hass, entry, cid):
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="unknown_conversation")
        reply = await async_ask(hass, entry, call.data[ATTR_TEXT], sara_conversation_id=cid)
        return {"reply": reply.speech(), "conversation_id": reply.conversation_id}

    hass.services.async_register(
        DOMAIN,
        SERVICE_SEND_MESSAGE,
        send_message,
        schema=SEND_MESSAGE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    return True


def _pick_entry(hass: HomeAssistant, entry_id: str | None) -> SaraConfigEntry:
    entries = [e for e in hass.config_entries.async_loaded_entries(DOMAIN)]
    if entry_id:
        entries = [e for e in entries if e.entry_id == entry_id]
    if not entries:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="not_loaded")
    if len(entries) > 1 and not entry_id:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="which_entry")
    return entries[0]


async def _own_conversation(hass: HomeAssistant, entry: SaraConfigEntry, cid: str) -> bool:
    data = entry.runtime_data
    if cid in data.created or cid in data.conversations.values():
        return True
    shared = await async_call(hass, entry, data.client.find_conversation())
    return shared == cid


async def async_remember(hass: HomeAssistant, entry: SaraConfigEntry, cid: str) -> None:
    """Notes a Sara conversation this entry made (sara.send_message may use it later)."""
    data = entry.runtime_data
    if cid in data.created:
        return
    data.created.append(cid)
    del data.created[:-MAX_CREATED]
    if data.store is not None:
        await data.store.async_save({"conversations": data.created})


async def async_shared(hass: HomeAssistant, entry: SaraConfigEntry) -> str:
    """The one 'Home Assistant' conversation (made, and remembered, the first time)."""
    cid = await async_call(hass, entry, entry.runtime_data.client.shared_conversation())
    await async_remember(hass, entry, cid)
    return cid


async def async_ask(
    hass: HomeAssistant,
    entry: SaraConfigEntry,
    text: str,
    *,
    sara_conversation_id: str | None = None,
) -> SaraReply:
    """One turn with Sara, in the given conversation or her 'Home Assistant' one."""
    client = entry.runtime_data.client
    cid = sara_conversation_id or await async_shared(hass, entry)
    reply = await async_call(hass, entry, client.send_message(cid, text))
    record_reply(hass, entry, reply)
    return reply


async def async_call[T](hass: HomeAssistant, entry: SaraConfigEntry, coro: Coroutine[Any, Any, T]) -> T:
    """Awaits a Sara call; her errors become Home Assistant errors (and a lost sign-in starts reauth)."""
    try:
        return await coro
    except SaraAuthError as err:
        entry.async_start_reauth(hass)
        raise HomeAssistantError(translation_domain=DOMAIN, translation_key="auth") from err
    except SaraDisclosureError as err:
        raise HomeAssistantError(translation_domain=DOMAIN, translation_key="disclosure") from err
    except SaraConnectionError as err:
        raise HomeAssistantError(translation_domain=DOMAIN, translation_key="cannot_connect", translation_placeholders={"error": str(err)}) from err
    except SaraError as err:
        raise HomeAssistantError(translation_domain=DOMAIN, translation_key="sara_error", translation_placeholders={"error": str(err)}) from err


@callback
def record_reply(hass: HomeAssistant, entry: SaraConfigEntry, reply: SaraReply) -> None:
    """Keeps her last reply for the sensor."""
    entry.runtime_data.last_reply = reply
    entry.runtime_data.last_reply_at = dt_util.utcnow()
    async_dispatcher_send(hass, SIGNAL_REPLY.format(entry_id=entry.entry_id))


async def async_setup_entry(hass: HomeAssistant, entry: SaraConfigEntry) -> bool:
    client = make_client(hass, entry)
    try:
        who: dict[str, Any] = await client.whoami()
    except SaraAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except SaraError as err:
        raise ConfigEntryNotReady(str(err)) from err
    # Security review M4: a token from before Home Assistant asked for its limited access has full
    # rights: sign in again (the owner approves it once more) to get the limited one.
    device = who.get("device") or {}
    if device.get("scope") not in (None, SCOPE):
        raise ConfigEntryAuthFailed("Sign in again: Sara now gives Home Assistant its own limited access.")
    store: Store = Store(hass, STORE_VERSION, f"{DOMAIN}.{entry.entry_id}")
    saved = await store.async_load() or {}
    created = [str(c) for c in saved.get("conversations", []) if isinstance(c, str)][-MAX_CREATED:]
    entry.runtime_data = SaraData(client=client, name=str(who.get("companion") or "Sara"), created=created, store=store)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_options_updated))
    return True


async def _options_updated(hass: HomeAssistant, entry: SaraConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: SaraConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Removing the integration signs Home Assistant out of Sara (best effort)."""
    await Store(hass, STORE_VERSION, f"{DOMAIN}.{entry.entry_id}").async_remove()
    try:
        await make_client(hass, entry).logout()
    except SaraError as err:
        LOGGER.debug("Signing out of Sara didn't work (sign it out under Devices in Sara): %s", err)
