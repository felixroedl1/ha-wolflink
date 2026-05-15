"""Rate limiting helpers for Wolf SmartSet authentication."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from time import monotonic

from homeassistant.core import HomeAssistant

from .const import DOMAIN

_AUTH_GUARD = "auth_guard"
_MIN_AUTH_INTERVAL_SECONDS = 8.0


@dataclass(slots=True)
class _AuthState:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_auth_monotonic: float = 0.0


def _get_auth_state(hass: HomeAssistant, username: str) -> _AuthState:
    domain_data = hass.data.setdefault(DOMAIN, {})
    guards: dict[str, _AuthState] = domain_data.setdefault(_AUTH_GUARD, {})
    key = username.strip().casefold()
    state = guards.get(key)
    if state is None:
        state = _AuthState()
        guards[key] = state
    return state


@asynccontextmanager
async def async_auth_guard(hass: HomeAssistant, username: str):
    """Serialize auth attempts and enforce a cool-down between attempts."""
    state = _get_auth_state(hass, username)
    async with state.lock:
        elapsed = monotonic() - state.last_auth_monotonic
        if elapsed < _MIN_AUTH_INTERVAL_SECONDS:
            await asyncio.sleep(_MIN_AUTH_INTERVAL_SECONDS - elapsed)
        try:
            yield
        finally:
            state.last_auth_monotonic = monotonic()
