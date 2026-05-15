"""The Wolf SmartSet selects."""

import logging
import re

from httpx import RequestError
from wolf_comm.models import ListItemParameter
from wolf_comm.token_auth import InvalidAuth
from wolf_comm.wolf_client import ParameterWriteError, WriteFailed

from homeassistant.components.select import SelectEntity
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


def _display_name(parameter: ListItemParameter) -> str:
    """Return display name for parameter."""
    return f"{parameter.parent} {parameter.name}".strip()


def _is_expert_parameter(parameter: ListItemParameter) -> bool:
    """Return true if parameter looks like expert/installer level."""
    combined = f"{parameter.parent} {parameter.name}".casefold()
    return (
        "fachmann" in combined
        or "expert" in combined
        or "installer" in combined
        or "service" in combined
    )


def _select_preferred_parameter(
    group: list[ListItemParameter],
) -> ListItemParameter:
    """Pick the best candidate from duplicate parameters."""
    return sorted(
        group,
        key=lambda parameter: (
            _is_expert_parameter(parameter),  # Prefer non-expert variant.
            0 if str(parameter.bundle_id).isdigit() else 1,
            parameter.parameter_id,
        ),
    )[0]


def _is_program_select(parameter: ListItemParameter) -> bool:
    """Return if parameter is a writable program selection."""
    if parameter.read_only:
        return False

    combined = f"{parameter.parent} {parameter.name}"
    combined_lower = combined.casefold()
    words = _normalize_words(combined)

    is_heating_or_warmwater = (
        "heizung" in combined_lower
        or "warmwasser" in combined_lower
        or "heating" in words
        or "dhw" in words
    )
    is_program_choice = (
        "programmwahl" in combined_lower
        or "zeitprogramm" in combined_lower
        or "time program" in combined_lower
        or ("program" in words and ("choice" in words or "selection" in words))
    )
    return is_heating_or_warmwater and is_program_choice


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: WolflinkConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up selectable program parameters."""
    coordinator = config_entry.runtime_data
    raw_parameters = [
        parameter
        for parameter in coordinator.parameters
        if isinstance(parameter, ListItemParameter) and _is_program_select(parameter)
    ]

    grouped_by_name: dict[str, list[ListItemParameter]] = {}
    for parameter in raw_parameters:
        key = _display_name(parameter).casefold()
        grouped_by_name.setdefault(key, []).append(parameter)

    matching_parameters: list[ListItemParameter] = []
    for key, group in grouped_by_name.items():
        selected = _select_preferred_parameter(group)
        if len(group) > 1:
            _LOGGER.debug(
                "Resolved duplicate select '%s' -> parameter_id=%s value_id=%s bundle_id=%s from %s candidates",
                key,
                selected.parameter_id,
                selected.value_id,
                selected.bundle_id,
                len(group),
            )
        matching_parameters.append(selected)

    _LOGGER.debug(
        "Discovered %s selectable program parameters: %s",
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
    async_add_entities(
        [
            WolfLinkProgramSelect(coordinator, parameter, coordinator.device_id)
            for parameter in matching_parameters
        ]
    )


class WolfLinkProgramSelect(CoordinatorEntity[WolfLinkCoordinator], SelectEntity):
    """Writable program select entity."""

    def __init__(
        self,
        coordinator: WolfLinkCoordinator,
        parameter: ListItemParameter,
        device_id: int,
    ) -> None:
        """Initialize select entity."""
        super().__init__(coordinator)
        self.parameter = parameter
        self._attr_name = _display_name(parameter)
        self._attr_unique_id = f"{device_id}:{parameter.parameter_id}:select"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(device_id))},
            configuration_url="https://www.wolf-smartset.com/",
            manufacturer=MANUFACTURER,
        )
        self._option_to_value = {item.name: item.value for item in parameter.items}
        self._value_to_option = {str(item.value): item.name for item in parameter.items}
        self._attr_options = list(dict.fromkeys(item.name for item in parameter.items))
        self._current_option: str | None = None

    @property
    def current_option(self) -> str | None:
        """Return selected option."""
        if self.parameter.parameter_id not in self.coordinator.data:
            return self._current_option

        value_id, raw_value = self.coordinator.data[self.parameter.parameter_id]
        self.parameter.value_id = value_id
        self._current_option = self._value_to_option.get(str(raw_value))
        return self._current_option

    @property
    def extra_state_attributes(self) -> dict[str, int | str]:
        """Return extra attributes."""
        return {
            "parameter_id": self.parameter.parameter_id,
            "value_id": self.parameter.value_id,
            "parent": self.parameter.parent,
        }

    async def async_select_option(self, option: str) -> None:
        """Select new program option."""
        if option not in self._option_to_value:
            raise HomeAssistantError(f"Invalid option '{option}' for {self.name}.")

        mapped_value = self._option_to_value[option]
        try:
            write_value: int | float | str = int(mapped_value)
        except (TypeError, ValueError):
            write_value = str(mapped_value)

        try:
            await self.coordinator.async_write_parameter_value(self.parameter, write_value)
        except InvalidAuth as exception:
            raise HomeAssistantError(
                "Invalid authentication while writing program selection."
            ) from exception
        except (ParameterWriteError, WriteFailed, RequestError) as exception:
            raise HomeAssistantError(
                f"Could not write program selection: {exception}"
            ) from exception

        self._current_option = option
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()
