from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from bot.handlers.admin.common import parse_datetime
from bot.utils.time import datetime_input_example

TZ = ZoneInfo("Europe/Minsk")


def test_datetime_input_example_rounds_up_to_next_hour():
    assert (
        datetime_input_example(datetime(2026, 10, 21, 19, 15, tzinfo=TZ), TZ) == "21.10.2026 20:00"
    )
    assert (
        datetime_input_example(datetime(2026, 10, 21, 19, 0, tzinfo=TZ), TZ) == "21.10.2026 20:00"
    )
    assert (
        datetime_input_example(datetime(2026, 10, 21, 23, 30, tzinfo=TZ), TZ) == "22.10.2026 00:00"
    )


def test_datetime_parse_error_contains_example():
    with pytest.raises(ValueError, match=r"21\.10\.2026 20:00"):
        parse_datetime(
            "не дата",
            TZ,
            now=datetime(2026, 10, 21, 19, 15, tzinfo=TZ),
        )
