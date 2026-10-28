"""Constants for the Sara integration."""

from __future__ import annotations

import logging

DOMAIN = "sara"
LOGGER = logging.getLogger(__package__)
VERSION = "0.2.0"

CONF_URL = "url"
CONF_TOKEN = "token"
CONF_DEVICE_ID = "device_id"

# Options
CONF_COMPANION = "companion"
CONF_SHARE_CONTEXT = "share_context"
CONF_ONE_CONVERSATION = "one_conversation"

DEFAULT_SHARE_CONTEXT = False
DEFAULT_ONE_CONVERSATION = False

DEVICE_NAME = "Home Assistant"
# How long the config flow waits for the owner to approve the code (Sara's codes last 10 minutes).
APPROVAL_TIMEOUT = 600

SERVICE_SEND_MESSAGE = "send_message"
ATTR_TEXT = "text"
ATTR_CONVERSATION_ID = "conversation_id"
ATTR_CONFIG_ENTRY_ID = "config_entry_id"

SIGNAL_REPLY = f"{DOMAIN}_reply_{{entry_id}}"
