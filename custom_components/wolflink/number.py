"""The Wolf SmartSet numbers."""

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


def _is_warmwater_setpoint(parameter: Parameter) -> bool:
    """Return if parameter is a writable warmwater setpoint."""
    if not isinstance(parameter, Temperature) or parameter.read_only:
        return False

    combined = f"{parameter.parent} {parameter.name}".casefold()
    return "warmwasser" in combined and "solltemperatur" in combined


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: WolflinkConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up all writable warmwater setpoints."""
    coordinator = config_entry.runtime_data

    entities = [
        WolfLinkWarmwaterSetpointNumber(coordinator, parameter, coordinator.device_id)
        for parameter in coordinator.parameters
        if _is_warmwater_setpoint(parameter)
    ]
    async_add_entities(entities)


class WolfLinkWarmwaterSetpointNumber(
    CoordinatorEntity[WolfLinkCoordinator], NumberEntity
):
    """Writable warmwater setpoint number."""

    def __init__(
        self,
        coordinator: WolfLinkCoordinator,
        parameter: Parameter,
        device_id: int,
    ) -> None:
        """Initialize the warmwater setpoint number."""
        super().__init__(coordinator)
        self.parameter = parameter
        self._attr_name = parameter.name
        self._attr_unique_id = f"{device_id}:{parameter.parameter_id}:setpoint"
        self._attr_device_class = NumberDeviceClass.TEMPERATURE
        self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
        self._attr_mode = NumberMode.BOX
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
        write_value = round(value)

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

        await self.coordinator.async_request_refresh()
