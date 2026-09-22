# 1. Install sqlite3 (optional, for direct DB pokes)
sudo apt install sqlite3

# 2. Promote the existing row
PYTHONPATH=src python - <<'PY'
import asyncio
from sqlalchemy import update
from pai.config import config
from pai.storage.database import Database
from pai.storage.models import UserModel

async def main():
    db = Database(url=config.database.url)
    async with db.session() as s:
        await s.execute(
            update(UserModel)
            .where(UserModel.email == "chethan@example.com")
            .values(is_admin=True)
        )
        await s.commit()
    await db.close()
    print("Done")

asyncio.run(main())
PY

# 3. Get a fresh token
curl -s -X POST http://127.0.0.1:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"chethan@example.com","password":"password123"}' \
  | python -m json.tool