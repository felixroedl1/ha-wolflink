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
    return _sorted_candidates(group)[0]


def _sorted_candidates(
    parameters: list[ListItemParameter],
) -> list[ListItemParameter]:
    """Return candidates sorted by preference."""
    return sorted(
        parameters,
        key=lambda parameter: (
            _is_expert_parameter(parameter),  # Prefer non-expert variant.
            0 if str(parameter.bundle_id).isdigit() else 1,
            parameter.parameter_id,
        ),
    )


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

    raw_parameters = [
        parameter
        for parameter in coordinator.parameters
        if isinstance(parameter, ListItemParameter) and _is_one_time_hot_water(parameter)
    ]

    grouped_by_name: dict[str, list[ListItemParameter]] = {}
    for parameter in raw_parameters:
        key = _display_name(parameter).casefold()
        grouped_by_name.setdefault(key, []).append(parameter)

    entities: list[WolfLinkOneTimeHotWaterButton] = []
    for key, group in grouped_by_name.items():
        parameter = _select_preferred_parameter(group)
        candidates = _sorted_candidates(group)
        if len(group) > 1:
            _LOGGER.debug(
                "Resolved duplicate button '%s' -> parameter_id=%s value_id=%s bundle_id=%s from %s candidates",
                key,
                parameter.parameter_id,
                parameter.value_id,
                parameter.bundle_id,
                len(group),
            )
        trigger_value = _resolve_trigger_value(parameter)
        if trigger_value is None:
            _LOGGER.debug(
                "Skipping button for parameter_id=%s because trigger value could not be resolved",
                parameter.parameter_id,
            )
            continue
        entities.append(
            WolfLinkOneTimeHotWaterButton(
                coordinator, parameter, candidates, coordinator.device_id, trigger_value
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
        candidates: list[ListItemParameter],
        device_id: int,
        trigger_value: int | str,
    ) -> None:
        """Initialize button entity."""
        super().__init__(coordinator)
        self.parameter = parameter
        self._candidates = candidates
        self._trigger_value = str(trigger_value)
        self._attr_name = _display_name(parameter)
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
            "candidate_parameter_ids": [candidate.parameter_id for candidate in self._candidates],
        }

    async def async_press(self) -> None:
        """Trigger one-time hot-water action."""
        last_exception: Exception | None = None
        tried_candidate = False

        for candidate in self._candidates:
            trigger_value = _resolve_trigger_value(candidate)
            if trigger_value is None:
                continue
            tried_candidate = True
            if candidate.parameter_id in self.coordinator.data:
                value_id, _ = self.coordinator.data[candidate.parameter_id]
                candidate.value_id = value_id

            try:
                write_value: int | float | str = int(trigger_value)
            except (TypeError, ValueError):
                write_value = str(trigger_value)

            try:
                await self.coordinator.async_write_parameter_value(candidate, write_value)
            except InvalidAuth as exception:
                raise HomeAssistantError(
                    "Invalid authentication while triggering 1x hot water."
                ) from exception
            except (ParameterWriteError, WriteFailed, RequestError) as exception:
                last_exception = exception
                _LOGGER.debug(
                    "Write failed for button '%s' candidate parameter_id=%s bundle_id=%s: %s",
                    self.name,
                    candidate.parameter_id,
                    candidate.bundle_id,
                    exception,
                )
                continue

            self.parameter = candidate
            self._trigger_value = str(trigger_value)
            await self.coordinator.async_request_refresh()
            return

        if not tried_candidate:
            raise HomeAssistantError("No writable candidate found for 1x hot water.")
        if last_exception is not None:
            raise HomeAssistantError(
                f"Could not trigger 1x hot water: {last_exception}"
            ) from last_exception
