"""Config flow: address → device code → owner approves in Sara → entry; reauth; options."""

from __future__ import annotations

import pytest
from homeassistant import config_entries
from homeassistant.setup import async_setup_component
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sara.const import CONF_ONE_CONVERSATION, CONF_SHARE_CONTEXT, CONF_TOKEN, CONF_URL, DOMAIN

from .fake_sara import TOKEN, FakeSara


@pytest.fixture(autouse=True)
async def _core(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, "homeassistant", {})


async def _start(hass: HomeAssistant, url: str) -> dict:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_URL: url})


async def test_device_code_flow(hass: HomeAssistant, fake: FakeSara, sara_url: str) -> None:
    result = await _start(hass, sara_url)
    assert result["type"] is FlowResultType.SHOW_PROGRESS
    assert result["step_id"] == "approve"
    assert result["progress_action"] == "wait_for_approval"
    assert result["description_placeholders"]["code"] == "K7QM-3XTP"
    assert result["description_placeholders"]["verify_url"] == f"{sara_url.replace('http://', 'http://')}/device"

    fake.approved = True  # the owner approves it in Sara
    await hass.async_block_till_done()
    result = await hass.config_entries.flow.async_configure(result["flow_id"])
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Sara"
    assert result["data"][CONF_URL] == sara_url
    assert result["data"][CONF_TOKEN] == TOKEN
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.state is config_entries.ConfigEntryState.LOADED

    # The same server twice: refused.
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_URL: sara_url + "/"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_disclosure_must_be_accepted(hass: HomeAssistant, fake: FakeSara, sara_url: str) -> None:
    fake.disclosure_accepted = False
    result = await _start(hass, sara_url)
    fake.approved = True
    await hass.async_block_till_done()
    result = await hass.config_entries.flow.async_configure(result["flow_id"])
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "disclosure"
    assert "Sara is an AI" in result["description_placeholders"]["disclosure"]
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"accept": False})
    assert result["errors"] == {"base": "must_accept"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"accept": True})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert fake.disclosure_accepted


async def test_denied(hass: HomeAssistant, fake: FakeSara, sara_url: str) -> None:
    result = await _start(hass, sara_url)
    fake.denied = True
    await hass.async_block_till_done()
    result = await hass.config_entries.flow.async_configure(result["flow_id"])
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "denied"


async def test_bad_address(hass: HomeAssistant, socket_enabled) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_URL: "ftp://nope"})
    assert result["errors"] == {"base": "invalid_url"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_URL: "http://127.0.0.1:9"})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_reauth(hass: HomeAssistant, fake: FakeSara, sara_url: str) -> None:
    entry = MockConfigEntry(domain=DOMAIN, title="Sara", unique_id=sara_url.lower(), data={CONF_URL: sara_url, CONF_TOKEN: "sara_dev.old.signed-out"})
    entry.add_to_hass(hass)
    # The old token doesn't work: setup fails with an auth error and a reauth flow starts.
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is config_entries.ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert len(flows) == 1 and flows[0]["context"]["source"] == config_entries.SOURCE_REAUTH

    result = await hass.config_entries.flow.async_configure(flows[0]["flow_id"], {})
    assert result["type"] is FlowResultType.SHOW_PROGRESS
    fake.approved = True
    await hass.async_block_till_done()
    result = await hass.config_entries.flow.async_configure(result["flow_id"])
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    await hass.async_block_till_done()
    assert entry.data[CONF_TOKEN] == TOKEN
    assert entry.state is config_entries.ConfigEntryState.LOADED


async def test_options(hass: HomeAssistant, fake: FakeSara, sara_url: str) -> None:
    fake.companions = [{"id": "main", "name": "Sara"}, {"id": "c2", "name": "Max"}]
    entry = MockConfigEntry(domain=DOMAIN, title="Sara", unique_id=sara_url.lower(), data={CONF_URL: sara_url, CONF_TOKEN: TOKEN})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert "companion" in result["data_schema"].schema
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"companion": "c2", CONF_SHARE_CONTEXT: True, CONF_ONE_CONVERSATION: True})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options == {"companion": "c2", CONF_SHARE_CONTEXT: True, CONF_ONE_CONVERSATION: True}
    assert entry.runtime_data.client.companion == "c2"
