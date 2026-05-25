"""Provider registry for upstream phone-number-refresh services."""
from __future__ import annotations

from phone_refresh.providers.aloha import AlohaProvider
from phone_refresh.providers.areen import AreenProvider
from phone_refresh.providers.base import BaseProvider, RawResponse
from phone_refresh.providers.layan import LayanProvider
from phone_refresh.providers.sky import SkyProvider

PROVIDER_REGISTRY: dict[str, type[BaseProvider]] = {
    "sky": SkyProvider,
    "aloha": AlohaProvider,
    "layan": LayanProvider,
    "areen": AreenProvider,
}


def get_provider(name: str) -> BaseProvider:
    """Instantiate the provider class registered under ``name``.

    Raises ``KeyError`` if the provider isn't known so callers can decide
    how to handle "no provider for this company" (typically: NOT_FOUND).
    """
    cls = PROVIDER_REGISTRY[name]
    return cls()


__all__ = [
    "BaseProvider",
    "RawResponse",
    "PROVIDER_REGISTRY",
    "get_provider",
    "SkyProvider",
    "AlohaProvider",
    "LayanProvider",
    "AreenProvider",
]
