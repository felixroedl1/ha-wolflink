"""DataUpdateCoordinator for the Wolf SmartSet Service integration."""

from datetime import timedelta
import inspect
import json
import logging
from httpx import RequestError
from wolf_comm.constants import (
    BASE_URL_PORTAL,
    ERROR_CODE,
    ERROR_MESSAGE,
    ERROR_READ_PARAMETER,
    ERROR_TYPE,
)
from wolf_comm.helpers import bearer_header
from wolf_comm.models import Parameter
from wolf_comm.token_auth import InvalidAuth
from wolf_comm.wolf_client import (
    FetchFailed,
    ParameterReadError,
    ParameterWriteError,
    WolfClient,
    WriteFailed,
)

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN
from .rate_limit import async_auth_guard

_LOGGER = logging.getLogger(__name__)
_DEFAULT_WRITE_BUNDLE_ID = 1000

type WolflinkConfigEntry = ConfigEntry[WolfLinkCoordinator]


class WolfLinkCoordinator(DataUpdateCoordinator[dict[int, tuple[int, str]]]):
    """Class to manage fetching Wolf SmartSet data."""

    config_entry: WolflinkConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: WolflinkConfigEntry,
        wolf_client: WolfClient,
        parameters: list[Parameter],
        gateway_id: int,
        device_id: int,
    ) -> None:
        """Initialize the coordinator."""
        coordinator_kwargs = {
            "name": DOMAIN,
            "update_interval": timedelta(seconds=60),
        }
        supports_config_entry = (
            "config_entry"
            in inspect.signature(DataUpdateCoordinator.__init__).parameters
        )
        if supports_config_entry:
            super().__init__(
                hass,
                _LOGGER,
                config_entry=entry,
                **coordinator_kwargs,
            )
        else:
            super().__init__(hass, _LOGGER, **coordinator_kwargs)
        self._wolf_client = wolf_client
        self.parameters = parameters
        self._gateway_id = gateway_id
        self.device_id = device_id
        self._username = entry.data[CONF_USERNAME]
        self._refetch_parameters = False

    async def _async_update_data(self) -> dict[int, tuple[int, str]]:
        """Update all stored entities for Wolf SmartSet."""
        try:
            async with async_auth_guard(self.hass, self._username):
                if not await self._wolf_client.fetch_system_state_list(
                    self.device_id, self._gateway_id
                ):
                    self._refetch_parameters = True
                    raise UpdateFailed(
                        "Could not fetch values from server because device is offline."
                    )
                if self._refetch_parameters:
                    self.parameters = await fetch_parameters(
                        self._wolf_client, self._gateway_id, self.device_id
                    )
                    self._refetch_parameters = False
                values = {
                    v.value_id: v.value
                    for v in await self._wolf_client.fetch_value(
                        self._gateway_id, self.device_id, self.parameters
                    )
                }
            return {
                parameter.parameter_id: (
                    parameter.value_id,
                    values[parameter.value_id],
                )
                for parameter in self.parameters
                if parameter.value_id in values
            }
        except RequestError as exception:
            raise UpdateFailed(
                f"Error communicating with API: {exception}"
            ) from exception
        except FetchFailed as exception:
            raise UpdateFailed(
                f"Could not fetch values from server due to: {exception}"
            ) from exception
        except ParameterReadError as exception:
            self._refetch_parameters = True
            raise UpdateFailed(
                "Could not fetch values for parameter. Refreshing value IDs."
            ) from exception
        except InvalidAuth as exception:
            raise UpdateFailed("Invalid authentication during update.") from exception

    async def async_write_parameter_value(
        self,
        parameter: Parameter,
        value: int | float | str,
        prefer_compat_endpoint: bool = False,
    ) -> None:
        """Write a new value for a parameter."""
        try:
            parameter_bundle_id = int(parameter.bundle_id)
            bundle_candidates = [parameter_bundle_id, _DEFAULT_WRITE_BUNDLE_ID]
        except (TypeError, ValueError):
            bundle_candidates = [_DEFAULT_WRITE_BUNDLE_ID]
        tried_bundles: set[int] = set()
        last_error: Exception | None = None

        for bundle_id in bundle_candidates:
            if bundle_id in tried_bundles:
                continue
            tried_bundles.add(bundle_id)
            strategies = (
                (self._async_write_parameter_value_compat, self._async_write_parameter_value_legacy)
                if prefer_compat_endpoint
                else (self._async_write_parameter_value_legacy, self._async_write_parameter_value_compat)
            )
            for strategy in strategies:
                try:
                    async with async_auth_guard(self.hass, self._username):
                        await strategy(parameter, value, bundle_id)
                    return
                except (ParameterWriteError, WriteFailed, RequestError) as exception:
                    last_error = exception
                    _LOGGER.debug(
                        "Write failed via %s for parameter_id=%s value_id=%s bundle_id=%s: %s",
                        strategy.__name__,
                        parameter.parameter_id,
                        parameter.value_id,
                        bundle_id,
                        exception,
                    )
                    continue

        if last_error is not None:
            raise last_error

    async def _async_write_parameter_value_legacy(
        self,
        parameter: Parameter,
        value: int | float | str,
        bundle_id: int,
    ) -> None:
        """Write parameter using legacy WriteParameterValues endpoint."""
        await self._wolf_client.write_value(
            self._gateway_id,
            self.device_id,
            bundle_id,
            {"ValueId": parameter.value_id, "State": str(value)},
        )

    async def _async_write_parameter_value_compat(
        self,
        parameter: Parameter,
        value: int | float | str,
        bundle_id: int,
    ) -> None:
        """Write parameter using /portal/api/portal/parameters/write endpoint."""
        if (
            self._wolf_client.tokens is None
            or self._wolf_client.tokens.is_expired()
            or self._wolf_client.session_id is None
        ):
            await self._wolf_client._WolfClient__authorize_and_session()

        payload = {
            "SessionId": self._wolf_client.session_id,
            "BundleId": bundle_id,
            "GatewayId": self._gateway_id,
            "SystemId": self.device_id,
            "WriteParameterValues": [
                {
                    "ValueId": parameter.value_id,
                    "ParameterId": parameter.parameter_id,
                    "ParameterName": parameter.name,
                    "Value": str(value),
                }
            ],
            "WaitForResponseTimeout": None,
            "GuiId": None,
        }
        headers = {
            **bearer_header(self._wolf_client.tokens.access_token),
            "Content-Type": "application/json",
        }
        response = await self._wolf_client.client.request(
            "post",
            f"{BASE_URL_PORTAL}/api/portal/parameters/write",
            json=payload,
            headers=headers,
        )
        body_text = response.text or ""
        if response.status_code >= 400:
            raise WriteFailed(
                f"Compat write HTTP {response.status_code}: {body_text[:500]}"
            )

        response_data: dict | list
        try:
            response_data = response.json()
        except json.JSONDecodeError as exception:
            raise WriteFailed(
                f"Compat write returned non-JSON response: {body_text[:500]}"
            ) from exception

        if isinstance(response_data, dict) and (
            ERROR_CODE in response_data or ERROR_TYPE in response_data
        ):
            error_msg = (
                f"Error {response_data.get(ERROR_CODE, '')}: "
                f"{response_data.get(ERROR_MESSAGE, str(response_data))}"
            )
            if (
                ERROR_MESSAGE in response_data
                and response_data[ERROR_MESSAGE] == ERROR_READ_PARAMETER
            ):
                raise ParameterWriteError(error_msg)
            raise WriteFailed(error_msg)


async def fetch_parameters(
    client: WolfClient, gateway_id: int, device_id: int
) -> list[Parameter]:
    """Fetch all available parameters with usage of WolfClient.

    By default Reglertyp entity is removed because API will not provide value for this parameter.
    """
    fetched_parameters = await client.fetch_parameters(gateway_id, device_id)
    return [param for param in fetched_parameters if param.name != "Reglertyp"]
