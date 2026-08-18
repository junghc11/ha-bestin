"""Event entities for BESTIN community access history."""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .hub import BestinHub


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up vehicle and visitor event entities."""
    hub: BestinHub = BestinHub.get_hub(hass, entry)
    if community := getattr(hub, "community_api", None):
        async_add_entities(
            [
                BestinCommunityEvent(community, "vehicle"),
                BestinCommunityEvent(community, "visitor"),
            ]
        )


class BestinCommunityEvent(EventEntity):
    """Represent newly observed community access records."""

    _attr_should_poll = False

    def __init__(self, community: Any, kind: str) -> None:
        self.community = community
        self.kind = kind
        self._attr_has_entity_name = True
        self._attr_translation_key = f"{kind}_access"
        self._attr_name = "차량 출입 이벤트" if kind == "vehicle" else "방문자 출입 이벤트"
        self._attr_unique_id = f"{community.entry.entry_id}_{kind}_access_event"
        self._attr_icon = "mdi:car-arrow-right" if kind == "vehicle" else "mdi:account-eye"
        self._attr_event_types = (
            ["arrival", "departure", "parked", "unknown"]
            if kind == "vehicle"
            else ["lobby", "main_gate", "unit_entrance", "unknown"]
        )
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"{community.entry.entry_id}_community")},
            "name": "BESTIN 커뮤니티",
            "manufacturer": "HDC Labs",
            "model": "Smart Home 2.0 Community API",
        }

    async def async_added_to_hass(self) -> None:
        """Subscribe to community refreshes."""
        self.async_on_remove(self.community.async_add_listener(self._handle_refresh))

    @callback
    def _handle_refresh(self) -> None:
        """Publish each newly observed record."""
        records = (
            self.community.new_vehicle_records
            if self.kind == "vehicle"
            else self.community.new_visitor_records
        )
        type_key = "event_type" if self.kind == "vehicle" else "access_type"
        for record in reversed(records):
            self._trigger_event(record[type_key], record)
            self.async_write_ha_state()
