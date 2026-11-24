from datetime import datetime
from zoneinfo import ZoneInfo

# Get current time in Kolkata
kolkata_tz = ZoneInfo("Asia/Kolkata")


def get_date() -> datetime:
    """Get the current date in UTC as a string."""
    kolkata_time = datetime.now(kolkata_tz)
    return kolkata_time