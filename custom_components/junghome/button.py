"""Platform for button integration."""
from __future__ import annotations
import asyncio
import logging
from datetime import timedelta

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator

from .const import DOMAIN, MANUFACTURER
from . import JunghomeConfigEntry
from .junghome_client import JunghomeGateway

_LOGGER = logging.getLogger(__name__)


class JunghomeHubConfigCoordinator(DataUpdateCoordinator):
    """Jung Home hub configuration update coordinator."""

    def __init__(self, hass: HomeAssistant, ip: str, token: str) -> None:
        """Initialize the coordinator."""
        self.ip = ip
        self.token = token

        super().__init__(
            hass,
            _LOGGER,
            name="Jung Home Hub Config",
            update_interval=timedelta(minutes=5),  # Update every 5 minutes
        )

    async def _async_update_data(self) -> dict:
        """Fetch hub configuration from Jung Home API."""
        try:
            config = await asyncio.wait_for(
                JunghomeGateway.request_hub_config(self.ip, self.token),
                timeout=30.0
            )
            
            if config is None:
                raise Exception("Failed to get hub configuration from Jung Home API")
            
            return config
            
        except asyncio.TimeoutError as err:
            _LOGGER.error("Timeout getting hub config from Jung Home API: %s", err)
            raise Exception("Timeout getting hub config from Jung Home API") from err
        except Exception as err:
            _LOGGER.error("Error getting hub config from Jung Home API: %s", err)
            raise


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: JunghomeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Jung Home hub buttons from a config entry."""
    
    # Get main coordinator to extract IP and token
    main_coordinator = config_entry.runtime_data
    
    # Create hub config coordinator (same as sensor.py)
    hub_coordinator = JunghomeHubConfigCoordinator(
        hass, main_coordinator.ip, main_coordinator.token
    )
    
    # Initial data fetch
    await hub_coordinator.async_config_entry_first_refresh()
    
    # Create button entities
    buttons = [
        JunghomeRefreshButton(hub_coordinator, main_coordinator),
    ]
    
    async_add_entities(buttons)


class JunghomeRefreshButton(CoordinatorEntity, ButtonEntity):
    """Jung Home refresh button entity."""

    def __init__(self, hub_coordinator: JunghomeHubConfigCoordinator, main_coordinator) -> None:
        """Initialize the refresh button."""
        super().__init__(hub_coordinator)
        
        self._main_coordinator = main_coordinator
        self._attr_unique_id = "junghome_hub_refresh"
        self._attr_name = "Refresh All Devices"
        self._attr_icon = "mdi:refresh"
        self._attr_entity_category = EntityCategory.CONFIG

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info - must match exactly with sensor device."""
        config = self.coordinator.data or {}
        return DeviceInfo(
            identifiers={(DOMAIN, "hub")},
            name="Jung Home Gateway",
            model="Gateway",
            manufacturer=MANUFACTURER,
            sw_version=config.get("version_release", "Unknown"),
            serial_number=config.get("system_serial", "Unknown"),
        )

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self._main_coordinator.is_websocket_connected

    async def async_press(self) -> None:
        """Handle button press - refresh all device data."""
        _LOGGER.info("Manual refresh button pressed - refreshing all device data")
        try:
            await self._main_coordinator.async_manual_refresh()
        except Exception as err:
            _LOGGER.error("Manual refresh failed: %s", err)