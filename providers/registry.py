"""Registry of provider adapters, keyed by provider key."""
from __future__ import annotations

from providers.adapters.aloha.adapter import AlohaAdapter
from providers.adapters.areen.adapter import AreenAdapter
from providers.adapters.layan.adapter import LayanAdapter
from providers.adapters.sky.adapter import SkyAdapter
from providers.contracts import BaseAdapter, Capability

ADAPTERS: dict[str, type[BaseAdapter]] = {
    "sky": SkyAdapter,
    "aloha": AlohaAdapter,
    "layan": LayanAdapter,
    "areen": AreenAdapter,
}


def get_adapter(key: str) -> BaseAdapter:
    """Instantiate the adapter registered under ``key``.

    Raises ``KeyError`` if the key isn't known so callers can decide how to
    handle "no provider for this company" (typically: NOT_FOUND).
    """
    return ADAPTERS[key]()


def supports(key: str, capability: Capability) -> bool:
    return capability in ADAPTERS[key].capabilities
