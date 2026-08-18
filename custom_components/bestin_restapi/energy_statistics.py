"""Import BESTIN daily energy readings as Home Assistant external statistics."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

import aiohttp
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_URL
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import CONF_SESSION, DOMAIN, LOGGER, SMART_HOME_2
from .energy_data import (
    ENERGY_CHANNELS,
    build_statistic_points,
    iter_months,
    month_usage,
    parse_daily_energy,
    recent_months,
)

# Keep a fixed anchor so Recorder's cumulative sums never shift as time passes.
# BESTIN exposes calendar-month daily history. Start at the beginning of the
# previous calendar year so current periods can be compared with the same
# period last year, while keeping Recorder's cumulative anchor stable forever.
HISTORY_START = date(2025, 1, 1)
REFRESH_INTERVAL = timedelta(hours=6)
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=20)


class BestinEnergyStatisticsImporter:
    """Fetch daily BESTIN totals and maintain external statistics."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, api: Any) -> None:
        self.hass = hass
        self.entry = entry
        self.api = api
        self.daily: dict[date, dict[str, float]] = {}
        self._listeners: set[Callable[[], None]] = set()
        self._refresh_lock = asyncio.Lock()
        self._cancel_interval: Callable[[], None] | None = None

    async def async_start(self) -> None:
        """Run the initial backfill and schedule refreshes."""
        await self.async_refresh(full=True)
        self._cancel_interval = async_track_time_interval(
            self.hass, self.async_refresh, REFRESH_INTERVAL
        )
        self.entry.async_on_unload(self.async_stop)

    @callback
    def async_stop(self) -> None:
        """Cancel the periodic refresh."""
        if self._cancel_interval is not None:
            self._cancel_interval()
            self._cancel_interval = None

    async def async_refresh(self, _now: Any = None, *, full: bool = False) -> None:
        """Refresh history and import all cached completed days."""
        if self._refresh_lock.locked():
            return

        async with self._refresh_lock:
            today = dt_util.now().date()
            months = (
                list(iter_months(HISTORY_START, today))
                if full or not self.daily
                else recent_months(today)
            )
            successful_months = 0

            for month in months:
                try:
                    month_data = await self._async_fetch_month(month)
                except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
                    LOGGER.warning(
                        "BESTIN daily energy refresh failed for %04d-%02d: %s",
                        month.year,
                        month.month,
                        type(err).__name__,
                    )
                    continue

                self._replace_month(month, month_data)
                successful_months += 1

            if successful_months == 0:
                return

            self._import_statistics(today)
            for listener in tuple(self._listeners):
                listener()
            LOGGER.info(
                "Imported BESTIN daily energy statistics through %s (%d days)",
                today - timedelta(days=1),
                sum(1 for day in self.daily if HISTORY_START <= day < today),
            )

    async def _async_fetch_month(self, month: date) -> dict[date, dict[str, float]]:
        """Fetch and normalize one calendar month, retrying once after auth refresh."""
        for attempt in range(2):
            session = self.entry.data.get(CONF_SESSION, {})
            base_url = session.get(CONF_URL)
            access_token = session.get("access-token")
            if not base_url or not access_token:
                raise ValueError("BESTIN session is incomplete")

            url = (
                f"{base_url.rstrip('/')}/v2/api/meter/daily/energies/"
                f"{month.year}/{month.month:02d}?skip=0&limit=31"
            )
            headers = {
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0",
                "access-token": access_token,
            }

            async with self.api.session.get(
                url, headers=headers, timeout=REQUEST_TIMEOUT
            ) as response:
                if response.status == 401 and attempt == 0:
                    await self.api._v2_refresh_session()
                    continue
                response.raise_for_status()
                payload = await response.json(content_type=None)
                return parse_daily_energy(payload)

        raise ValueError("BESTIN session refresh did not authorize the request")

    def _replace_month(
        self, month: date, month_data: dict[date, dict[str, float]]
    ) -> None:
        """Replace cached values for a successfully fetched month."""
        for day in tuple(self.daily):
            if day.year == month.year and day.month == month.month:
                self.daily.pop(day)
        self.daily.update(month_data)

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Register a listener for refreshed daily totals."""
        self._listeners.add(listener)
        return lambda: self._listeners.discard(listener)

    def electricity_month_usage(self, months_back: int = 0) -> float | None:
        """Return electricity usage for the requested calendar month."""
        target = recent_months(dt_util.now().date(), months_back + 1)[0]
        return month_usage(self.daily, "electricity", target)

    def electricity_year_over_year(self) -> dict[str, Any]:
        """Return comparable current-month and prior-year daily electricity."""
        today = dt_util.now().date()
        current_month = today.replace(day=1)
        previous_month = current_month.replace(year=current_month.year - 1)

        current_rows = sorted(
            (day, channels["electricity"])
            for day, channels in self.daily.items()
            if day.year == current_month.year
            and day.month == current_month.month
            and day < today
            and "electricity" in channels
        )
        through_day = max((day.day for day, _value in current_rows), default=0)
        previous_rows = sorted(
            (day, channels["electricity"])
            for day, channels in self.daily.items()
            if day.year == previous_month.year
            and day.month == previous_month.month
            and day.day <= through_day
            and "electricity" in channels
        )
        recent_rows = sorted(
            (day, channels["electricity"])
            for day, channels in self.daily.items()
            if day < today and "electricity" in channels
        )[-30:]
        previous_year_recent_rows = []
        for day, _value in recent_rows:
            try:
                source_day = day.replace(year=day.year - 1)
            except ValueError:
                source_day = day.replace(year=day.year - 1, day=28)
            previous_value = self.daily.get(source_day, {}).get("electricity")
            previous_year_recent_rows.append(
                {
                    "date": day.isoformat(),
                    "source_date": source_day.isoformat(),
                    "value": previous_value,
                }
            )

        current_total = round(sum(value for _day, value in current_rows), 2)
        previous_total = round(sum(value for _day, value in previous_rows), 2)
        change_percent = (
            round((current_total - previous_total) / previous_total * 100, 1)
            if previous_total > 0
            else None
        )

        return {
            "comparison_current_year": current_month.year,
            "comparison_previous_year": previous_month.year,
            "comparison_month": current_month.month,
            "comparison_through_day": through_day,
            "current_period_total": current_total,
            "previous_year_period_total": previous_total,
            "year_over_year_percent": change_percent,
            "current_month_daily": [
                {"day": day.day, "value": value} for day, value in current_rows
            ],
            "previous_year_same_month_daily": [
                {"day": day.day, "value": value} for day, value in previous_rows
            ],
            "recent_30_days": [
                {"date": day.isoformat(), "value": value}
                for day, value in recent_rows
            ],
            "previous_year_recent_30_days": previous_year_recent_rows,
        }

    @callback
    def _import_statistics(self, today: date) -> None:
        """Queue external-statistic updates for every BESTIN energy channel."""
        timezone = dt_util.get_time_zone(self.hass.config.time_zone)
        if timezone is None:
            timezone = dt_util.UTC

        for channel, description in ENERGY_CHANNELS.items():
            statistic_id = f"{DOMAIN}:{description['statistic_suffix']}"
            metadata = StatisticMetaData(
                mean_type=StatisticMeanType.NONE,
                has_sum=True,
                name=description["name"],
                source=DOMAIN,
                statistic_id=statistic_id,
                unit_class=description["unit_class"],
                unit_of_measurement=description["unit"],
            )
            points = build_statistic_points(
                self.daily, channel, HISTORY_START, today, timezone
            )
            async_add_external_statistics(
                self.hass, metadata, [StatisticData(**point) for point in points]
            )


async def async_setup_energy_statistics(
    hass: HomeAssistant, entry: ConfigEntry, hub: Any
) -> None:
    """Set up daily energy statistics for the remote Smart Home 2.0 entry only."""
    if hub.cntr_version != SMART_HOME_2 or hub.api is None:
        return
    session = entry.data.get(CONF_SESSION)
    if not isinstance(session, dict):
        return

    importer = BestinEnergyStatisticsImporter(hass, entry, hub.api)
    hub.energy_statistics_importer = importer
    await importer.async_start()
