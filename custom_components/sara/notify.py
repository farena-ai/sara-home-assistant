"""notify.sara_note: send Sara a note from an automation (she answers in her own time, in her app)."""

from __future__ import annotations

from homeassistant.components.notify import NotifyEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SaraConfigEntry, async_call, async_shared
from .conversation import device_info


async def async_setup_entry(hass: HomeAssistant, entry: SaraConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities([SaraNotifyEntity(entry)])


class SaraNotifyEntity(NotifyEntity):
    """A note in her 'Home Assistant' conversation. Her reply arrives like her other messages."""

    _attr_has_entity_name = True
    _attr_translation_key = "note"

    def __init__(self, entry: SaraConfigEntry) -> None:
        self.entry = entry
        self._attr_unique_id = f"{entry.entry_id}_note"
        self._attr_device_info = device_info(entry)

    async def async_send_message(self, message: str, title: str | None = None) -> None:
        client = self.entry.runtime_data.client
        text = f"Note from Home Assistant{f' ({title})' if title else ''}: {message}"

        cid = await async_shared(self.hass, self.entry)
        await async_call(self.hass, self.entry, client.send_note(cid, text))
