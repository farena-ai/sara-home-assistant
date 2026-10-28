"""Sara as a Home Assistant conversation agent (Assist, voice satellites)."""

from __future__ import annotations

from typing import Literal

from homeassistant.components import conversation
from homeassistant.const import MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import area_registry as ar, device_registry as dr, intent
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SaraConfigEntry, async_ask, async_call, async_remember, async_shared
from .const import CONF_ONE_CONVERSATION, CONF_SHARE_CONTEXT, DOMAIN, VERSION

MAX_TRACKED = 200


def device_info(entry: SaraConfigEntry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer="Sara (self-hosted)",
        model="Companion",
        sw_version=VERSION,
        entry_type=DeviceEntryType.SERVICE,
        configuration_url=entry.runtime_data.client.url,
    )


async def async_setup_entry(hass: HomeAssistant, entry: SaraConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities([SaraConversationEntity(entry)])


class SaraConversationEntity(conversation.ConversationEntity):
    """Sends what you say to Sara and returns her reply. Her safety layer runs on her server."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_supports_streaming = False

    def __init__(self, entry: SaraConfigEntry) -> None:
        self.entry = entry
        self._attr_unique_id = entry.entry_id
        self._attr_device_info = device_info(entry)

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        return MATCH_ALL

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        conversation.async_set_agent(self.hass, self.entry, self)

    async def async_will_remove_from_hass(self) -> None:
        conversation.async_unset_agent(self.hass, self.entry)
        await super().async_will_remove_from_hass()

    def _where(self, user_input: conversation.ConversationInput) -> str | None:
        """'Kitchen speaker in the Kitchen': only when the owner chose to share it."""
        if not user_input.device_id:
            return None
        dev = dr.async_get(self.hass).async_get(user_input.device_id)
        if dev is None:
            return None
        name = dev.name_by_user or dev.name
        area = ar.async_get(self.hass).async_get_area(dev.area_id) if dev.area_id else None
        if name and area:
            return f"{name} in the {area.name}"
        return name or (area.name if area else None)

    async def _async_handle_message(
        self,
        user_input: conversation.ConversationInput,
        chat_log: conversation.ChatLog,
    ) -> conversation.ConversationResult:
        data = self.entry.runtime_data
        opts = self.entry.options
        text = user_input.text
        if opts.get(CONF_SHARE_CONTEXT):
            where = self._where(user_input)
            text = f"[Said to Home Assistant{f' ({where})' if where else ''}] {text}"
        try:
            if opts.get(CONF_ONE_CONVERSATION):
                sara_id = await async_shared(self.hass, self.entry)
            else:
                sara_id = data.conversations.get(chat_log.conversation_id)
                if sara_id is None:
                    sara_id = await async_call(self.hass, self.entry, data.client.create_conversation())
                    await async_remember(self.hass, self.entry, sara_id)
                    data.conversations[chat_log.conversation_id] = sara_id
                    while len(data.conversations) > MAX_TRACKED:
                        data.conversations.pop(next(iter(data.conversations)))
            reply = await async_ask(self.hass, self.entry, text, sara_conversation_id=sara_id)
            speech = reply.speech()
        except HomeAssistantError as err:
            response = intent.IntentResponse(language=user_input.language)
            response.async_set_error(intent.IntentResponseErrorCode.UNKNOWN, str(err))
            return conversation.ConversationResult(response=response, conversation_id=chat_log.conversation_id)
        except Exception as err:  # noqa: BLE001 - the Sara client raises its own errors; anything else is reported, not crashed on
            response = intent.IntentResponse(language=user_input.language)
            response.async_set_error(intent.IntentResponseErrorCode.UNKNOWN, f"Sara: {err}")
            return conversation.ConversationResult(response=response, conversation_id=chat_log.conversation_id)

        chat_log.async_add_assistant_content_without_tools(conversation.AssistantContent(agent_id=user_input.agent_id, content=speech))
        response = intent.IntentResponse(language=user_input.language)
        response.async_set_speech(speech)
        return conversation.ConversationResult(response=response, conversation_id=chat_log.conversation_id, continue_conversation=speech.rstrip().endswith("?"))
