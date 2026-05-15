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


def _normalize_label(text: str | None) -> str:
    """Normalize text for robust label/signature comparisons."""
    if text is None:
        return ""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text.casefold())).strip()


def _display_name(parameter: ListItemParameter) -> str:
    """Return display name for parameter."""
    parent = str(parameter.parent).strip() if parameter.parent is not None else ""
    if parent.casefold() == "none":
        parent = ""
    return f"{parent} {parameter.name}".strip()


def _parameter_context(parameter: ListItemParameter) -> str:
    """Return rough context for select parameters."""
    combined = f"{parameter.parent or ''} {parameter.name}"
    combined_lower = combined.casefold()
    words = _normalize_words(combined)

    if (
        "warmwasser" in combined_lower
        or "trinkwasser" in combined_lower
        or "dhw" in words
        or ("hot" in words and "water" in words)
    ):
        return "warmwasser"
    if "heizung" in combined_lower or "heating" in words or "heizkreis" in combined_lower:
        return "heizung"
    return "unknown"


def _option_signature(parameter: ListItemParameter) -> tuple[str, ...]:
    """Return normalized option signature."""
    options = (_normalize_label(item.name) for item in parameter.items)
    return tuple(sorted(option for option in options if option))


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


def _is_program_select(parameter: ListItemParameter) -> bool:
    """Return if parameter is a writable program selection."""
    if parameter.read_only:
        return False

    combined = f"{parameter.parent} {parameter.name}"
    combined_lower = combined.casefold()
    words = _normalize_words(combined)
    option_words = _normalize_words(" ".join(item.name for item in parameter.items))

    has_heating_or_warmwater_context = (
        "heizung" in combined_lower
        or "warmwasser" in combined_lower
        or "trinkwasser" in combined_lower
        or "heating" in words
        or "dhw" in words
        or ("hot" in words and "water" in words)
    )
    has_program_wording = (
        "programmwahl" in combined_lower
        or "zeitprogramm" in combined_lower
        or "time program" in combined_lower
        or "prog. select" in combined_lower
        or "prog select" in combined_lower
        or "program selector" in combined_lower
        or ("program" in words and ("choice" in words or "selection" in words))
    )

    has_time_program_options = (
        ("zeitprogramm" in option_words or ("time" in option_words and "program" in option_words))
        and any(token in option_words for token in {"1", "2", "3"})
    )
    has_mode_options = (
        ("auto" in option_words or "automatic" in option_words)
        and (
            "standby" in option_words
            or "permanent" in option_words
            or "sparen" in option_words
            or "economy" in option_words
        )
    )

    return (
        (has_program_wording and has_heating_or_warmwater_context)
        or (has_program_wording and (has_time_program_options or has_mode_options))
        or (has_heating_or_warmwater_context and (has_time_program_options or has_mode_options))
    )


