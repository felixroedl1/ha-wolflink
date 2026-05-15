"""The Wolf SmartSet switches."""

import logging
import re

from httpx import RequestError
from wolf_comm.models import ListItemParameter
from wolf_comm.token_auth import InvalidAuth
from wolf_comm.wolf_client import ParameterWriteError, WriteFailed

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import WolflinkConfigEntry, WolfLinkCoordinator

_LOGGER = logging.getLogger(__name__)

_ON_MARKERS = {"ein", "on", "aktiv", "aktiviert", "true", "yes", "ja", "1"}
_OFF_MARKERS = {"aus", "off", "deaktiviert", "deaktiv", "false", "no", "nein", "0"}


def _normalize_words(text: str) -> set[str]:
    """Normalize text to lowercase words."""
    cleaned = re.sub(r"[^a-z0-9]+", " ", text.casefold())
    return {word for word in cleaned.split() if word}


def _is_mode_switch(parameter: ListItemParameter) -> bool:
    """Return if parameter is a writable party/holiday mode switch."""
    if parameter.read_only:
        return False

    combined = f"{parameter.parent} {parameter.name}".casefold()
    return "partymodus" in combined or "urlaubsmodus" in combined


def _resolve_on_off_values(
    parameter: ListItemParameter,
) -> tuple[int | str, int | str] | None:
    """Resolve on/off values from list items."""
    on_value: int | str | None = None
    off_value: int | str | None = None
    for item in parameter.items:
        words = _normalize_words(item.name)
        if on_value is None and words & _ON_MARKERS:
            on_value = item.value
        if off_value is None and words & _OFF_MARKERS:
            off_value = item.value

    if on_value is None or off_value is None:
        if len(parameter.items) == 2:
            off_value = parameter.items[0].value
            on_value = parameter.items[1].value
        else:
            return None

    return on_value, off_value


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: WolflinkConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up writable party/holiday mode switches."""
    coordinator = config_entry.runtime_data
    entities: list[WolfLinkModeSwitch] = []
    for parameter in coordinator.parameters:
        if not isinstance(parameter, ListItemParameter) or not _is_mode_switch(parameter):
            continue
        resolved = _resolve_on_off_values(parameter)
        if resolved is None:
            _LOGGER.debug(
                "Skipping switch for parameter_id=%s because on/off values could not be resolved",
                parameter.parameter_id,
            )
            continue
        on_value, off_value = resolved
        entities.append(
            WolfLinkModeSwitch(
                coordinator, parameter, coordinator.device_id, on_value, off_value
            )
        )
    _LOGGER.debug(
        "Discovered %s writable mode switch parameters: %s",
        len(entities),
        [
            {
                "name": entity.parameter.name,
                "parent": entity.parameter.parent,
                "parameter_id": entity.parameter.parameter_id,
                "value_id": entity.parameter.value_id,
                "bundle_id": entity.parameter.bundle_id,
                "on_value": entity._on_value,
                "off_value": entity._off_value,
            }
            for entity in entities
        ],
    )
    async_add_entities(entities)


class WolfLinkModeSwitch(CoordinatorEntity[WolfLinkCoordinator], SwitchEntity):
    """Writable party/holiday mode switch."""

    def __init__(
        self,
        coordinator: WolfLinkCoordinator,
        parameter: ListItemParameter,
        device_id: int,
        on_value: int | str,
        off_value: int | str,
    ) -> None:
        """Initialize switch entity."""
        super().__init__(coordinator)
        self.parameter = parameter
        self._on_value = str(on_value)
        self._off_value = str(off_value)
        self._attr_name = f"{parameter.parent} {parameter.name}"
        self._attr_unique_id = f"{device_id}:{parameter.parameter_id}:switch"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(device_id))},
            configuration_url="https://www.wolf-smartset.com/",
            manufacturer=MANUFACTURER,
        )
        self._is_on: bool | None = None

    @property
    def is_on(self) -> bool | None:
        """Return switch state."""
        if self.parameter.parameter_id not in self.coordinator.data:
            return self._is_on

        value_id, raw_value = self.coordinator.data[self.parameter.parameter_id]
        self.parameter.value_id = value_id
        self._is_on = str(raw_value) == self._on_value
        return self._is_on

    @property
    def extra_state_attributes(self) -> dict[str, int | str]:
        """Return extra attributes."""
        return {
            "parameter_id": self.parameter.parameter_id,
            "value_id": self.parameter.value_id,
            "parent": self.parameter.parent,
            "on_value": self._on_value,
            "off_value": self._off_value,
        }

    async def async_turn_on(self, **kwargs) -> None:
        """Turn mode on."""
        await self._async_set_mode(self._on_value, True)

    async def async_turn_off(self, **kwargs) -> None:
        """Turn mode off."""
        await self._async_set_mode(self._off_value, False)

    async def _async_set_mode(self, target_value: str, target_state: bool) -> None:
        """Write switch value."""
        try:
            try:
                write_value: int | float | str = int(target_value)
            except (TypeError, ValueError):
                write_value = target_value
            await self.coordinator.async_write_parameter_value(self.parameter, write_value)
        except InvalidAuth as exception:
            raise HomeAssistantError(
                "Invalid authentication while writing switch value."
            ) from exception
        except (ParameterWriteError, WriteFailed, RequestError) as exception:
            raise HomeAssistantError(f"Could not write switch value: {exception}") from exception

        self._is_on = target_state
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()
