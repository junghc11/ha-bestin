"""Explicit action buttons for BESTIN community facilities."""

from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .hub import BestinHub


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up elevator and common entrance action buttons."""
    hub: BestinHub = BestinHub.get_hub(hass, entry)
    if not (community := getattr(hub, "community_api", None)):
        return

    entities: list[ButtonEntity] = [
        BestinElevatorButton(community, "up"),
        BestinElevatorButton(community, "down"),
    ]
    entities.extend(
        BestinLobbyDoorButton(community, door) for door in community.lobby_doors
    )
    async_add_entities(entities)


class BestinCommunityButton(ButtonEntity):
    """Base for explicit community facility actions."""

    _attr_should_poll = False

    def __init__(self, community: Any) -> None:
        self.community = community
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"{community.entry.entry_id}_community")},
            "name": "BESTIN 커뮤니티",
            "manufacturer": "HDC Labs",
            "model": "Smart Home 2.0 Community API",
        }


class BestinElevatorButton(BestinCommunityButton):
    """Call the elevator in one selected direction."""

    def __init__(self, community: Any, direction: str) -> None:
        super().__init__(community)
        self.direction = direction
        self._attr_name = (
            "엘리베이터 위 방향 호출"
            if direction == "up"
            else "엘리베이터 아래 방향 호출"
        )
        self._attr_unique_id = (
            f"{community.entry.entry_id}_elevator_call_{direction}"
        )
        self._attr_icon = "mdi:elevator-up" if direction == "up" else "mdi:elevator-down"

    async def async_press(self) -> None:
        """Issue the elevator call only after an explicit button press."""
        await self.community.async_call_elevator(self.direction)


class BestinLobbyDoorButton(BestinCommunityButton):
    """Open one common entrance after an explicit button press."""

    _attr_entity_registry_enabled_default = False

    def __init__(self, community: Any, door: dict[str, Any]) -> None:
        super().__init__(community)
        self.door = door
        self._attr_name = f"공동현관 열기 - {door['name']}"
        self._attr_unique_id = (
            f"{community.entry.entry_id}_open_lobby_door_{door['id']}"
        )
        self._attr_icon = "mdi:door-open"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return non-secret lobby metadata."""
        return {"lobby_id": self.door.get("lobby_id"), "door_id": self.door["id"]}

    async def async_press(self) -> None:
        """Open the selected entrance only after an explicit button press."""
        await self.community.async_open_lobby_door(self.door["id"])