def _is_time_program_select(parameter: ListItemParameter) -> bool:
    """Return true if the select is a time-program selector."""
    combined = f"{parameter.parent or ''} {parameter.name}"
    combined_lower = combined.casefold()
    words = _normalize_words(combined)
    option_words = _normalize_words(" ".join(item.name for item in parameter.items))

    has_time_program_name = (
        "zeitprogramm" in combined_lower
        or "time program" in combined_lower
        or ("time" in words and "program" in words)
    )
    has_time_program_options = (
        ("zeitprogramm" in option_words or ("time" in option_words and "program" in option_words))
        and any(token in option_words for token in {"1", "2", "3"})
    )
    return has_time_program_name or has_time_program_options


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: WolflinkConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up selectable program parameters."""
    coordinator = config_entry.runtime_data

    for parameter in coordinator.parameters:
        combined = f"{parameter.parent} {parameter.name}".casefold()
        if not any(
            marker in combined
            for marker in (
                "programm",
                "program",
                "zeitprogramm",
                "time program",
                "heizung",
                "warmwasser",
                "dhw",
            )
        ):
            continue
        if not isinstance(parameter, ListItemParameter):
            _LOGGER.debug(
                "Program-like parameter is not a ListItemParameter and will be skipped: "
                "name=%s parent=%s parameter_id=%s value_id=%s type=%s read_only=%s",
                parameter.name,
                parameter.parent,
                parameter.parameter_id,
                parameter.value_id,
                type(parameter).__name__,
                parameter.read_only,
            )
            continue
        if not _is_program_select(parameter):
            _LOGGER.debug(
                "Program-like ListItemParameter filtered out: name=%s parent=%s parameter_id=%s value_id=%s read_only=%s options=%s",
                parameter.name,
                parameter.parent,
                parameter.parameter_id,
                parameter.value_id,
                parameter.read_only,
                [item.name for item in parameter.items],
            )

    raw_parameters = []
    for parameter in coordinator.parameters:
        if not isinstance(parameter, ListItemParameter):
            continue
        if not _is_program_select(parameter):
            continue
        if _is_time_program_select(parameter):
            _LOGGER.debug(
                "Skipping time-program select parameter: name=%s parent=%s parameter_id=%s value_id=%s",
                parameter.name,
                parameter.parent,
                parameter.parameter_id,
                parameter.value_id,
            )
            continue
        raw_parameters.append(parameter)

    specific_signatures = {
        _option_signature(parameter)
        for parameter in raw_parameters
        if _parameter_context(parameter) != "unknown"
    }

    filtered_parameters: list[ListItemParameter] = []
    for parameter in raw_parameters:
        context = _parameter_context(parameter)
        signature = _option_signature(parameter)
        if context == "unknown" and signature in specific_signatures:
            _LOGGER.debug(
                "Skipping generic select duplicate: name=%s parent=%s parameter_id=%s value_id=%s options=%s",
                parameter.name,
                parameter.parent,
                parameter.parameter_id,
                parameter.value_id,
                [item.name for item in parameter.items],
            )
            continue
        filtered_parameters.append(parameter)

    grouped_by_name: dict[str, list[ListItemParameter]] = {}
    for parameter in filtered_parameters:
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
            WolfLinkProgramSelect(
                coordinator,
                _select_preferred_parameter(group),
                _sorted_candidates(group),
                coordinator.device_id,
            )
            for group in grouped_by_name.values()
        ]
    )


class WolfLinkProgramSelect(CoordinatorEntity[WolfLinkCoordinator], SelectEntity):
    """Writable program select entity."""
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: WolfLinkCoordinator,
        parameter: ListItemParameter,
        candidates: list[ListItemParameter],
        device_id: int,
    ) -> None:
        """Initialize select entity."""
        super().__init__(coordinator)
        self.parameter = parameter
        self._candidates = candidates
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
        for candidate in self._candidates:
            if candidate.parameter_id not in self.coordinator.data:
                continue

            value_id, raw_value = self.coordinator.data[candidate.parameter_id]
            candidate.value_id = value_id
            value_to_option = {str(item.value): item.name for item in candidate.items}
            resolved_option = value_to_option.get(str(raw_value))
            if resolved_option is not None:
                self.parameter = candidate
                self._option_to_value = {item.name: item.value for item in candidate.items}
                self._value_to_option = value_to_option
                self._attr_options = list(dict.fromkeys(item.name for item in candidate.items))
                self._current_option = resolved_option
                return self._current_option
        return self._current_option

    @property
    def extra_state_attributes(self) -> dict[str, int | str]:
        """Return extra attributes."""
        return {
            "parameter_id": self.parameter.parameter_id,
            "value_id": self.parameter.value_id,
            "parent": self.parameter.parent,
            "candidate_parameter_ids": [candidate.parameter_id for candidate in self._candidates],
        }

    async def async_select_option(self, option: str) -> None:
        """Select new program option."""
        last_exception: Exception | None = None
        has_option_candidate = False

        for candidate in self._candidates:
            option_to_value = {item.name: item.value for item in candidate.items}
            if option not in option_to_value:
                continue

            has_option_candidate = True
            if candidate.parameter_id in self.coordinator.data:
                value_id, _ = self.coordinator.data[candidate.parameter_id]
                candidate.value_id = value_id
            mapped_value = option_to_value[option]
            try:
                write_value: int | float | str = int(mapped_value)
            except (TypeError, ValueError):
                write_value = str(mapped_value)

            try:
                await self.coordinator.async_write_parameter_value(
                    candidate,
                    write_value,
                    prefer_compat_endpoint=True,
                )
            except InvalidAuth as exception:
                raise HomeAssistantError(
                    "Invalid authentication while writing program selection."
                ) from exception
            except (ParameterWriteError, WriteFailed, RequestError) as exception:
                last_exception = exception
                _LOGGER.debug(
                    "Write failed for select '%s' candidate parameter_id=%s bundle_id=%s: %s",
                    self.name,
                    candidate.parameter_id,
                    candidate.bundle_id,
                    exception,
                )
                continue

            self.parameter = candidate
            self._option_to_value = option_to_value
            self._value_to_option = {str(item.value): item.name for item in candidate.items}
            self._attr_options = list(dict.fromkeys(item.name for item in candidate.items))
            self._current_option = option
            self.async_write_ha_state()
            await self.coordinator.async_request_refresh()
            return

        if not has_option_candidate:
            raise HomeAssistantError(f"Invalid option '{option}' for {self.name}.")
        if last_exception is not None:
            raise HomeAssistantError(
                f"Could not write program selection: {last_exception}"
            ) from last_exception
