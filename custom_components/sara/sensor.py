"""sensor.sara_last_reply: her last reply through Home Assistant.

Her words can be private, and Home Assistant's recorder keeps every state for days and shows it to
every user: so the state is only WHEN she last replied (a timestamp), her words are the ``text``
attribute, which the recorder leaves out, and the Sara conversation id isn't exposed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SaraConfigEntry
from .const import SIGNAL_REPLY
from .conversation import device_info

async def async_setup_entry(hass: HomeAssistant, entry: SaraConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities([SaraLastReplySensor(entry)])


class SaraLastReplySensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "last_reply"
    _attr_should_poll = False
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    # Security review M4: her words never go to the recorder's history.
    _unrecorded_attributes = frozenset({"text"})

    def __init__(self, entry: SaraConfigEntry) -> None:
        self.entry = entry
        self._attr_unique_id = f"{entry.entry_id}_last_reply"
        self._attr_device_info = device_info(entry)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_REPLY.format(entry_id=self.entry.entry_id), self._updated))

    @callback
    def _updated(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> datetime | None:
        return self.entry.runtime_data.last_reply_at

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        d = self.entry.runtime_data
        if d.last_reply is None:
            return None
        return {"text": d.last_reply.speech()}
