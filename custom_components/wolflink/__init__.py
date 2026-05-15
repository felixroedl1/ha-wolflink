"""The Wolf SmartSet Service integration."""

import logging
import re
from functools import partial

from httpx import RequestError
from wolf_comm.token_auth import InvalidAuth
from wolf_comm.wolf_client import FetchFailed, WolfClient

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.httpx_client import create_async_httpx_client
from homeassistant.util import slugify

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


def _entity_prefix_for_entry(entry: ConfigEntry) -> str:
    """Build the standardized entity ID prefix for a config entry."""
    prefix_seed = (
        entry.data.get(DEVICE_NAME)
        or entry.title
        or entry.unique_id
        or DOMAIN
    )
    prefix_slug = slugify(str(prefix_seed)) or DOMAIN
    if prefix_slug.startswith(f"{DOMAIN}_"):
        return prefix_slug
    if prefix_slug == DOMAIN:
        return DOMAIN
    return f"{DOMAIN}_{prefix_slug}"


def _prefixed_object_id(prefix: str, object_id: str) -> str:
    """Return object_id with expected prefix."""
    expected_prefix = f"{prefix}_"
    if object_id.startswith(expected_prefix):
        return object_id

    normalized = object_id
    if normalized.startswith(f"{DOMAIN}_"):
        normalized = normalized[len(DOMAIN) + 1 :]
        device_slug = prefix[len(DOMAIN) + 1 :] if prefix.startswith(f"{DOMAIN}_") else ""
        if device_slug and normalized.startswith(f"{device_slug}_"):
            normalized = normalized[len(device_slug) + 1 :]
        elif re.match(r"^[a-z0-9]*\d[a-z0-9]*_", normalized):
            normalized = normalized.split("_", maxsplit=1)[1]
    normalized = normalized.strip("_")
    if not normalized:
        normalized = object_id.strip("_")
    return f"{prefix}_{normalized}"


def _migrate_entity_prefixes(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Migrate existing wolflink entity IDs to one unified prefix."""
    entity_registry = er.async_get(hass)
    prefix = _entity_prefix_for_entry(entry)

    for registry_entry in er.async_entries_for_config_entry(
        entity_registry, entry.entry_id
    ):
        if "." not in registry_entry.entity_id:
            continue

        domain, object_id = registry_entry.entity_id.split(".", maxsplit=1)
        new_object_id = _prefixed_object_id(prefix, object_id)
        new_entity_id = f"{domain}.{new_object_id}"
        if new_entity_id == registry_entry.entity_id:
            continue

        try:
            entity_registry.async_update_entity(
                registry_entry.entity_id,
                new_entity_id=new_entity_id,
            )
            _LOGGER.debug(
                "Migrated entity_id from %s to %s",
                registry_entry.entity_id,
                new_entity_id,
            )
        except ValueError:
            _LOGGER.warning(
                "Could not migrate entity_id from %s to %s because target already exists",
                registry_entry.entity_id,
                new_entity_id,
            )


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
    if entry.version == 1 and entry.minor_version < 2:
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

    if entry.version == 1 and entry.minor_version < 3:
        _migrate_entity_prefixes(hass, entry)
        hass.config_entries.async_update_entry(entry, minor_version=3)

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
