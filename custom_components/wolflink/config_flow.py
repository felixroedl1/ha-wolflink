"""Config flow for Wolf SmartSet Service integration."""

import logging
from functools import partial
from typing import Any

from httpcore import ConnectError
import voluptuous as vol
from wolf_comm.models import Device
from wolf_comm.token_auth import InvalidAuth
from wolf_comm.wolf_client import WolfClient

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers import selector
from homeassistant.helpers.httpx_client import create_async_httpx_client

from .const import (
    CONF_EXPERT_MODE,
    CONF_EXPERT_PASSWORD,
    DEVICE_GATEWAY,
    DEVICE_ID,
    DEVICE_NAME,
    DOMAIN,
)
from .rate_limit import async_auth_guard

_LOGGER = logging.getLogger(__name__)
_DEFAULT_EXPERT_PIN = "1111"


def _resolve_expert_value(expert_mode: bool, expert_password: str | None) -> bool | str:
    """Build expert mode value for wolf_comm."""
    if not expert_mode:
        return False
    if isinstance(expert_password, str) and expert_password.strip():
        return expert_password.strip()
    return True


def _build_user_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    """Build schema for initial user step."""
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_USERNAME,
                default=defaults.get(CONF_USERNAME, ""),
            ): selector.TextSelector(
                selector.TextSelectorConfig(
                    type=selector.TextSelectorType.TEXT,
                    autocomplete="username",
                )
            ),
            vol.Required(
                CONF_PASSWORD,
                default=defaults.get(CONF_PASSWORD, ""),
            ): selector.TextSelector(
                selector.TextSelectorConfig(
                    type=selector.TextSelectorType.PASSWORD,
                    autocomplete="current-password",
                )
            ),
            vol.Optional(
                CONF_EXPERT_MODE,
                default=defaults.get(CONF_EXPERT_MODE, False),
            ): selector.BooleanSelector(),
        }
    )


def _build_expert_pin_schema(default_pin: str | None = None) -> vol.Schema:
    """Build schema for expert PIN step."""
    return vol.Schema(
        {
            vol.Required(
                CONF_EXPERT_PASSWORD,
                default=(default_pin or _DEFAULT_EXPERT_PIN),
            ): selector.TextSelector(
                selector.TextSelectorConfig(
                    type=selector.TextSelectorType.PASSWORD,
                    autocomplete="one-time-code",
                )
            ),
        }
    )


def _build_options_toggle_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    """Build schema for options toggle step."""
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Optional(
                CONF_EXPERT_MODE,
                default=defaults.get(CONF_EXPERT_MODE, False),
            ): selector.BooleanSelector(),
        }
    )


class WolfLinkConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Wolf SmartSet Service."""

    VERSION = 1
    MINOR_VERSION = 4

    fetched_systems: dict[str, Device]

    def __init__(self) -> None:
        """Initialize with empty credentials."""
        self.username: str | None = None
        self.password: str | None = None
        self.expert_mode: bool = False
        self.expert_password: str = ""
        self.fetched_systems = {}

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Create options flow for this handler."""
        return WolfLinkOptionsFlow(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step to get connection parameters."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self.username = user_input[CONF_USERNAME]
            self.password = user_input[CONF_PASSWORD]
            self.expert_mode = bool(user_input.get(CONF_EXPERT_MODE, False))

            if self.expert_mode:
                if not self.expert_password:
                    self.expert_password = _DEFAULT_EXPERT_PIN
                return await self.async_step_expert_pin()

            self.expert_password = ""
            return await self._async_fetch_systems_and_continue(step_id_on_error="user")

        defaults = {
            CONF_USERNAME: self.username or "",
            CONF_PASSWORD: self.password or "",
            CONF_EXPERT_MODE: self.expert_mode,
        }
        return self.async_show_form(
            step_id="user",
            data_schema=_build_user_schema(defaults),
            errors=errors,
        )

    async def async_step_expert_pin(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle expert PIN input when expert mode is enabled."""
        errors: dict[str, str] = {}
        if user_input is not None:
            pin = str(user_input.get(CONF_EXPERT_PASSWORD) or _DEFAULT_EXPERT_PIN).strip()
            self.expert_password = pin or _DEFAULT_EXPERT_PIN
            return await self._async_fetch_systems_and_continue(
                step_id_on_error="expert_pin"
            )

        default_pin = self.expert_password or _DEFAULT_EXPERT_PIN
        return self.async_show_form(
            step_id="expert_pin",
            data_schema=_build_expert_pin_schema(default_pin),
            errors=errors,
        )

    async def _async_fetch_systems_and_continue(
        self, step_id_on_error: str
    ) -> ConfigFlowResult:
        """Authenticate and fetch systems, then continue with device step."""
        if self.username is None or self.password is None:
            return self.async_show_form(
                step_id="user",
                data_schema=_build_user_schema(
                    {
                        CONF_USERNAME: "",
                        CONF_PASSWORD: "",
                        CONF_EXPERT_MODE: self.expert_mode,
                    }
                ),
                errors={"base": "unknown"},
            )

        errors: dict[str, str] = {}
        expert_value = _resolve_expert_value(self.expert_mode, self.expert_password)
        httpx_client = await self.hass.async_add_executor_job(
            partial(
                create_async_httpx_client,
                hass=self.hass,
                verify_ssl=False,
                timeout=20,
            )
        )
        wolf_client = WolfClient(
            self.username,
            self.password,
            expert_p=expert_value,
            client=httpx_client,
        )

        try:
            async with async_auth_guard(self.hass, self.username):
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
            else:
                self.fetched_systems = {str(system.id): system for system in systems}
                return await self.async_step_device()

        if step_id_on_error == "expert_pin":
            return self.async_show_form(
                step_id="expert_pin",
                data_schema=_build_expert_pin_schema(
                    self.expert_password or _DEFAULT_EXPERT_PIN
                ),
                errors=errors,
            )

        return self.async_show_form(
            step_id="user",
            data_schema=_build_user_schema(
                {
                    CONF_USERNAME: self.username,
                    CONF_PASSWORD: self.password,
                    CONF_EXPERT_MODE: self.expert_mode,
                }
            ),
            errors=errors,
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
                    CONF_EXPERT_MODE: self.expert_mode,
                    CONF_EXPERT_PASSWORD: self.expert_password,
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


class WolfLinkOptionsFlow(OptionsFlow):
    """Options flow for wolflink."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry
        self.expert_mode = bool(
            self._config_entry.options.get(
                CONF_EXPERT_MODE,
                self._config_entry.data.get(CONF_EXPERT_MODE, False),
            )
        )
        stored_pin = self._config_entry.options.get(
            CONF_EXPERT_PASSWORD,
            self._config_entry.data.get(CONF_EXPERT_PASSWORD, ""),
        )
        self.expert_password = str(stored_pin or _DEFAULT_EXPERT_PIN)

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage wolflink options."""
        if user_input is not None:
            self.expert_mode = bool(user_input.get(CONF_EXPERT_MODE, False))
            if self.expert_mode:
                return await self.async_step_expert_pin()

            return self.async_create_entry(
                title="",
                data={
                    CONF_EXPERT_MODE: False,
                    CONF_EXPERT_PASSWORD: "",
                },
            )

        return self.async_show_form(
            step_id="init",
            data_schema=_build_options_toggle_schema(
                {CONF_EXPERT_MODE: self.expert_mode}
            ),
            errors={},
        )

    async def async_step_expert_pin(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle expert PIN option input."""
        if user_input is not None:
            pin = str(user_input.get(CONF_EXPERT_PASSWORD) or _DEFAULT_EXPERT_PIN).strip()
            self.expert_password = pin or _DEFAULT_EXPERT_PIN
            return self.async_create_entry(
                title="",
                data={
                    CONF_EXPERT_MODE: True,
                    CONF_EXPERT_PASSWORD: self.expert_password,
                },
            )

        return self.async_show_form(
            step_id="expert_pin",
            data_schema=_build_expert_pin_schema(self.expert_password),
            errors={},
        )
