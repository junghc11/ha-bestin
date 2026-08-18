"""Sensor platform for BESTIN"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.sensor import (
    DOMAIN as DOMAIN_SENSOR,
)
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    UnitOfEnergy,
    UnitOfPower,
    UnitOfVolume,
    UnitOfVolumeFlowRate,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN, NEW_SENSOR
from .device import BestinDevice
from .hub import BestinHub

if TYPE_CHECKING:
    from .energy_statistics import BestinEnergyStatisticsImporter

HISTORY_ATTRIBUTE_LIMIT = 50

VEHICLE_EVENT_LABELS = {
    "arrival": "입차",
    "departure": "출차",
    "parked": "주차",
    "unknown": "알 수 없음",
}

VISITOR_ACCESS_LABELS = {
    "lobby": "로비",
    "main_gate": "공동현관",
    "unit_entrance": "세대 현관",
    "unknown": "알 수 없음",
}

DEVICE_ICON = {
    "light:dcvalue": "mdi:flash",
    "outlet:powercons": "mdi:flash",
    "electric:realtime": "mdi:flash",
    "electric:total": "mdi:lightning-bolt",
    "gas:realtime": "mdi:gas-cylinder",
    "gas:total": "mdi:gas-cylinder",
    "heat:realtime": "mdi:radiator",
    "heat:total": "mdi:thermometer-lines",
    "hotwater:realtime": "mdi:water-boiler",
    "hotwater:total": "mdi:water-boiler",
    "water:realtime": "mdi:water-pump",
    "water:total": "mdi:water-pump",
}

DEVICE_CLASS = {
    "light:dcvalue": SensorDeviceClass.POWER,
    "outlet:cutvalue": SensorDeviceClass.POWER,
    "outlet:powercons": SensorDeviceClass.POWER,
    "electric:realtime": SensorDeviceClass.POWER,
    "electric:total": SensorDeviceClass.ENERGY,
    "gas:total": SensorDeviceClass.GAS,
    "water:total": SensorDeviceClass.WATER,
}

DEVICE_UNIT = {
    "light:dcvalue": UnitOfPower.WATT,
    "outlet:cutvalue": UnitOfPower.WATT,
    "outlet:powercons": UnitOfPower.WATT,
    "electric:realtime": UnitOfPower.WATT,
    "electric:total": UnitOfEnergy.KILO_WATT_HOUR,
    "gas:realtime": UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR,
    "gas:total": UnitOfVolume.CUBIC_METERS,
    "heat:realtime": UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR,
    "heat:total": UnitOfVolume.CUBIC_METERS,
    "hotwater:realtime": UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR,
    "hotwater:total": UnitOfVolume.CUBIC_METERS,
    "water:realtime": UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR,
    "water:total": UnitOfVolume.CUBIC_METERS,
}

VALUE_CONVERSION = {
    "electric:total": lambda val, _: round(val / 100, 2),
    "gas:total": lambda val, _: round(val / 1000, 2),
    "gas:realtime": lambda val, _: val / 10,
    "heat:total": lambda val, _: round(val / 1000, 2),
    "heat:realtime": lambda val, wp_ver: val if wp_ver == "General" else val / 1000,
    "hotwater:total": lambda val, _: round(val / 1000, 2),
    "hotwater:realtime": lambda val, wp_ver: val if wp_ver == "General" else val / 1000,
    "water:total": lambda val, _: round(val / 1000, 2),
    "water:realtime": lambda val, wp_ver: val if wp_ver == "General" else val / 1000,
}


def extract_and_transform(identifier: str) -> str:
    """Extract and transform the identifier to a formatted string."""
    if "energy_" in identifier:
        extracted_segment = identifier.split("energy_")[1]
    else:
        extracted_segment = ":".join(
            [identifier.split("_")[1], identifier.split("_")[3]]
        )

    transformed_segment = extracted_segment.replace("_", ":")
    return transformed_segment


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> bool:
    """Setup sensor platform."""
    hub: BestinHub = BestinHub.get_hub(hass, entry)
    hub.entity_groups[DOMAIN_SENSOR] = set()

    @callback
    def async_add_sensor(devices=None):
        if devices is None:
            devices = hub.api.get_devices_from_domain(DOMAIN_SENSOR)

        entities = [
            BestinSensor(device, hub)
            for device in devices
            if device.unique_id not in hub.entity_groups[DOMAIN_SENSOR]
        ]

        if entities:
            async_add_entities(entities)

    entry.async_on_unload(
        async_dispatcher_connect(
            hass, hub.async_signal_new_device(NEW_SENSOR), async_add_sensor
        )
    )
    if importer := getattr(hub, "energy_statistics_importer", None):
        async_add_entities(
            [
                BestinRestEnergyPeriodSensor(
                    importer,
                    months_back=0,
                    name="전기 현재 검침기간 사용량",
                    unique_suffix="electricity_billing_cycle_usage",
                ),
                BestinRestEnergyPeriodSensor(
                    importer,
                    months_back=1,
                    name="전기 전월 사용량",
                    unique_suffix="electricity_previous_month_usage",
                ),
                BestinRestEnergyPeriodSensor(
                    importer,
                    months_back=2,
                    name="전기 전전월 사용량",
                    unique_suffix="electricity_month_before_previous_usage",
                ),
            ]
        )
    if community := getattr(hub, "community_api", None):
        async_add_entities(
            [
                BestinCommunityHistorySensor(community, "vehicle"),
                BestinCommunityHistorySensor(community, "visitor"),
                BestinLobbyDoorListSensor(community),
            ]
        )
    async_add_sensor()


class BestinCommunityHistorySensor(SensorEntity):
    """Expose the latest community record and a bounded recent list."""

    _attr_should_poll = False

    def __init__(self, community, kind: str) -> None:
        self.community = community
        self.kind = kind
        self._attr_has_entity_name = True
        self._attr_translation_key = f"latest_{kind}_access"
        self._attr_name = "최근 차량 출입" if kind == "vehicle" else "최근 방문자 출입"
        self._attr_unique_id = f"{community.entry.entry_id}_latest_{kind}_access"
        self._attr_icon = "mdi:car-clock" if kind == "vehicle" else "mdi:account-clock"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"{community.entry.entry_id}_community")},
            "name": "BESTIN 커뮤니티",
            "manufacturer": "HDC Labs",
            "model": "Smart Home 2.0 Community API",
        }

    @property
    def native_value(self) -> str | None:
        """Return the latest access type."""
        records = (
            self.community.vehicle_records
            if self.kind == "vehicle"
            else self.community.visitor_records
        )
        if not records:
            return None
        return records[0]["event_type" if self.kind == "vehicle" else "access_type"]

    @property
    def extra_state_attributes(self):
        """Return latest details and the ten-record API page."""
        records = (
            self.community.vehicle_records
            if self.kind == "vehicle"
            else self.community.visitor_records
        )
        labels = (
            VEHICLE_EVENT_LABELS if self.kind == "vehicle" else VISITOR_ACCESS_LABELS
        )
        label_key = "event_type" if self.kind == "vehicle" else "access_type"
        label_field = "event_label" if self.kind == "vehicle" else "access_label"
        recent_records = [
            {**record, label_field: labels.get(record.get(label_key), "알 수 없음")}
            for record in records[:HISTORY_ATTRIBUTE_LIMIT]
        ]
        return {
            "latest": recent_records[0] if recent_records else None,
            "recent_records": recent_records,
            "stored_record_count": len(records),
            "storage_limit": 500,
        }

    async def async_added_to_hass(self) -> None:
        """Subscribe to community refreshes."""
        self.async_on_remove(self.community.async_add_listener(self._handle_refresh))

    @callback
    def _handle_refresh(self) -> None:
        """Write state after a REST refresh."""
        self.async_write_ha_state()


class BestinLobbyDoorListSensor(SensorEntity):
    """Expose the server-returned common entrance allowlist."""

    _attr_should_poll = False
    _attr_name = "공동현관 목록"
    _attr_icon = "mdi:door"

    def __init__(self, community) -> None:
        self.community = community
        self._attr_unique_id = f"{community.entry.entry_id}_lobby_doors"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"{community.entry.entry_id}_community")},
            "name": "BESTIN 커뮤니티",
            "manufacturer": "HDC Labs",
            "model": "Smart Home 2.0 Community API",
        }

    @property
    def native_value(self) -> int:
        """Return the number of doors authorized by the server."""
        return len(self.community.lobby_doors)

    @property
    def extra_state_attributes(self):
        """Return non-secret door IDs and lobby names."""
        return {"doors": self.community.lobby_doors}

    async def async_added_to_hass(self) -> None:
        """Subscribe to the six-hour door-list refresh."""
        self.async_on_remove(self.community.async_add_listener(self._handle_refresh))

    @callback
    def _handle_refresh(self) -> None:
        """Write state after a door-list refresh."""
        self.async_write_ha_state()


class BestinRestEnergyPeriodSensor(SensorEntity):
    """Expose REST daily sums for billing integrations that require an entity."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_icon = "mdi:transmission-tower"
    _attr_should_poll = False

    def __init__(
        self,
        importer: BestinEnergyStatisticsImporter,
        months_back: int,
        name: str,
        unique_suffix: str,
    ) -> None:
        self.importer = importer
        self.months_back = months_back
        self._attr_name = name
        self._attr_unique_id = f"{importer.entry.entry_id}_{unique_suffix}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, importer.entry.entry_id)},
            "name": "BESTIN REST 에너지",
            "manufacturer": "HDC Labs",
            "model": "Smart Home 2.0 Energy API",
        }

    @property
    def native_value(self) -> float | None:
        """Return the selected calendar-month electricity total."""
        return self.importer.electricity_month_usage(self.months_back)

    @property
    def extra_state_attributes(self) -> dict | None:
        """Expose a compact same-month prior-year comparison for charts."""
        if self.months_back != 0:
            return None
        attributes = self.importer.electricity_year_over_year()
        attributes["comparison_generated_at"] = dt_util.now().isoformat()
        return attributes

    async def async_added_to_hass(self) -> None:
        """Subscribe to REST refreshes."""
        self.async_on_remove(self.importer.async_add_listener(self._handle_refresh))

    @callback
    def _handle_refresh(self) -> None:
        """Write state after the REST cache changes."""
        self.async_write_ha_state()


class BestinSensor(BestinDevice, SensorEntity):
    """Defined the Sensor."""

    TYPE = DOMAIN_SENSOR

    def __init__(self, device, hub) -> None:
        """Initialize the sensor."""
        super().__init__(device, hub)
        self._attr_id = extract_and_transform(self._device_info.device_id)
        self._attr_icon = DEVICE_ICON.get(self._attr_id)

    @property
    def native_value(self):
        """Return the state of the sensor."""
        factor = VALUE_CONVERSION.get(self._attr_id)
        if callable(factor):
            return factor(self._device_info.state, self.hub.wp_version)
        return self._device_info.state

    @property
    def device_class(self):
        """Return the class of the sensor."""
        return DEVICE_CLASS.get(self._attr_id)

    @property
    def native_unit_of_measurement(self):
        """Return the unit of measurement of this sensor."""
        return DEVICE_UNIT.get(self._attr_id)

    @property
    def state_class(self):
        """Type of this sensor state."""
        if self._device_info.device_type in [
            "light:dcvalue",
            "outlet:powercons",
            "energy",
        ]:
            return "total_increasing" if "total" in self._attr_id else "measurement"
        return None
