"""Normalize BESTIN community access REST responses."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

VEHICLE_MODE_NAMES = {
    "1": "arrival",
    "2": "departure",
    "3": "parked",
}

VISITOR_ACCESS_NAMES = {
    "0": "lobby",
    "1": "main_gate",
    "2": "unit_entrance",
}


def _records(payload: Any) -> Iterable[Mapping[str, Any]]:
    """Yield mapping records from raw or commonly wrapped API responses."""
    if isinstance(payload, Mapping):
        for key in ("data", "items", "results"):
            nested = payload.get(key)
            if isinstance(nested, list):
                payload = nested
                break
        else:
            payload = [payload]

    if not isinstance(payload, list):
        return []

    return (item for item in payload if isinstance(item, Mapping))


def _text(value: Any) -> str | None:
    """Return a stripped text value, preserving zero."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def parse_vehicle_records(payload: Any) -> list[dict[str, Any]]:
    """Normalize vehicle arrival/departure history."""
    normalized: list[dict[str, Any]] = []
    for item in _records(payload):
        created_at = _text(item.get("created_at"))
        car_number = _text(item.get("car_num"))
        mode_code = _text(item.get("car_mode"))
        if created_at is None or car_number is None or mode_code is None:
            continue

        normalized.append(
            {
                "id": _text(item.get("id")),
                "created_at": created_at,
                "car_number": car_number,
                "parking_location": _text(item.get("park_loca")),
                "mode_code": mode_code,
                "event_type": VEHICLE_MODE_NAMES.get(mode_code, "unknown"),
            }
        )
    return normalized


def vehicle_record_key(record: Mapping[str, Any]) -> str:
    """Build a stable deduplication key for a vehicle record."""
    if record_id := _text(record.get("id")):
        return f"id:{record_id}"
    return "|".join(
        _text(record.get(key)) or ""
        for key in ("created_at", "car_number", "parking_location", "mode_code")
    )


def parse_visitor_records(payload: Any) -> list[dict[str, Any]]:
    """Normalize visitor access history without downloading images."""
    normalized: list[dict[str, Any]] = []
    for item in _records(payload):
        visitor_id = _text(item.get("id"))
        accessed_at = _text(item.get("accessed_at"))
        access_code = _text(item.get("accessed_type"))
        if visitor_id is None or accessed_at is None or access_code is None:
            continue

        normalized.append(
            {
                "id": visitor_id,
                "accessed_at": accessed_at,
                "access_code": access_code,
                "access_type": VISITOR_ACCESS_NAMES.get(access_code, "unknown"),
                "message": _text(item.get("message")),
            }
        )
    return normalized


def visitor_record_key(record: Mapping[str, Any]) -> str:
    """Build a stable deduplication key for a visitor record."""
    if visitor_id := _text(record.get("id")):
        return f"id:{visitor_id}"
    return "|".join(
        _text(record.get(key)) or ""
        for key in ("accessed_at", "access_code", "message")
    )


def merge_recent_records(
    new_records: Iterable[Mapping[str, Any]],
    stored_records: Iterable[Mapping[str, Any]],
    key_builder: Callable[[Mapping[str, Any]], str],
    limit: int,
) -> list[dict[str, Any]]:
    """Merge newest-first records with stored history and remove duplicates."""
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in (*new_records, *stored_records):
        key = key_builder(record)
        if key in seen:
            continue
        seen.add(key)
        merged.append(dict(record))
        if len(merged) >= limit:
            break
    return merged


def parse_lobby_doors(payload: Any) -> list[dict[str, Any]]:
    """Normalize the list of common entrance doors."""
    normalized: list[dict[str, Any]] = []
    for item in _records(payload):
        door_id = _text(item.get("id"))
        if door_id is None:
            continue
        normalized.append(
            {
                "id": door_id,
                "lobby_id": _text(item.get("lobby_id")),
                "name": _text(item.get("lobby_name")) or f"Lobby {door_id}",
            }
        )

    return sorted(
        normalized,
        key=lambda door: (
            int(door["lobby_id"])
            if door["lobby_id"] and door["lobby_id"].isdigit()
            else 2**31,
            door["name"],
        ),
    )
