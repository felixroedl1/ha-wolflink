"""Shared Wolf SmartSet client handling."""

import asyncio
from dataclasses import dataclass

from wolf_comm.models import Device, Parameter, Value
from wolf_comm.wolf_client import WolfClient

from homeassistant.core import HomeAssistant
from homeassistant.helpers.httpx_client import create_async_httpx_client

from .const import DOMAIN

_ACCOUNT_CLIENTS = "account_clients"


@dataclass(slots=True)
class AccountClient:
    """Shared account client with serialized API access."""

    username: str
    password: str
    wolf_client: WolfClient
    lock: asyncio.Lock
    ref_count: int = 0

    async def fetch_system_list(self) -> list[Device]:
        """Fetch systems for this account."""
        async with self.lock:
            return await self.wolf_client.fetch_system_list()

    async def fetch_parameters(
        self, gateway_id: int, device_id: int
    ) -> list[Parameter]:
        """Fetch parameter descriptors for a device."""
        async with self.lock:
            return await self.wolf_client.fetch_parameters(gateway_id, device_id)

    async def fetch_system_state_list(self, device_id: int, gateway_id: int) -> bool:
        """Fetch online state for a device."""
        async with self.lock:
            return await self.wolf_client.fetch_system_state_list(device_id, gateway_id)

    async def fetch_value(
        self, gateway_id: int, device_id: int, parameters: list[Parameter]
    ) -> list[Value]:
        """Fetch values for the provided parameters."""
        async with self.lock:
            return await self.wolf_client.fetch_value(gateway_id, device_id, parameters)

    async def write_value(
        self, gateway_id: int, device_id: int, bundle_id: int, payload: dict[str, str]
    ) -> None:
        """Write a value to a parameter."""
        async with self.lock:
            await self.wolf_client.write_value(gateway_id, device_id, bundle_id, payload)


def _username_key(username: str) -> str:
    return username.strip().casefold()


def _build_account_client(
    hass: HomeAssistant, username: str, password: str
) -> AccountClient:
    wolf_client = WolfClient(
        username,
        password,
        client=create_async_httpx_client(hass=hass, verify_ssl=False, timeout=20),
    )
    return AccountClient(
        username=username,
        password=password,
        wolf_client=wolf_client,
        lock=asyncio.Lock(),
    )


def async_get_account_client(
    hass: HomeAssistant, username: str, password: str
) -> AccountClient:
    """Get or create the shared account client."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    account_clients: dict[str, AccountClient] = domain_data.setdefault(
        _ACCOUNT_CLIENTS, {}
    )
    key = _username_key(username)

    account_client = account_clients.get(key)
    if account_client is None or account_client.password != password:
        account_client = _build_account_client(hass, username, password)
        account_clients[key] = account_client

    account_client.ref_count += 1
    return account_client


def async_release_account_client(hass: HomeAssistant, username: str) -> None:
    """Decrease ref count for a shared account client."""
    domain_data = hass.data.get(DOMAIN)
    if domain_data is None:
        return

    account_clients: dict[str, AccountClient] | None = domain_data.get(_ACCOUNT_CLIENTS)
    if account_clients is None:
        return

    account_client = account_clients.get(_username_key(username))
    if account_client is None:
        return

    account_client.ref_count = max(0, account_client.ref_count - 1)
