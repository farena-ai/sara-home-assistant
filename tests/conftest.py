"""Shared fixtures: a fake Sara server on 127.0.0.1 and Home Assistant's custom-integration loader."""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator

import pytest
from aiohttp.test_utils import TestServer

from .fake_sara import FakeSara

if sys.platform == "win32":
    # Home Assistant runs on Linux; its test harness blocks sockets, but on Windows asyncio's event
    # loop itself needs a local socket pair. Let sockets through there (the tests only use 127.0.0.1).
    import pytest_socket

    pytest_socket.disable_socket = lambda *a, **k: None


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Loads custom_components/sara in every Home Assistant test."""
    return


@pytest.fixture
def fake() -> FakeSara:
    return FakeSara()


@pytest.fixture
async def sara_url(fake: FakeSara, socket_enabled) -> AsyncIterator[str]:
    """The fake Sara's address (http://127.0.0.1:<port>)."""
    server = TestServer(fake.app(), host="127.0.0.1")
    await server.start_server()
    try:
        yield f"http://127.0.0.1:{server.port}"
    finally:
        await server.close()
