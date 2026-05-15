"""The Wolf SmartSet numbers."""

import re
import logging

from httpx import RequestError
from wolf_comm.models import Parameter, Temperature
from wolf_comm.token_auth import InvalidAuth
from wolf_comm.wolf_client import ParameterWriteError, WriteFailed

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import WolflinkConfigEntry, WolfLinkCoordinator

_LOGGER = logging.getLogger(__name__)


def _normalize_words(text: str) -> set[str]:
    """Normalize text to lowercase words."""
    cleaned = re.sub(r"[^a-z0-9]+", " ", text.casefold())
    return {word for word in cleaned.split() if word}


def _is_warmwater_setpoint(parameter: Parameter) -> bool:
    """Return if parameter is a writable warmwater setpoint."""
    if not isinstance(parameter, Temperature) or parameter.read_only:
        return False

    combined = f"{parameter.parent} {parameter.name}"
    words = _normalize_words(combined)
    combined_lower = combined.casefold()

    has_warmwater = (
        "warmwasser" in combined_lower
        or "trinkwasser" in combined_lower
        or "dhw" in words
        or ("ww" in words and "t" in words)
    )
    has_setpoint = (
        "solltemperatur" in combined_lower
        or "setpoint" in words
        or ("soll" in words and ("temperatur" in words or "temp" in words or "t" in words))
        or ("set" in words and ("temp" in words or "temperature" in words))
        or ("target" in words and ("temp" in words or "temperature" in words))
    )
    return has_warmwater and has_setpoint


def _is_heating_setpoint_correction(parameter: Parameter) -> bool:
    """Return if parameter is a writable heating setpoint correction."""
    if not isinstance(parameter, Temperature) or parameter.read_only:
        return False
    combined = f"{parameter.parent} {parameter.name}".casefold()
    return "heizung" in combined and "sollwertkorrektur" in combined


def _display_name(parameter: Parameter) -> str:
    """Return display name for parameter."""
    return f"{parameter.parent} {parameter.name}".strip()


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: WolflinkConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up all writable warmwater setpoints."""
    coordinator = config_entry.runtime_data

    raw_parameters = [
        parameter
        for parameter in coordinator.parameters
        if _is_warmwater_setpoint(parameter)
        or _is_heating_setpoint_correction(parameter)
    ]

    deduplicated_by_name: dict[str, Parameter] = {}
    for parameter in raw_parameters:
        key = _display_name(parameter).casefold()
        if key in deduplicated_by_name:
            existing = deduplicated_by_name[key]
            _LOGGER.debug(
                "Skipping duplicate writable number '%s' (parameter_id=%s, value_id=%s, bundle_id=%s); "
                "already mapped to parameter_id=%s, value_id=%s, bundle_id=%s",
                _display_name(parameter),
                parameter.parameter_id,
                parameter.value_id,
                parameter.bundle_id,
                existing.parameter_id,
                existing.value_id,
                existing.bundle_id,
            )
            continue
        deduplicated_by_name[key] = parameter

    matching_parameters = list(deduplicated_by_name.values())
    _LOGGER.debug(
        "Discovered %s writable warmwater setpoint parameters: %s",
        len(matching_parameters),
        [
            {
                "name": parameter.name,
                "parent": parameter.parent,
                "parameter_id": parameter.parameter_id,
                "value_id": parameter.value_id,
                "bundle_id": parameter.bundle_id,
            }
            for parameter in matching_parameters
        ],
    )

    entities = [
        WolfLinkWarmwaterSetpointNumber(coordinator, parameter, coordinator.device_id)
        for parameter in matching_parameters
    ]
    async_add_entities(entities)


class WolfLinkWarmwaterSetpointNumber(
    CoordinatorEntity[WolfLinkCoordinator], NumberEntity
):
    """Writable Wolf setpoint number."""

    def __init__(
        self,
        coordinator: WolfLinkCoordinator,
        parameter: Parameter,
        device_id: int,
    ) -> None:
        """Initialize the warmwater setpoint number."""
        super().__init__(coordinator)
        self.parameter = parameter
        self._is_heating_correction = _is_heating_setpoint_correction(parameter)
        self._attr_name = _display_name(parameter)
        self._attr_unique_id = f"{device_id}:{parameter.parameter_id}:setpoint"
        self._attr_device_class = NumberDeviceClass.TEMPERATURE
        self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
        self._attr_mode = NumberMode.BOX
        if self._is_heating_correction:
            self._attr_native_min_value = -10
            self._attr_native_max_value = 10
            self._attr_native_step = 0.5
        else:
            self._attr_native_min_value = 20
            self._attr_native_max_value = 75
            self._attr_native_step = 1
        self._value: float | None = None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(device_id))},
            configuration_url="https://www.wolf-smartset.com/",
            manufacturer=MANUFACTURER,
        )

    def _update_current_value(self) -> None:
        """Update locally cached value from coordinator data."""
        if self.parameter.parameter_id not in self.coordinator.data:
            return

        value_id, raw_value = self.coordinator.data[self.parameter.parameter_id]
        self.parameter.value_id = value_id

        try:
            self._value = float(raw_value)
        except (TypeError, ValueError):
            return

    @property
    def native_value(self) -> float | None:
        """Return current setpoint value."""
        self._update_current_value()
        return self._value

    @property
    def extra_state_attributes(self) -> dict[str, int | str]:
        """Return extra attributes."""
        return {
            "parameter_id": self.parameter.parameter_id,
            "value_id": self.parameter.value_id,
            "parent": self.parameter.parent,
        }

    async def async_set_native_value(self, value: float) -> None:
        """Set a new warmwater setpoint."""
        write_value: int | float
        if self._is_heating_correction:
            write_value = round(value, 1)
        else:
            write_value = round(value)
        if self.parameter.value_id is None:
            raise HomeAssistantError("No value_id available for warmwater setpoint.")

        try:
            await self.coordinator.async_write_parameter_value(
                self.parameter, write_value
            )
        except InvalidAuth as exception:
            raise HomeAssistantError(
                "Invalid authentication while writing warmwater setpoint."
            ) from exception
        except (ParameterWriteError, WriteFailed, RequestError) as exception:
            raise HomeAssistantError(
                f"Could not write warmwater setpoint: {exception}"
            ) from exception

        self._value = float(write_value)
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()
