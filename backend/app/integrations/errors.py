"""Errors raised by the Meta / WhatsApp API clients."""

from __future__ import annotations


class ExternalAPIError(RuntimeError):
    """A call to Meta failed.

    `transient` errors (network problems, 5xx, rate limits) are worth retrying;
    permanent ones (bad token, invalid number, closed messaging window) are not.
    """

    def __init__(self, message: str, *, transient: bool, code: int | None = None) -> None:
        super().__init__(message)
        self.transient = transient
        self.code = code
