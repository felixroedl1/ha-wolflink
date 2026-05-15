"""The Wolf SmartSet Service integration."""

import logging
from functools import partial

from httpx import RequestError
from wolf_comm.token_auth import InvalidAuth
from wolf_comm.wolf_client import FetchFailed, WolfClient

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.httpx_client import create_async_httpx_client

from .const import (
    CONF_EXPERT_MODE,
    CONF_EXPERT_PASSWORD,
    DEVICE_GATEWAY,
    DEVICE_ID,
    DEVICE_NAME,
    DOMAIN,
)
from .coordinator import WolflinkConfigEntry, WolfLinkCoordinator, fetch_parameters
from .rate_limit import async_auth_guard

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.SENSOR,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SWITCH,
    Platform.BUTTON,
]


def _get_entry_setting(entry: ConfigEntry, key: str, default=None):
    """Return config setting preferring options over stored entry data."""
    if key in entry.options:
        return entry.options[key]
    return entry.data.get(key, default)


def _resolve_expert_mode(entry: ConfigEntry) -> bool | str:
    """Resolve expert mode value for wolf_comm."""
    expert_mode = bool(_get_entry_setting(entry, CONF_EXPERT_MODE, False))
    expert_password = _get_entry_setting(entry, CONF_EXPERT_PASSWORD)
    if not expert_mode:
        return False
    if isinstance(expert_password, str) and expert_password.strip():
        return expert_password.strip()
    return True


async def async_setup_entry(hass: HomeAssistant, entry: WolflinkConfigEntry) -> bool:
    """Set up Wolf SmartSet Service from a config entry."""

    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]
    device_name = entry.data[DEVICE_NAME]
    device_id = entry.data[DEVICE_ID]
    gateway_id = entry.data[DEVICE_GATEWAY]
    _LOGGER.debug(
        "Setting up wolflink integration for device: %s (ID: %s, gateway: %s)",
        device_name,
        device_id,
        gateway_id,
    )
    expert_mode = _resolve_expert_mode(entry)
    httpx_client = await hass.async_add_executor_job(
        partial(
            create_async_httpx_client,
            hass=hass,
            verify_ssl=False,
            timeout=20,
        )
    )

    wolf_client = WolfClient(
        username,
        password,
        expert_p=expert_mode,
        client=httpx_client,
    )

    async with async_auth_guard(hass, username):
        parameters = await fetch_parameters_init(wolf_client, gateway_id, device_id)

    coordinator = WolfLinkCoordinator(
        hass, entry, wolf_client, parameters, gateway_id, device_id
    )

    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: WolflinkConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate old entry."""
    # convert unique_id to string
    if entry.version == 1 and entry.minor_version == 1:
        if isinstance(entry.unique_id, int):
            hass.config_entries.async_update_entry(
                entry, unique_id=str(entry.unique_id)
            )
            device_registry = dr.async_get(hass)
            for device in dr.async_entries_for_config_entry(
                device_registry, entry.entry_id
            ):
                new_identifiers = set()
                for identifier in device.identifiers:
                    if identifier[0] == DOMAIN:
                        new_identifiers.add((DOMAIN, str(identifier[1])))
                    else:
                        new_identifiers.add(identifier)
                device_registry.async_update_device(
                    device.id, new_identifiers=new_identifiers
                )
        hass.config_entries.async_update_entry(entry, minor_version=2)

    return True


async def fetch_parameters_init(client: WolfClient, gateway_id: int, device_id: int):
    """Fetch all available parameters with usage of WolfClient but handles all exceptions and results in ConfigEntryNotReady."""
    try:
        return await fetch_parameters(client, gateway_id, device_id)
    except InvalidAuth as exception:
        raise ConfigEntryNotReady(
            "Authentication temporary failed (Wolf portal unavailable or rate limited)."
        ) from exception
    except (FetchFailed, RequestError) as exception:
        raise ConfigEntryNotReady(
            f"Error communicating with API: {exception}"
        ) from exception
