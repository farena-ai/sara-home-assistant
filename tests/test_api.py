"""The API client alone, against the fake Sara (no Home Assistant needed)."""

from __future__ import annotations

import aiohttp
import pytest

from custom_components.sara.api import (
    SaraAuthError,
    SaraClient,
    SaraConnectionError,
    SaraDisclosureError,
    SaraError,
    is_local_host,
    normalize_url,
)

from .fake_sara import TOKEN, FakeSara


def test_normalize_url() -> None:
    assert normalize_url("192.168.1.20:7860/") == "http://192.168.1.20:7860"
    assert normalize_url("sara.local:7860") == "http://sara.local:7860"
    assert normalize_url("sara.example.com") == "https://sara.example.com"
    assert normalize_url(" https://sara.example.com/base/ ") == "https://sara.example.com/base"
    with pytest.raises(ValueError):
        normalize_url("ftp://x")
    with pytest.raises(ValueError):
        normalize_url("")
    # Security review L8: only really local addresses get plain http (the device token goes in clear).
    assert normalize_url("172.20.0.5:7860") == "http://172.20.0.5:7860"  # 172.16.0.0/12 is private
    assert normalize_url("172.217.3.110:7860") == "https://172.217.3.110:7860"  # 172.217.* is public
    assert normalize_url("10.example.com") == "https://10.example.com"
    assert normalize_url("192.168.example.com") == "https://192.168.example.com"
    assert normalize_url("localhost.evil.com:7860") == "https://localhost.evil.com:7860"
    assert normalize_url("sara.local.example.com") == "https://sara.local.example.com"
    assert normalize_url("10.0.0.7") == "http://10.0.0.7"
    assert normalize_url("[::1]:7860") == "http://[::1]:7860"
    assert normalize_url("[fe80::1]:7860") == "http://[fe80::1]:7860"
    assert normalize_url("169.254.10.10") == "http://169.254.10.10"
    assert normalize_url("localhost:7860") == "http://localhost:7860"
    assert normalize_url("8.8.8.8:7860") == "https://8.8.8.8:7860"
    assert is_local_host("SARA.LOCAL") and not is_local_host("") and not is_local_host("example.com")


async def test_device_code_sign_in(fake: FakeSara, sara_url: str) -> None:
    async with aiohttp.ClientSession() as s:
        c = SaraClient(s, sara_url)
        start = await c.device_start("Home Assistant")
        assert start.code == "K7QM-3XTP"
        assert fake.asked_scope == "home-assistant", "it asks Sara for its limited access"
        assert start.verify_url.endswith("/device")
        assert await c.device_poll(start.poll_token) == ("pending", None)
        fake.approved = True
        assert await c.wait_for_approval(start, max_wait=5) == ("approved", TOKEN)
        fake.approved, fake.denied = False, True
        assert await c.wait_for_approval(start, max_wait=5) == ("denied", None)
        start.poll_token = "nope"
        assert await c.device_poll(start.poll_token) == ("expired", None)


async def test_round_trip_and_safety_events(fake: FakeSara, sara_url: str) -> None:
    fake.notice = "Sara is an AI."
    fake.ask_approval = True
    async with aiohttp.ClientSession() as s:
        c = SaraClient(s, sara_url, TOKEN)
        assert (await c.whoami())["companion"] == "Sara"
        cid = await c.shared_conversation()
        assert fake.conversations[cid]["title"] == "Home Assistant"
        assert await c.shared_conversation() == cid  # found again, not made twice
        r = await c.send_message(cid, "Hi, what's your name?")
        assert r.text == "Hi! I'm Sara."
        assert fake.received == [(cid, "Hi, what's your name?", False)]
        # Approvals are declined (nobody to ask) and the reply says so; disclosure shown first.
        assert fake.decisions == [("ap-1", False)]
        assert r.speech().startswith("Sara is an AI.\n\nHi! I'm Sara.")
        assert "turn on light.kitchen" in r.speech()
        # Her safety check replaced the reply: the replacement is what's returned.
        fake.replace_with = "Let's talk about something else."
        assert (await c.send_message(cid, "x")).text == "Let's talk about something else."
        await c.send_note(cid, "the door opened")
        assert fake.received[-1] == (cid, "the door opened", True)


async def test_errors(fake: FakeSara, sara_url: str) -> None:
    async with aiohttp.ClientSession() as s:
        with pytest.raises(SaraAuthError):
            await SaraClient(s, sara_url, "sara_dev.x.wrong").whoami()
        c = SaraClient(s, sara_url, TOKEN)
        cid = await c.create_conversation()
        fake.disclosure_accepted = False
        with pytest.raises(SaraDisclosureError):
            await c.send_message(cid, "hi")
        # A failed turn: the AI notice she showed first stays with the error.
        fake.disclosure_accepted = True
        fake.notice, fake.fail_with = "Sara is an AI.", "model is down"
        with pytest.raises(SaraError) as err:
            await c.send_message(cid, "hi")
        assert str(err.value) == "Sara is an AI.\n\nmodel is down"
        fake.notice = fake.fail_with = None
        assert await c.companions() == []
        fake.companions = [{"id": "main", "name": "Sara"}, {"id": "c2", "name": "Max"}]
        assert [x["id"] for x in await c.companions()] == ["main", "c2"]
        await c.logout()
        with pytest.raises(SaraAuthError):
            await c.whoami()
        with pytest.raises(SaraConnectionError):
            await SaraClient(s, "http://127.0.0.1:9", TOKEN).whoami()
