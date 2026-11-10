PYTHONPATH=src python - <<'PY'
import asyncio
import os
import sys
import traceback

print("=" * 60)
print("STEP 0: environment")
print("=" * 60)
print("cwd:                 ", os.getcwd())
print("python:              ", sys.executable)

# ---- import check ----
try:
    from pai.config import config
    print("config.database.url: ", config.database.url)
except Exception as e:
    print("FAILED importing pai.config:", e)
    traceback.print_exc()
    sys.exit(1)

try:
    from pai.storage.database import Database
    from pai.storage.models import UserModel
    print("imports:             OK")
except Exception as e:
    print("FAILED importing pai.storage:", e)
    traceback.print_exc()
    sys.exit(1)

# ---- password helper check (common failure) ----
hash_password = None
verify_password = None
for module_name in ("pai.security.auth", "pai.security.passwords"):
    try:
        mod = __import__(module_name, fromlist=["hash_password", "verify_password"])
        hash_password = getattr(mod, "hash_password", None)
        verify_password = getattr(mod, "verify_password", None)
        if hash_password:
            print(f"hash_password from:  {module_name}")
            break
    except ImportError:
        pass

if hash_password is None:
    print("FAILED: hash_password not found in pai.security.auth or .passwords")
    print("Check which module actually defines it:")
    print("  grep -rn 'def hash_password' src/pai/")
    sys.exit(1)

# ---- resolve actual DB file path ----
print()
print("=" * 60)
print("STEP 1: resolve DB file")
print("=" * 60)
db_url = config.database.url
print("url:                 ", db_url)

db_path = db_url
for prefix in ("sqlite+aiosqlite:///", "sqlite:///", "sqlite+aiosqlite://"):
    if db_path.startswith(prefix):
        db_path = db_path[len(prefix):]
        break
if db_path.startswith("./"):
    db_path = db_path[2:]
db_abs = os.path.abspath(db_path)
print("resolved path:       ", db_abs)
print("exists:              ", os.path.exists(db_abs))
if os.path.exists(db_abs):
    print("size:                ", os.path.getsize(db_abs), "bytes")

# ---- inspect schema ----
async def inspect_and_create():
    print()
    print("=" * 60)
    print("STEP 2: connect + inspect schema")
    print("=" * 60)

    db = Database(url=db_url)
    await db.initialize()
    print("initialize():        OK")

    from sqlalchemy import select, text

    async with db.session() as s:
        # What tables exist?
        result = await s.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        )
        tables = [r[0] for r in result.fetchall()]
        print("tables:              ", tables)

        if "users" not in tables:
            print()
            print("🚨 'users' table does not exist in this database.")
            print("   The Database.initialize() call didn't create it.")
            print("   Check that Base is the same across pai.storage.database")
            print("   and pai.storage.models.")
            return

        # Column names
        result = await s.execute(text("PRAGMA table_info(users)"))
        columns = [(r[1], r[2]) for r in result.fetchall()]
        print("users columns:")
        for name, ctype in columns:
            print(f"    {name:20} {ctype}")
        column_names = [c[0] for c in columns]

        # Which password column does it use?
        hashed_col = (
            "hashed_password" if "hashed_password" in column_names
            else "password_hash" if "password_hash" in column_names
            else None
        )
        if hashed_col is None:
            print()
            print("🚨 users table has NO password column!")
            print("   Looked for 'hashed_password' and 'password_hash'.")
            return
        print(f"password column:      {hashed_col}")

        # Existing users
        result = await s.execute(select(UserModel))
        users = result.scalars().all()
        print()
        print(f"existing users ({len(users)}):")
        for u in users:
            admin = "ADMIN" if u.is_admin else "     "
            ph = getattr(u, hashed_col) or ""
            hash_status = "✓" if ph.startswith(("$2a$", "$2b$", "$2y$")) else "✗"
            print(f"    [{admin}] {u.email:30} hash={hash_status} id={str(u.id)[:12]}")

    print()
    print("=" * 60)
    print("STEP 3: create user")
    print("=" * 60)

    import uuid
    from sqlalchemy import delete

    OLD_EMAIL    = "chethan@example.com"
    NEW_EMAIL    = "chethan@example.com"
    NEW_PASSWORD = "password123"
    NEW_IS_ADMIN = True

    async with db.session() as s:
        # Delete if exists
        old = (await s.execute(
            select(UserModel).where(UserModel.email == OLD_EMAIL)
        )).scalar_one_or_none()
        if old:
            await s.delete(old)
            await s.flush()
            print(f"deleted:             {old.email} (id={old.id})")
        else:
            print(f"deleted:             nothing (no user with {OLD_EMAIL!r})")

        # Verify gone
        still_there = (await s.execute(
            select(UserModel).where(UserModel.email == NEW_EMAIL)
        )).scalar_one_or_none()
        if still_there:
            print(f"🚨 ERROR: {NEW_EMAIL} still exists after delete!")
            return

        # Create
        try:
            hashed = hash_password(NEW_PASSWORD)
            print(f"hash generated:      {hashed[:30]}…")
        except Exception as e:
            print(f"🚨 hash_password failed: {e}")
            traceback.print_exc()
            return

        # Build the UserModel with the correct field name
        user_kwargs = {
            "id": str(uuid.uuid4()),
            "email": NEW_EMAIL,
            "is_admin": NEW_IS_ADMIN,
        }
        if hashed_col == "hashed_password":
            user_kwargs["hashed_password"] = hashed
        elif hashed_col == "password_hash":
            user_kwargs["password_hash"] = hashed
        else:
            print(f"🚨 Unknown password column: {hashed_col}")
            return

        try:
            user = UserModel(**user_kwargs)
        except TypeError as e:
            print(f"🚨 UserModel construction failed: {e}")
            print("   This means the column exists but is not a valid kwarg.")
            print("   Possible mismatch between database.py and models.py Base.")
            traceback.print_exc()
            return

        s.add(user)

        try:
            await s.commit()
            print(f"committed:           user id={user.id}")
        except Exception as e:
            print(f"🚨 commit failed: {e}")
            traceback.print_exc()
            return

    # ---- verify from a fresh session ----
    print()
    print("=" * 60)
    print("STEP 4: verify from fresh session")
    print("=" * 60)

    async with db.session() as s:
        fresh = (await s.execute(
            select(UserModel).where(UserModel.email == NEW_EMAIL)
        )).scalar_one_or_none()

        if fresh is None:
            print("🚨 user NOT found in fresh session — commit did not persist!")
            return

        print(f"found:               {fresh.email}")
        print(f"  id:                {fresh.id}")
        print(f"  is_admin:          {fresh.is_admin}")
        stored_hash = getattr(fresh, hashed_col) or ""
        print(f"  hash prefix:       {stored_hash[:10]}")
        print(f"  verify:            {verify_password(NEW_PASSWORD, stored_hash)}")

    await db.close()

try:
    asyncio.run(inspect_and_create())
except Exception as e:
    print()
    print("=" * 60)
    print("UNCAUGHT EXCEPTION")
    print("=" * 60)
    traceback.print_exc()
PY