"""Config flow for Wolf SmartSet Service integration."""

import logging
from typing import Any

from httpcore import ConnectError
import voluptuous as vol
from wolf_comm.models import Device
from wolf_comm.token_auth import InvalidAuth
from wolf_comm.wolf_client import WolfClient

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME

from .rate_limit import async_auth_guard
from .const import DEVICE_GATEWAY, DEVICE_ID, DEVICE_NAME, DOMAIN

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = vol.Schema(
    {vol.Required(CONF_USERNAME): str, vol.Required(CONF_PASSWORD): str}
)


class WolfLinkConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Wolf SmartSet Service."""

    VERSION = 1
    MINOR_VERSION = 2

    fetched_systems: dict[str, Device]

    def __init__(self) -> None:
        """Initialize with empty username and password."""
        self.username: str | None = None
        self.password: str | None = None
        self.fetched_systems = {}

    async def async_step_user(
        self, user_input: dict[str, str] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step to get connection parameters."""
        errors = {}
        if user_input is not None:
            username = user_input[CONF_USERNAME]
            password = user_input[CONF_PASSWORD]
            wolf_client = WolfClient(
                username, password
            )
            try:
                async with async_auth_guard(self.hass, username):
                    systems = await wolf_client.fetch_system_list()
            except ConnectError:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                if not systems:
                    errors["base"] = "no_devices"
                    return self.async_show_form(
                        step_id="user", data_schema=USER_SCHEMA, errors=errors
                    )

                self.fetched_systems = {str(system.id): system for system in systems}
                self.username = username
                self.password = password
                return await self.async_step_device()
        return self.async_show_form(
            step_id="user", data_schema=USER_SCHEMA, errors=errors
        )

    async def async_step_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Allow user to select device from devices connected to specified account."""
        errors: dict[str, str] = {}
        if user_input is not None:
            selected_device_id = user_input[DEVICE_ID]
            system = self.fetched_systems[selected_device_id]
            await self.async_set_unique_id(str(system.id))
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=system.name,
                data={
                    CONF_USERNAME: self.username,
                    CONF_PASSWORD: self.password,
                    DEVICE_NAME: system.name,
                    DEVICE_GATEWAY: system.gateway,
                    DEVICE_ID: system.id,
                },
            )

        data_schema = vol.Schema(
            {
                vol.Required(DEVICE_ID): vol.In(
                    {
                        str(device.id): f"{device.name} ({device.id})"
                        for device in self.fetched_systems.values()
                    }
                )
            }
        )
        return self.async_show_form(
            step_id="device", data_schema=data_schema, errors=errors
        )
