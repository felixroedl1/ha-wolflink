"""The Wolf SmartSet buttons."""

import logging
import re

from httpx import RequestError
from wolf_comm.models import ListItemParameter
from wolf_comm.token_auth import InvalidAuth
from wolf_comm.wolf_client import ParameterWriteError, WriteFailed

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import WolflinkConfigEntry, WolfLinkCoordinator

_LOGGER = logging.getLogger(__name__)

_TRIGGER_MARKERS = {"ein", "start", "aktiv", "aktiviert", "ja", "yes", "1"}


def _normalize_words(text: str) -> set[str]:
    """Normalize text to lowercase words."""
    cleaned = re.sub(r"[^a-z0-9]+", " ", text.casefold())
    return {word for word in cleaned.split() if word}


def _is_one_time_hot_water(parameter: ListItemParameter) -> bool:
    """Return if parameter can trigger one-time hot water."""
    if parameter.read_only:
        return False
    combined = f"{parameter.parent} {parameter.name}".casefold()
    return "warmwasser" in combined and "1x" in combined


def _resolve_trigger_value(parameter: ListItemParameter) -> int | str | None:
    """Resolve the write value that triggers the action."""
    for item in parameter.items:
        if _normalize_words(item.name) & _TRIGGER_MARKERS:
            return item.value
    numeric_values: list[int] = []
    for item in parameter.items:
        try:
            numeric_values.append(int(item.value))
        except (TypeError, ValueError):
            continue
    if numeric_values:
        return max(numeric_values)
    return None


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: WolflinkConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one-time hot-water trigger buttons."""
    coordinator = config_entry.runtime_data
    entities: list[WolfLinkOneTimeHotWaterButton] = []
    for parameter in coordinator.parameters:
        if not isinstance(parameter, ListItemParameter) or not _is_one_time_hot_water(parameter):
            continue
        trigger_value = _resolve_trigger_value(parameter)
        if trigger_value is None:
            _LOGGER.debug(
                "Skipping button for parameter_id=%s because trigger value could not be resolved",
                parameter.parameter_id,
            )
            continue
        entities.append(
            WolfLinkOneTimeHotWaterButton(
                coordinator, parameter, coordinator.device_id, trigger_value
            )
        )
    _LOGGER.debug(
        "Discovered %s one-time hot-water button parameters: %s",
        len(entities),
        [
            {
                "name": entity.parameter.name,
                "parent": entity.parameter.parent,
                "parameter_id": entity.parameter.parameter_id,
                "value_id": entity.parameter.value_id,
                "bundle_id": entity.parameter.bundle_id,
                "trigger_value": entity._trigger_value,
            }
            for entity in entities
        ],
    )
    async_add_entities(entities)


class WolfLinkOneTimeHotWaterButton(
    CoordinatorEntity[WolfLinkCoordinator], ButtonEntity
):
    """Button to trigger one-time hot water."""

    def __init__(
        self,
        coordinator: WolfLinkCoordinator,
        parameter: ListItemParameter,
        device_id: int,
        trigger_value: int | str,
    ) -> None:
        """Initialize button entity."""
        super().__init__(coordinator)
        self.parameter = parameter
        self._trigger_value = str(trigger_value)
        self._attr_name = f"{parameter.parent} {parameter.name}"
        self._attr_unique_id = f"{device_id}:{parameter.parameter_id}:button"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(device_id))},
            configuration_url="https://www.wolf-smartset.com/",
            manufacturer=MANUFACTURER,
        )

    @property
    def extra_state_attributes(self) -> dict[str, int | str]:
        """Return extra attributes."""
        return {
            "parameter_id": self.parameter.parameter_id,
            "value_id": self.parameter.value_id,
            "parent": self.parameter.parent,
            "trigger_value": self._trigger_value,
        }

    async def async_press(self) -> None:
        """Trigger one-time hot-water action."""
        try:
            try:
                write_value: int | float | str = int(self._trigger_value)
            except (TypeError, ValueError):
                write_value = self._trigger_value
            await self.coordinator.async_write_parameter_value(self.parameter, write_value)
        except InvalidAuth as exception:
            raise HomeAssistantError(
                "Invalid authentication while triggering 1x hot water."
            ) from exception
        except (ParameterWriteError, WriteFailed, RequestError) as exception:
            raise HomeAssistantError(
                f"Could not trigger 1x hot water: {exception}"
            ) from exception

        await self.coordinator.async_request_refresh()
