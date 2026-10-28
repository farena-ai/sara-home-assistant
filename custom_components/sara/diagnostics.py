"""Diagnostics: settings and state, never the device token."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import SaraConfigEntry
from .const import CONF_TOKEN

TO_REDACT = {CONF_TOKEN, "authorization", "pollToken", "poll_token"}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: SaraConfigEntry) -> dict[str, Any]:
    data = getattr(entry, "runtime_data", None)
    return {
        "entry": async_redact_data({"title": entry.title, "data": dict(entry.data), "options": dict(entry.options)}, TO_REDACT),
        "companion": getattr(data, "name", None),
        "tracked_conversations": len(data.conversations) if data else 0,
        "last_reply_at": data.last_reply_at.isoformat() if data and data.last_reply_at else None,
    }
