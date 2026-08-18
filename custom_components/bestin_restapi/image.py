"""Image entities for BESTIN community visitor records."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .hub import BestinHub


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the latest visitor image entity."""
    hub: BestinHub = BestinHub.get_hub(hass, entry)
    if community := getattr(hub, "community_api", None):
        async_add_entities([BestinLatestVisitorImage(community)])


class BestinLatestVisitorImage(ImageEntity):
    """Expose the latest BESTIN visitor image through HA's authenticated proxy."""

    _attr_should_poll = False
    _attr_content_type = "image/jpeg"

    def __init__(self, community: Any) -> None:
        super().__init__(community.hass)
        self.community = community
        self._visitor_id = self._latest_visitor_id()
        self._cached_visitor_id: str | None = None
        self._visitor_image_bytes: bytes | None = None
        self._attr_name = "최근 방문자 이미지"
        self._attr_unique_id = (
            f"{community.entry.entry_id}_latest_visitor_image"
        )
        self._attr_icon = "mdi:account-box"
        self._attr_image_last_updated = (
            dt_util.utcnow() if self._visitor_id is not None else None
        )
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"{community.entry.entry_id}_community")},
            "name": "BESTIN 커뮤니티",
            "manufacturer": "HDC Labs",
            "model": "Smart Home 2.0 Community API",
        }

    @property
    def available(self) -> bool:
        """Return whether a visitor image can be requested."""
        return self._visitor_id is not None

    @property
    def image_last_updated(self) -> datetime | None:
        """Return when the selected visitor image changed."""
        return self._attr_image_last_updated

    async def async_image(self) -> bytes | None:
        """Return the latest visitor image, cached for the current record."""
        visitor_id = self._visitor_id
        if visitor_id is None:
            return None
        if (
            self._cached_visitor_id == visitor_id
            and self._visitor_image_bytes is not None
        ):
            return self._visitor_image_bytes

        image = await self.community.async_get_visitor_image(visitor_id)
        if visitor_id == self._visitor_id:
            self._cached_visitor_id = visitor_id
            self._visitor_image_bytes = image
        return image

    async def async_added_to_hass(self) -> None:
        """Subscribe to community refreshes."""
        self.async_on_remove(self.community.async_add_listener(self._handle_refresh))

    @callback
    def _handle_refresh(self) -> None:
        """Invalidate the cache only when the latest visitor record changes."""
        visitor_id = self._latest_visitor_id()
        if visitor_id == self._visitor_id:
            return
        self._visitor_id = visitor_id
        self._cached_visitor_id = None
        self._visitor_image_bytes = None
        self._attr_image_last_updated = (
            dt_util.utcnow() if visitor_id is not None else None
        )
        self.async_write_ha_state()

    def _latest_visitor_id(self) -> str | None:
        """Return the latest visitor record ID from memory."""
        if not self.community.visitor_records:
            return None
        visitor_id = self.community.visitor_records[0].get("id")
        return str(visitor_id) if visitor_id is not None else None
