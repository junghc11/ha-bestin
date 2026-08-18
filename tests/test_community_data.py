"""Tests for BESTIN community access response normalization."""

import importlib.util
from pathlib import Path

MODULE_PATH = (
    Path(__file__).parents[1]
    / "custom_components"
    / "bestin_restapi"
    / "community_data.py"
)
SPEC = importlib.util.spec_from_file_location("bestin_community_data", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
COMMUNITY_DATA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(COMMUNITY_DATA)


def test_parse_vehicle_records_and_dedup_key() -> None:
    records = COMMUNITY_DATA.parse_vehicle_records(
        [
            {
                "id": 77,
                "created_at": "2026-08-04T20:31:02+09:00",
                "car_num": "12가3456",
                "park_loca": "B2",
                "car_mode": 1,
            },
            {"created_at": "missing required values"},
        ]
    )

    assert records == [
        {
            "id": "77",
            "created_at": "2026-08-04T20:31:02+09:00",
            "car_number": "12가3456",
            "parking_location": "B2",
            "mode_code": "1",
            "event_type": "arrival",
        }
    ]
    assert COMMUNITY_DATA.vehicle_record_key(records[0]) == "id:77"


def test_parse_vehicle_record_without_id_has_composite_key() -> None:
    record = COMMUNITY_DATA.parse_vehicle_records(
        {
            "data": [
                {
                    "created_at": "2026-08-04 21:00:00",
                    "car_num": "34나5678",
                    "park_loca": None,
                    "car_mode": "2",
                }
            ]
        }
    )[0]

    assert record["event_type"] == "departure"
    assert COMMUNITY_DATA.vehicle_record_key(record) == (
        "2026-08-04 21:00:00|34나5678||2"
    )


def test_parse_visitor_records() -> None:
    records = COMMUNITY_DATA.parse_visitor_records(
        {
            "items": [
                {
                    "id": "abc",
                    "accessed_at": "2026-08-04T19:00:00+09:00",
                    "accessed_type": 1,
                    "message": "경비실",
                }
            ]
        }
    )

    assert records[0]["access_type"] == "main_gate"
    assert records[0]["message"] == "경비실"
    assert COMMUNITY_DATA.visitor_record_key(records[0]) == "id:abc"


def test_parse_lobby_doors_sorts_numeric_lobby_id() -> None:
    doors = COMMUNITY_DATA.parse_lobby_doors(
        [
            {"id": "b", "lobby_id": "10", "lobby_name": "후문"},
            {"id": "a", "lobby_id": 2, "lobby_name": "정문"},
            {"id": None, "lobby_id": 1, "lobby_name": "invalid"},
        ]
    )

    assert [door["id"] for door in doors] == ["a", "b"]


def test_merge_recent_records_keeps_newest_unique_and_bounded() -> None:
    merged = COMMUNITY_DATA.merge_recent_records(
        [
            {"id": "3", "accessed_at": "new"},
            {"id": "2", "accessed_at": "updated"},
        ],
        [
            {"id": "2", "accessed_at": "old"},
            {"id": "1", "accessed_at": "oldest"},
        ],
        COMMUNITY_DATA.visitor_record_key,
        3,
    )

    assert [record["id"] for record in merged] == ["3", "2", "1"]
    assert merged[1]["accessed_at"] == "updated"
