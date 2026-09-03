"""Platform for sensor integration."""
from __future__ import annotations
import logging

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from . import JunghomeConfigEntry
from .coordinator import JunghomeHubConfigCoordinator
from .datapoints import extract_quantity_label_unit, iter_datapoints_by_type
from .entity import JunghomeDeviceEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: JunghomeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Jung Home sensors from a config entry."""
    coordinator = config_entry.runtime_data
    _LOGGER.info("Initialize Jung Home sensors from coordinator")
    created_sensor_ids: set[str] = set()

    # Shared with binary_sensor; already refreshed during coordinator setup.
    hub_coordinator = coordinator.hub_config
    async_add_entities(
        [
            JunghomeCloudStateSensor(hub_coordinator),
            JunghomeIPAddressSensor(hub_coordinator),
            JunghomeVersionSensor(hub_coordinator),
        ]
    )

    async def add_new_sensors(devices):
        """Add new energy sensor devices dynamically."""
        sensors = []
        for device in devices:
            _LOGGER.debug("Processing device %s: %s", device["id"], device)
            if device["type"] not in ["Socket", "SocketEnergy"]:
                continue

            for datapoint in iter_datapoints_by_type(device, "quantity"):
                datapoint_id = datapoint.get("id")
                unique_id = f"{device['id']}_{datapoint_id}"
                if not datapoint_id or unique_id in created_sensor_ids:
                    continue

                quantity_label, quantity_unit = extract_quantity_label_unit(
                    datapoint.get("values", [])
                )

                if quantity_label and quantity_unit:
                    sensor_type = coordinator._map_quantity_label_to_sensor_type(quantity_label)
                    if sensor_type:
                        sensors.append(
                            JunghomeEnergySensor(
                                coordinator,
                                device,
                                datapoint_id,
                                sensor_type,
                                quantity_label,
                                quantity_unit,
                            )
                        )
                        created_sensor_ids.add(unique_id)
                        _LOGGER.debug(
                            "Creating sensor for device %s: %s (label: %s, unit: %s)",
                            device["id"],
                            sensor_type,
                            quantity_label,
                            quantity_unit,
                        )

        if sensors:
            _LOGGER.info("Adding %d new sensor entities", len(sensors))
            async_add_entities(sensors)

    coordinator.register_entity_callback("sensor", add_new_sensors)

    if coordinator.data is None or "devices" not in coordinator.data:
        _LOGGER.warning("No device data available from coordinator")
        return

    devices = coordinator.data["devices"]
    _LOGGER.debug("Setting up sensors for devices: %s", devices)
    await add_new_sensors(devices)


class JunghomeEnergySensor(JunghomeDeviceEntity, SensorEntity):
    """Jung Home energy sensor entity."""

    SENSOR_TYPES = {
        "sensor_device_input_power": ("Present Device Input Power", SensorDeviceClass.POWER),
        "sensor_active_power_loadside": ("Active Power Loadside", SensorDeviceClass.POWER),
    }

    def __init__(
        self,
        coordinator,
        device,
        datapoint_id,
        sensor_type,
        quantity_label,
        quantity_unit,
    ) -> None:
        """Initialize a Jung Home energy sensor."""
        super().__init__(coordinator)
        _LOGGER.debug("Initializing energy sensor for device %s, type %s, quantity_label: %s, quantity_unit: %s", device["id"], sensor_type, quantity_label, quantity_unit)
        self._device_id = device["id"]
        self._datapoint_id = datapoint_id
        self._sensor_type = sensor_type
        self._quantity_label_display = quantity_label.strip()
        # Per JUNG HOME documentation, device_id is unique across installations and device resets.
        self._attr_unique_id = f"{self._device_id}_{self._datapoint_id}"
        self._attr_name = f"{device['label']} {self._quantity_label_display}"
        _LOGGER.debug("_attr_device_class: %s", self.SENSOR_TYPES[sensor_type])
        self._attr_device_class = self.SENSOR_TYPES[sensor_type][1]
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = quantity_unit

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        device = self._get_device()
        if device:
            label = device.get("label")
            if label:
                new_name = f"{label} {self._quantity_label_display}"
                if new_name != self._attr_name:
                    self._attr_name = new_name
            self.coordinator.apply_device_area(device)
        self.async_write_ha_state()

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        return self._build_device_info(self._get_device().get("type", "Unknown"))

    @property
    def native_value(self):
        """Return the current value of the sensor."""
        device = self._get_device()
        if device:
            states = device.get("states", {})
            value = states.get(self._sensor_type, {}).get("value", 0)
            _LOGGER.debug("Sensor %s value: %s", self._attr_unique_id, value)
            return value
        return 0

    @property
    def native_unit_of_measurement(self):
        """Return the unit of measurement."""
        return self._attr_native_unit_of_measurement


class JunghomeHubSensorBase(CoordinatorEntity, SensorEntity):
    """Base class for Jung Home hub sensors."""

    def __init__(self, coordinator: JunghomeHubConfigCoordinator, sensor_type: str, name: str) -> None:
        """Initialize a Jung Home hub sensor."""
        super().__init__(coordinator)
        
        self._sensor_type = sensor_type
        self._attr_unique_id = f"junghome_hub_{sensor_type}"
        self._attr_name = name

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        config = self.coordinator.data or {}
        hub_id = config.get("ip_address") or "hub"
        return DeviceInfo(
            identifiers={(DOMAIN, hub_id)},
            name="Jung Home Gateway",
            model="Gateway",
            manufacturer=MANUFACTURER,
            sw_version=config.get("version_release", "Unknown"),
            serial_number=config.get("system_serial", "Unknown"),
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()


class JunghomeCloudStateSensor(JunghomeHubSensorBase):
    """Jung Home cloud state sensor."""

    def __init__(self, coordinator: JunghomeHubConfigCoordinator) -> None:
        """Initialize the cloud state sensor."""
        super().__init__(coordinator, "cloud_state", "Cloud State")

    @property
    def native_value(self) -> str:
        """Return the state of the sensor."""
        config = self.coordinator.data or {}
        if config.get("cloud_register", False):
            return "registered"
        return "not_registered"

    @property
    def extra_state_attributes(self) -> dict:
        """Return the state attributes."""
        config = self.coordinator.data or {}
        return {
            "cloud_username": config.get("cloud_username", ""),
            "cloud_connect": config.get("cloud_connect", False),
            "cloud_register": config.get("cloud_register", False),
        }

    @property
    def icon(self) -> str:
        """Return the icon for the sensor."""
        config = self.coordinator.data or {}
        if config.get("cloud_register", False):
            return "mdi:cloud-check"
        return "mdi:cloud-off"




class JunghomeIPAddressSensor(JunghomeHubSensorBase):
    """Jung Home IP address sensor."""

    def __init__(self, coordinator: JunghomeHubConfigCoordinator) -> None:
        """Initialize the IP address sensor."""
        super().__init__(coordinator, "ip_address", "IP Address")

    @property
    def native_value(self) -> str:
        """Return the state of the sensor."""
        config = self.coordinator.data or {}
        return config.get("ip_address", "Unknown")

    @property
    def extra_state_attributes(self) -> dict:
        """Return the state attributes."""
        config = self.coordinator.data or {}
        return {
            "dhcp": config.get("ip_dhcp", False),
            "subnet": config.get("ip_subnet", ""),
            "dns": config.get("ip_dns", ""),
            "gateway": config.get("ip_gateway", ""),
        }

    @property
    def icon(self) -> str:
        """Return the icon for the sensor."""
        return "mdi:ip-network"


class JunghomeVersionSensor(JunghomeHubSensorBase):
    """Jung Home version sensor."""

    def __init__(self, coordinator: JunghomeHubConfigCoordinator) -> None:
        """Initialize the version sensor."""
        super().__init__(coordinator, "version", "Version")

    @property
    def native_value(self) -> str:
        """Return the state of the sensor."""
        config = self.coordinator.data or {}
        return config.get("version_release", "Unknown")

    @property
    def extra_state_attributes(self) -> dict:
        """Return the state attributes."""
        config = self.coordinator.data or {}
        return {
            "build": config.get("version_build", ""),
            "is_beta": config.get("version_beta", False),
            "smartphone_version": config.get("version_smartphone", "0"),
        }

    @property
    def icon(self) -> str:
        """Return the icon for the sensor."""
        return "mdi:information"
