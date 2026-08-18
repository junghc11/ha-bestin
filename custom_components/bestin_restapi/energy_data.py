"""Pure helpers for BESTIN daily energy data."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, datetime, time, timedelta, tzinfo
from decimal import Decimal, InvalidOperation
from typing import Any

ENERGY_CHANNELS: dict[str, dict[str, str]] = {
    "electricity": {
        "field": "ENERGY_USE01",
        "name": "BESTIN Electricity Daily Consumption",
        "statistic_suffix": "electricity_consumption",
        "unit": "kWh",
        "unit_class": "energy",
    },
    "water": {
        "field": "ENERGY_USE02",
        "name": "BESTIN Water Daily Consumption",
        "statistic_suffix": "water_consumption",
        "unit": "m\u00b3",
        "unit_class": "volume",
    },
    "gas": {
        "field": "ENERGY_USE03",
        "name": "BESTIN Gas Daily Consumption",
        "statistic_suffix": "gas_consumption",
        "unit": "m\u00b3",
        "unit_class": "volume",
    },
    "hot_water": {
        "field": "ENERGY_USE04",
        "name": "BESTIN Hot Water Daily Consumption",
        "statistic_suffix": "hot_water_consumption",
        "unit": "m\u00b3",
        "unit_class": "volume",
    },
    "heating": {
        "field": "ENERGY_USE05",
        "name": "BESTIN Heating Daily Consumption",
        "statistic_suffix": "heating_consumption",
        "unit": "m\u00b3",
        "unit_class": "volume",
    },
}


def iter_months(start: date, end: date) -> Iterable[date]:
    """Yield the first day of every month from start through end."""
    current = start.replace(day=1)
    final = end.replace(day=1)
    while current <= final:
        yield current
        if current.month == 12:
            current = current.replace(year=current.year + 1, month=1)
        else:
            current = current.replace(month=current.month + 1)


def recent_months(today: date, count: int = 2) -> list[date]:
    """Return the current month and preceding months in ascending order."""
    months = [today.replace(day=1)]
    while len(months) < count:
        current = months[0]
        previous = (
            current.replace(year=current.year - 1, month=12)
            if current.month == 1
            else current.replace(month=current.month - 1)
        )
        months.insert(0, previous)
    return months


def parse_daily_energy(payload: Any) -> dict[date, dict[str, float]]:
    """Normalize the daily energy response from either BESTIN field style."""
    rows = _extract_rows(payload)
    parsed: dict[date, dict[str, float]] = {}

    for row in rows:
        if not isinstance(row, Mapping):
            continue
        try:
            day = date(
                _as_int(_first(row, "year", "ENERGY_YEAR")),
                _as_int(_first(row, "month", "ENERGY_MONTH")),
                _as_int(_first(row, "day", "ENERGY_DAY")),
            )
        except (TypeError, ValueError):
            continue

        values: dict[str, float] = {}
        for channel, description in ENERGY_CHANNELS.items():
            value = _as_non_negative_float(row.get(description["field"]))
            if value is not None:
                values[channel] = value
        if values:
            parsed[day] = values

    return parsed


def build_statistic_points(
    daily: Mapping[date, Mapping[str, float]],
    channel: str,
    anchor: date,
    today: date,
    timezone: tzinfo,
) -> list[dict[str, datetime | float]]:
    """Build a cumulative series whose daily change lands on that local day."""
    points: list[dict[str, datetime | float]] = [
        {
            "start": datetime.combine(anchor, time.min, tzinfo=timezone),
            "state": 0.0,
            "sum": 0.0,
        }
    ]
    cumulative = Decimal(0)

    for day in sorted(daily):
        if day < anchor or day >= today:
            continue
        value = daily[day].get(channel)
        if value is None:
            continue
        cumulative += Decimal(str(value))
        cumulative_value = float(cumulative)
        points.append(
            {
                "start": datetime.combine(
                    day + timedelta(days=1), time.min, tzinfo=timezone
                ),
                "state": cumulative_value,
                "sum": cumulative_value,
            }
        )

    return points


def month_usage(
    daily: Mapping[date, Mapping[str, float]],
    channel: str,
    month: date,
) -> float | None:
    """Return the API usage total for one calendar month."""
    start = month.replace(day=1)
    end = (
        start.replace(year=start.year + 1, month=1)
        if start.month == 12
        else start.replace(month=start.month + 1)
    )
    values = [
        Decimal(str(channels[channel]))
        for day, channels in daily.items()
        if start <= day < end and channel in channels
    ]
    if not values:
        return None
    return float(sum(values, start=Decimal(0)))


def _extract_rows(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, Mapping):
        return []
    for key in ("data", "items", "energies", "list"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


def _first(row: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def _as_int(value: Any) -> int:
    if isinstance(value, bool) or value is None:
        raise ValueError
    return int(str(value).strip())


def _as_non_negative_float(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value).replace(",", "").strip())
    except InvalidOperation:
        return None
    if not parsed.is_finite() or parsed < 0:
        return None
    return float(parsed)
