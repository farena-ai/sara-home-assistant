"""Optional: the whole integration inside Home Assistant against a real (throwaway) Sara server.

Skipped unless both are set:
    SARA_LIVE_URL=http://127.0.0.1:7880  SARA_LIVE_OWNER_TOKEN=<that server's owner token>
The test approves the device code through Sara's API with the owner token (the owner re-auth
Sara asks for), then asks her something through Home Assistant's conversation agent.
Use a throwaway server: it adds a device and a "Home Assistant" conversation, then signs out.
"""

from __future__ import annotations

import json
import os

import aiohttp
import pytest
from homeassistant import config_entries
from homeassistant.components import conversation
from homeassistant.core import Context, HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.setup import async_setup_component

from custom_components.sara.const import CONF_URL, DOMAIN

URL = os.environ.get("SARA_LIVE_URL")
OWNER = os.environ.get("SARA_LIVE_OWNER_TOKEN")

pytestmark = pytest.mark.skipif(not (URL and OWNER), reason="set SARA_LIVE_URL and SARA_LIVE_OWNER_TOKEN to run against a real Sara")


async def test_live_sign_in_and_conversation(hass: HomeAssistant, socket_enabled) -> None:
    assert await async_setup_component(hass, "homeassistant", {})
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_URL: URL})
    assert result["type"] is FlowResultType.SHOW_PROGRESS, result
    code = result["description_placeholders"]["code"]

    # The owner approves "Home Assistant" in Sara, confirming with the owner token.
    async with aiohttp.ClientSession() as s:
        h = {"authorization": f"Bearer {OWNER}", "content-type": "application/json"}
        async with s.post(f"{URL}/api/device/approve", headers=h, data=json.dumps({"code": code, "token": OWNER})) as r:
            assert r.status == 200, await r.text()

    await hass.async_block_till_done()
    result = await hass.config_entries.flow.async_configure(result["flow_id"])
    if result["type"] is FlowResultType.FORM and result["step_id"] == "disclosure":
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"accept": True})
    assert result["type"] is FlowResultType.CREATE_ENTRY, result
    await hass.async_block_till_done()

    agent = [e for e in hass.states.async_entity_ids("conversation") if e != "conversation.home_assistant"][0]
    res = await conversation.async_converse(hass, "Hi, what's your name?", None, Context(), agent_id=agent)
    speech = res.response.speech["plain"]["speech"]
    print(f"\nSara via Home Assistant: {speech}")
    assert res.response.response_type.value != "error", speech
    assert speech.strip()

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    await hass.config_entries.async_remove(entry.entry_id)  # signs Home Assistant out of Sara
    await hass.async_block_till_done()
