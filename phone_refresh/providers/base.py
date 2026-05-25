from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class RawResponse:
    """Normalized envelope returned by every provider's ``call`` method.

    The pattern matcher consumes only these four fields, which keeps the
    upstream-specific transport details fully encapsulated in each
    provider subclass.
    """

    text: str
    json: Any | None
    status_code: int
    error: str | None = None
    html_form_detected: bool = False


class BaseProvider(ABC):
    """Abstract upstream-refresh client.

    Subclasses define ``name`` (matches the registry key) and implement
    ``call(phone)`` which must always return a ``RawResponse`` — including
    when the upstream call fails (set ``error`` and ``status_code=0``).
    """

    name: str = ""
    timeout: int = 20

    @abstractmethod
    def call(self, phone: str) -> RawResponse: ...
