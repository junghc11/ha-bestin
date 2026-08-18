"""BESTIN Smart Home 2.0 community access API support."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable, Mapping
from datetime import timedelta
from typing import Any

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_URL
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store

from .community_data import (
    parse_lobby_doors,
    parse_vehicle_records,
    parse_visitor_records,
    merge_recent_records,
    vehicle_record_key,
    visitor_record_key,
)
from .const import CONF_SESSION, DOMAIN, LOGGER, SMART_HOME_2

POLL_INTERVAL = timedelta(seconds=60)
DOOR_REFRESH_INTERVAL = timedelta(hours=6)
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=20)
MAX_SEEN_RECORDS = 500
MAX_STORED_RECORDS = 500
STORAGE_VERSION = 1


class BestinCommunityAPI:
    """Poll read-only community history and expose explicit action methods."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, api: Any) -> None:
        self.hass = hass
        self.entry = entry
        self.api = api
        self.vehicle_records: list[dict[str, Any]] = []
        self.visitor_records: list[dict[str, Any]] = []
        self.lobby_doors: list[dict[str, Any]] = []
        self.new_vehicle_records: list[dict[str, Any]] = []
        self.new_visitor_records: list[dict[str, Any]] = []
        self._listeners: set[Callable[[], None]] = set()
        self._refresh_lock = asyncio.Lock()
        self._cancel_poll: Callable[[], None] | None = None
        self._cancel_door_refresh: Callable[[], None] | None = None
        self._vehicle_initialized = False
        self._visitor_initialized = False
        self._seen_vehicle_order: deque[str] = deque()
        self._seen_vehicle_keys: set[str] = set()
        self._seen_visitor_order: deque[str] = deque()
        self._seen_visitor_keys: set[str] = set()
        self._store = Store[dict[str, Any]](
            hass,
            STORAGE_VERSION,
            f"{DOMAIN}_community_history_{entry.entry_id}",
        )

    async def async_start(self) -> None:
        """Establish baselines and start polling without failing hub setup."""
        await self._async_load_history()
        await self.async_refresh()
        await self.async_refresh_doors()
        self._cancel_poll = async_track_time_interval(
            self.hass, self.async_refresh, POLL_INTERVAL
        )
        self._cancel_door_refresh = async_track_time_interval(
            self.hass, self.async_refresh_doors, DOOR_REFRESH_INTERVAL
        )
        self.entry.async_on_unload(self.async_stop)

    @callback
    def async_stop(self) -> None:
        """Cancel scheduled refreshes."""
        if self._cancel_poll is not None:
            self._cancel_poll()
            self._cancel_poll = None
        if self._cancel_door_refresh is not None:
            self._cancel_door_refresh()
            self._cancel_door_refresh = None

    async def async_refresh(self, _now: Any = None) -> None:
        """Refresh vehicle and visitor history and publish only new records."""
        if self._refresh_lock.locked():
            return

        async with self._refresh_lock:
            self.new_vehicle_records = []
            self.new_visitor_records = []
            updated = False
            history_changed = False

            try:
                payload = await self._async_get_json("/v2/api/cars?skip=0&limit=10")
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
                LOGGER.warning(
                    "BESTIN vehicle history refresh failed: %s", type(err).__name__
                )
            else:
                records = parse_vehicle_records(payload)
                self.new_vehicle_records = self._accept_records(
                    records,
                    vehicle_record_key,
                    self._seen_vehicle_order,
                    self._seen_vehicle_keys,
                    self._vehicle_initialized,
                )
                self._vehicle_initialized = True
                merged = merge_recent_records(
                    records,
                    self.vehicle_records,
                    vehicle_record_key,
                    MAX_STORED_RECORDS,
                )
                history_changed |= merged != self.vehicle_records
                self.vehicle_records = merged
                updated = True

            try:
                payload = await self._async_get_json(
                    "/v2/api/visitors?skip=0&limit=10"
                )
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
                LOGGER.warning(
                    "BESTIN visitor history refresh failed: %s", type(err).__name__
                )
            else:
                records = parse_visitor_records(payload)
                self.new_visitor_records = self._accept_records(
                    records,
                    visitor_record_key,
                    self._seen_visitor_order,
                    self._seen_visitor_keys,
                    self._visitor_initialized,
                )
                self._visitor_initialized = True
                merged = merge_recent_records(
                    records,
                    self.visitor_records,
                    visitor_record_key,
                    MAX_STORED_RECORDS,
                )
                history_changed |= merged != self.visitor_records
                self.visitor_records = merged
                updated = True

            if not updated:
                return

            if history_changed:
                await self._async_save_history()

            for record in reversed(self.new_vehicle_records):
                self.hass.bus.async_fire(f"{DOMAIN}_vehicle_event", record)
            for record in reversed(self.new_visitor_records):
                self.hass.bus.async_fire(f"{DOMAIN}_visitor_event", record)
            for listener in tuple(self._listeners):
                listener()

    async def _async_load_history(self) -> None:
        """Restore bounded vehicle and visitor history across HA restarts."""
        data = await self._store.async_load()
        if not isinstance(data, dict):
            return

        vehicle_records = data.get("vehicle_records")
        if isinstance(vehicle_records, list):
            self.vehicle_records = [
                dict(record)
                for record in vehicle_records[:MAX_STORED_RECORDS]
                if isinstance(record, Mapping)
            ]

        visitor_records = data.get("visitor_records")
        if isinstance(visitor_records, list):
            self.visitor_records = [
                dict(record)
                for record in visitor_records[:MAX_STORED_RECORDS]
                if isinstance(record, Mapping)
            ]

        self._seed_seen_records(
            self.vehicle_records,
            vehicle_record_key,
            self._seen_vehicle_order,
            self._seen_vehicle_keys,
        )
        self._seed_seen_records(
            self.visitor_records,
            visitor_record_key,
            self._seen_visitor_order,
            self._seen_visitor_keys,
        )

    async def _async_save_history(self) -> None:
        """Persist bounded community history in Home Assistant storage."""
        await self._store.async_save(
            {
                "vehicle_records": self.vehicle_records,
                "visitor_records": self.visitor_records,
            }
        )

    @staticmethod
    def _seed_seen_records(
        records: list[dict[str, Any]],
        key_builder: Callable[[Mapping[str, Any]], str],
        seen_order: deque[str],
        seen_keys: set[str],
    ) -> None:
        """Seed the de-duplication window from persisted newest-first records."""
        for record in reversed(records[-MAX_SEEN_RECORDS:]):
            key = key_builder(record)
            if key in seen_keys:
                continue
            seen_order.append(key)
            seen_keys.add(key)

    async def async_refresh_doors(self, _now: Any = None) -> None:
        """Refresh common entrance metadata without opening any door."""
        try:
            payload = await self._async_get_json("/v2/api/onepass/doors")
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
            LOGGER.warning(
                "BESTIN common entrance list refresh failed: %s",
                type(err).__name__,
            )
            return
        self.lobby_doors = parse_lobby_doors(payload)
        for listener in tuple(self._listeners):
            listener()

    @staticmethod
    def _accept_records(
        records: list[dict[str, Any]],
        key_builder: Callable[[Mapping[str, Any]], str],
        seen_order: deque[str],
        seen_keys: set[str],
        initialized: bool,
    ) -> list[dict[str, Any]]:
        """Update a bounded seen set and return records new after baseline."""
        new_records = [
            record for record in records if key_builder(record) not in seen_keys
        ]
        for record in reversed(records):
            key = key_builder(record)
            if key in seen_keys:
                continue
            seen_order.append(key)
            seen_keys.add(key)
            while len(seen_order) > MAX_SEEN_RECORDS:
                seen_keys.discard(seen_order.popleft())
        return new_records if initialized else []

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Register an entity refresh listener."""
        self._listeners.add(listener)
        return lambda: self._listeners.discard(listener)

    async def async_call_elevator(self, direction: str) -> dict[str, Any]:
        """Call the elevator in an explicit user-selected direction."""
        if direction not in {"up", "down"}:
            raise HomeAssistantError("Elevator direction must be up or down")
        return await self._async_post_json(
            f"/v2/api/elevator/{direction}", raw_body=direction
        )

    async def async_open_lobby_door(self, door_id: str) -> dict[str, Any]:
        """Open one explicitly selected common entrance door."""
        session = self.entry.data.get(CONF_SESSION, {})
        alias = session.get("alias")
        if not alias:
            raise HomeAssistantError("BESTIN session alias is unavailable")
        return await self._async_post_json(
            f"/v2/api/onepass/doors/{door_id}/apply",
            json_body={"appname": "\uc2a4\ub9c8\ud2b8\ud6482.0", "alias": alias},
        )

    async def async_get_visitor_image(self, visitor_id: str) -> bytes:
        """Download one visitor image on demand."""
        for attempt in range(2):
            url, headers = self._request_details(
                f"/v2/api/visitors/{visitor_id}/image"
            )
            async with self.api.session.get(
                url, headers=headers, timeout=REQUEST_TIMEOUT
            ) as response:
                if response.status == 401 and attempt == 0:
                    await self.api._v2_refresh_session()
                    continue
                response.raise_for_status()
                return await response.read()
        raise HomeAssistantError("BESTIN visitor image authorization failed")

    async def _async_get_json(self, path: str) -> Any:
        """Perform an authenticated JSON GET, refreshing once on 401."""
        for attempt in range(2):
            url, headers = self._request_details(path)
            async with self.api.session.get(
                url, headers=headers, timeout=REQUEST_TIMEOUT
            ) as response:
                if response.status == 401 and attempt == 0:
                    await self.api._v2_refresh_session()
                    continue
                response.raise_for_status()
                return await response.json(content_type=None)
        raise ValueError("BESTIN session refresh did not authorize the request")

    async def _async_post_json(
        self,
        path: str,
        *,
        raw_body: str | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Perform an authenticated action POST, refreshing once on 401."""
        for attempt in range(2):
            url, headers = self._request_details(path)
            async with self.api.session.post(
                url,
                headers=headers,
                data=raw_body,
                json=json_body,
                timeout=REQUEST_TIMEOUT,
            ) as response:
                if response.status == 401 and attempt == 0:
                    await self.api._v2_refresh_session()
                    continue
                response.raise_for_status()
                payload = await response.json(content_type=None)
                if isinstance(payload, dict) and payload.get("result") == "fail":
                    raise HomeAssistantError("BESTIN action was rejected")
                return payload if isinstance(payload, dict) else {"result": payload}
        raise HomeAssistantError("BESTIN action authorization failed")

    def _request_details(self, path: str) -> tuple[str, dict[str, str]]:
        """Build a request from the integration-maintained session."""
        session = self.entry.data.get(CONF_SESSION, {})
        base_url = session.get(CONF_URL)
        access_token = session.get("access-token")
        if not base_url or not access_token:
            raise ValueError("BESTIN session is incomplete")
        return (
            f"{base_url.rstrip('/')}{path}",
            {
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0",
                "access-token": access_token,
            },
        )


async def async_setup_community(
    hass: HomeAssistant, entry: ConfigEntry, hub: Any
) -> None:
    """Set up community features for a remote Smart Home 2.0 entry."""
    if hub.cntr_version != SMART_HOME_2 or hub.api is None:
        return
    session = entry.data.get(CONF_SESSION)
    if not isinstance(session, dict):
        return

    community = BestinCommunityAPI(hass, entry, hub.api)
    hub.community_api = community
    await community.async_start()
