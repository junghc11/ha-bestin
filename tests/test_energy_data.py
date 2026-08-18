"""Tests for BESTIN daily energy normalization."""

import importlib.util
from datetime import date, timedelta, timezone
from pathlib import Path

MODULE_PATH = (
    Path(__file__).parents[1]
    / "custom_components"
    / "bestin_restapi"
    / "energy_data.py"
)
SPEC = importlib.util.spec_from_file_location("bestin_energy_data", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
ENERGY_DATA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ENERGY_DATA)

build_statistic_points = ENERGY_DATA.build_statistic_points
iter_months = ENERGY_DATA.iter_months
month_usage = ENERGY_DATA.month_usage
parse_daily_energy = ENERGY_DATA.parse_daily_energy
recent_months = ENERGY_DATA.recent_months


def test_parse_daily_energy_accepts_both_date_key_styles() -> None:
    parsed = parse_daily_energy(
        [
            {
                "year": "2026",
                "month": "08",
                "day": "01",
                "ENERGY_USE01": "35.30",
                "ENERGY_USE02": "1.25",
            },
            {
                "ENERGY_YEAR": 2026,
                "ENERGY_MONTH": 8,
                "ENERGY_DAY": 2,
                "ENERGY_USE01": "39.06",
                "ENERGY_USE03": "0.70",
            },
        ]
    )

    assert parsed[date(2026, 8, 1)] == {
        "electricity": 35.30,
        "water": 1.25,
    }
    assert parsed[date(2026, 8, 2)] == {
        "electricity": 39.06,
        "gas": 0.70,
    }


def test_parse_daily_energy_unwraps_data_and_rejects_invalid_values() -> None:
    parsed = parse_daily_energy(
        {
            "data": [
                {
                    "year": 2026,
                    "month": 7,
                    "day": 31,
                    "ENERGY_USE01": "1,234.50",
                    "ENERGY_USE02": "-1",
                    "ENERGY_USE03": None,
                },
                {"year": "bad", "month": 7, "day": 30, "ENERGY_USE01": 1},
            ]
        }
    )

    assert parsed == {date(2026, 7, 31): {"electricity": 1234.5}}


def test_build_statistic_points_uses_next_local_midnight() -> None:
    kst = timezone(timedelta(hours=9))
    points = build_statistic_points(
        {
            date(2026, 8, 1): {"electricity": 35.30},
            date(2026, 8, 2): {"electricity": 39.06},
            date(2026, 8, 3): {"electricity": 37.24},
            date(2026, 8, 4): {"electricity": 28.27},
        },
        "electricity",
        date(2026, 8, 1),
        date(2026, 8, 4),
        kst,
    )

    assert [point["sum"] for point in points] == [0.0, 35.3, 74.36, 111.6]
    assert points[-1]["start"].isoformat() == "2026-08-04T00:00:00+09:00"


def test_month_ranges_cross_year_boundary() -> None:
    assert list(iter_months(date(2025, 12, 10), date(2026, 2, 1))) == [
        date(2025, 12, 1),
        date(2026, 1, 1),
        date(2026, 2, 1),
    ]
    assert recent_months(date(2026, 1, 5)) == [
        date(2025, 12, 1),
        date(2026, 1, 1),
    ]


def test_month_ranges_cover_one_year_backfill() -> None:
    months = list(iter_months(date(2025, 1, 1), date(2026, 8, 18)))

    assert len(months) == 20
    assert months[0] == date(2025, 1, 1)
    assert months[-1] == date(2026, 8, 1)


def test_month_usage_includes_current_partial_day() -> None:
    daily = {
        date(2026, 7, 31): {"electricity": 40.68},
        date(2026, 8, 1): {"electricity": 35.38},
        date(2026, 8, 2): {"electricity": 38.06},
        date(2026, 8, 3): {"electricity": 37.24},
        date(2026, 8, 4): {"electricity": 28.27},
    }

    assert month_usage(daily, "electricity", date(2026, 8, 4)) == 138.95
    assert month_usage(daily, "electricity", date(2026, 6, 1)) is None
