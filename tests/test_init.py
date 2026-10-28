"""Conversation agent round trip, sara.send_message, notify, sensor, diagnostics, removal."""

from __future__ import annotations

import pytest
from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sara.const import CONF_ONE_CONVERSATION, CONF_SHARE_CONTEXT, CONF_TOKEN, CONF_URL, DOMAIN
from custom_components.sara.diagnostics import async_get_config_entry_diagnostics

from .fake_sara import TOKEN, FakeSara


@pytest.fixture
async def entry(hass: HomeAssistant, fake: FakeSara, sara_url: str) -> MockConfigEntry:
    assert await async_setup_component(hass, "homeassistant", {})
    e = MockConfigEntry(domain=DOMAIN, title="Sara", unique_id=sara_url.lower(), data={CONF_URL: sara_url, CONF_TOKEN: TOKEN, "device_id": "abc123"})
    e.add_to_hass(hass)
    assert await hass.config_entries.async_setup(e.entry_id)
    await hass.async_block_till_done()
    assert e.state is ConfigEntryState.LOADED
    return e


def _agent_id(hass: HomeAssistant) -> str:
    states = hass.states.async_entity_ids("conversation")
    ours = [s for s in states if s != "conversation.home_assistant"]
    assert len(ours) == 1, states
    return ours[0]


async def test_conversation_round_trip(hass: HomeAssistant, entry: MockConfigEntry, fake: FakeSara) -> None:
    fake.notice = "Sara is an AI, not a person."
    agent = _agent_id(hass)
    result = await conversation.async_converse(hass, "Hi, what's your name?", None, Context(), agent_id=agent)
    speech = result.response.speech["plain"]["speech"]
    assert speech == "Sara is an AI, not a person.\n\nHi! I'm Sara."
    assert len(fake.received) == 1
    sara_conv, text, background = fake.received[0]
    assert (text, background) == ("Hi, what's your name?", False)
    assert fake.conversations[sara_conv]["title"] == "Home Assistant"

    # The same HA conversation stays in the same Sara conversation; a new one gets its own.
    fake.notice = None
    await conversation.async_converse(hass, "And again?", result.conversation_id, Context(), agent_id=agent)
    assert fake.received[1][0] == sara_conv
    await conversation.async_converse(hass, "New chat", None, Context(), agent_id=agent)
    assert fake.received[2][0] != sara_conv

    # The sensor keeps her last reply: the time as its state, her words in an attribute that the
    # recorder leaves out (security review M4), and no Sara conversation id.
    state = hass.states.get("sensor.sara_last_reply")
    assert state is not None and state.attributes["text"] == "Hi! I'm Sara."
    assert state.attributes.get("device_class") == "timestamp" and "T" in state.state
    assert "Sara" not in state.state and "conversation_id" not in state.attributes
    from custom_components.sara.sensor import SaraLastReplySensor

    assert "text" in SaraLastReplySensor._unrecorded_attributes


async def test_conversation_options(hass: HomeAssistant, entry: MockConfigEntry, fake: FakeSara) -> None:
    hass.config_entries.async_update_entry(entry, options={CONF_ONE_CONVERSATION: True, CONF_SHARE_CONTEXT: True})
    await hass.async_block_till_done()
    agent = _agent_id(hass)
    await conversation.async_converse(hass, "one", None, Context(), agent_id=agent)
    await conversation.async_converse(hass, "two", None, Context(), agent_id=agent)
    assert fake.received[0][0] == fake.received[1][0]
    assert fake.received[0][1] == "[Said to Home Assistant] one"


async def test_conversation_errors_and_reauth(hass: HomeAssistant, entry: MockConfigEntry, fake: FakeSara) -> None:
    agent = _agent_id(hass)
    fake.valid_tokens.clear()  # signed out in Sara
    result = await conversation.async_converse(hass, "hello", None, Context(), agent_id=agent)
    assert result.response.response_type.value == "error"
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress()
    assert any(f["context"]["source"] == "reauth" for f in flows)


async def test_send_message_service_and_notify(hass: HomeAssistant, entry: MockConfigEntry, fake: FakeSara) -> None:
    resp = await hass.services.async_call(DOMAIN, "send_message", {"text": "What's for dinner?"}, blocking=True, return_response=True)
    assert resp["reply"] == "Hi! I'm Sara."
    cid = resp["conversation_id"]
    assert fake.conversations[cid]["title"] == "Home Assistant"
    resp = await hass.services.async_call(DOMAIN, "send_message", {"text": "again"}, blocking=True, return_response=True)
    assert resp["conversation_id"] == cid

    notify_id = "notify.sara_note"
    assert hass.states.get(notify_id) is not None
    await hass.services.async_call("notify", "send_message", {"entity_id": notify_id, "message": "Front door opened", "title": "Door"}, blocking=True)
    assert fake.received[-1] == (cid, "Note from Home Assistant (Door): Front door opened", True)

    # Security review M4: only conversations this integration made (or its shared one).
    resp = await hass.services.async_call(DOMAIN, "send_message", {"text": "again", "conversation_id": cid}, blocking=True, return_response=True)
    assert resp["conversation_id"] == cid
    agent = _agent_id(hass)
    await conversation.async_converse(hass, "made by the agent", None, Context(), agent_id=agent)
    made = fake.received[-1][0]
    assert made != cid
    resp = await hass.services.async_call(DOMAIN, "send_message", {"text": "follow up", "conversation_id": made}, blocking=True, return_response=True)
    assert resp["conversation_id"] == made
    private = "11111111-2222-3333-4444-555555555555"
    fake.conversations[private] = {"id": private, "title": "Diary", "updatedAt": "2026-10-27T00:00:00Z"}
    sent = len(fake.received)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "send_message", {"text": "what did we talk about?", "conversation_id": private}, blocking=True, return_response=True)
    assert len(fake.received) == sent, "nothing was sent to it"

    fake.disclosure_accepted = False
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(DOMAIN, "send_message", {"text": "x"}, blocking=True, return_response=True)


async def test_created_conversations_survive_a_reload(hass: HomeAssistant, entry: MockConfigEntry, fake: FakeSara) -> None:
    agent = _agent_id(hass)
    await conversation.async_converse(hass, "hello", None, Context(), agent_id=agent)
    made = fake.received[-1][0]
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    resp = await hass.services.async_call(DOMAIN, "send_message", {"text": "still mine?", "conversation_id": made}, blocking=True, return_response=True)
    assert resp["conversation_id"] == made


async def test_full_access_token_asks_to_sign_in_again(hass: HomeAssistant, fake: FakeSara, sara_url: str) -> None:
    """A token from before the limited Home Assistant access: setup asks for a new sign-in."""
    assert await async_setup_component(hass, "homeassistant", {})
    fake.scope = "full"
    e = MockConfigEntry(domain=DOMAIN, title="Sara", unique_id=sara_url.lower(), data={CONF_URL: sara_url, CONF_TOKEN: TOKEN})
    e.add_to_hass(hass)
    await hass.config_entries.async_setup(e.entry_id)
    await hass.async_block_till_done()
    assert e.state is ConfigEntryState.SETUP_ERROR
    assert any(f["context"]["source"] == "reauth" for f in hass.config_entries.flow.async_progress())


async def test_diagnostics_redacts_token(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["entry"]["data"][CONF_TOKEN] == "**REDACTED**"
    assert TOKEN not in repr(diag)
    assert diag["companion"] == "Sara"


async def test_remove_signs_out(hass: HomeAssistant, entry: MockConfigEntry, fake: FakeSara) -> None:
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert fake.logged_out
