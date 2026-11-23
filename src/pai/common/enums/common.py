from enum import StrEnum


class TimeZones(StrEnum):
    """Supported time zones."""
    UTC = "UTC"
    IST = "Asia/Kolkata"
    EST = "America/New_York"
    PST = "America/Los_Angeles"
    CET = "Europe/Berlin"
    JST = "Asia/Tokyo"