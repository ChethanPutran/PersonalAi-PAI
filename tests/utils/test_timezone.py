from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from croniter import croniter

tz = ZoneInfo("Asia/Kolkata")
now = datetime.now(timezone.utc)
local = now.astimezone(tz)
nxt_local = croniter("19 13 * * *", local).get_next(datetime)
nxt_utc = nxt_local.astimezone(timezone.utc)

print("now utc       ", now.isoformat())
print("now local     ", local.isoformat())
print("next local    ", nxt_local.isoformat())
print("next utc      ", nxt_utc.isoformat())
print("next as local ", nxt_utc.astimezone(tz).isoformat())