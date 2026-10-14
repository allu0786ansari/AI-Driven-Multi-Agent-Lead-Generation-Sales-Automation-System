"""Builds the configured Meta clients (META_API_MODE = live | fake)."""

from __future__ import annotations

from functools import lru_cache

from app.config import get_settings
from app.integrations.meta_graph import FakeMetaGraphClient, LiveMetaGraphClient, MetaGraphClient
from app.integrations.meta_messaging import (
    FakeMetaMessagingClient,
    LiveMetaMessagingClient,
    MetaMessagingClient,
)
from app.integrations.whatsapp import CloudAPIWhatsAppSender, FakeWhatsAppSender, WhatsAppSender


@lru_cache
def get_whatsapp_sender() -> WhatsAppSender:
    s = get_settings()
    if s.meta_api_mode == "fake":
        return FakeWhatsAppSender()
    return CloudAPIWhatsAppSender(
        s.meta_graph_base_url,
        s.meta_graph_version,
        s.whatsapp_phone_number_id,
        s.whatsapp_access_token,
        s.meta_api_timeout_seconds,
    )


@lru_cache
def get_meta_graph() -> MetaGraphClient:
    s = get_settings()
    if s.meta_api_mode == "fake":
        return FakeMetaGraphClient()
    return LiveMetaGraphClient(
        s.meta_graph_base_url, s.meta_graph_version, s.meta_page_access_token, s.meta_api_timeout_seconds
    )


@lru_cache
def get_meta_messaging() -> MetaMessagingClient:
    s = get_settings()
    if s.meta_api_mode == "fake":
        return FakeMetaMessagingClient()
    return LiveMetaMessagingClient(
        s.meta_graph_base_url,
        s.meta_graph_version,
        s.meta_page_id,
        s.meta_page_access_token,
        s.instagram_api_base_url,
        s.instagram_account_id,
        s.instagram_access_token,
        s.meta_api_timeout_seconds,
    )


def reset_clients() -> None:
    """For tests: forget cached clients after settings change."""
    get_whatsapp_sender.cache_clear()
    get_meta_graph.cache_clear()
    get_meta_messaging.cache_clear()
