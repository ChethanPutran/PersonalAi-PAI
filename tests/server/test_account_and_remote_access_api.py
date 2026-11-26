"""HTTP level, on the real app: device ownership + the remote-access switches on the plugin routes,
the account API and the Settings page."""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent / "leaks"))
from helpers import isolated_config  # noqa: E402

from pai.server.factory import create_application  # noqa: E402

DEVICE = {"name": "Work laptop", "device_type": "desktop", "app_version": "1.0.0", "runtime_version": "1.0.0",
          "platform": "linux", "platform_version": "6", "os_version": "6.1", "architecture": "x86_64"}


def signup(client, email):
    r = client.post("/api/v1/auth/register", json={"email": email, "password": "correct-horse-1"})
    assert r.status_code in (200, 201), r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def add_device(client, headers):
    r = client.post("/api/v1/devices/register", json={"device": DEVICE}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["device_id"]


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    app = create_application(isolated_config(tmp_path_factory.mktemp("acct")))
    with TestClient(app) as client:
        alice, mallory = signup(client, "alice@example.com"), signup(client, "mallory@example.com")
        yield client, alice, mallory, add_device(client, alice)


def test_remote_switches_default_off_and_only_the_owner_can_change_them(world):
    client, alice, mallory, dev = world
    got = client.get(f"/api/v1/devices/{dev}/remote-access", headers=alice).json()
    assert got["allow_remote_install"] is False and got["allow_remote_invoke"] is False
    for method in (client.get, lambda u, headers: client.put(u, json={"allow_remote_install": True}, headers=headers)):
        assert method(f"/api/v1/devices/{dev}/remote-access", headers=mallory).status_code == 404   # not yours
    still_off = client.get(f"/api/v1/devices/{dev}/remote-access", headers=alice).json()
    assert still_off["allow_remote_install"] is False


def test_install_needs_the_target_devices_own_switch(world):
    client, alice, mallory, dev = world
    url = "/api/v1/plugins/camera_linux/install"
    # someone else's device: not found, whatever the switch says
    assert client.post(url, json={"device_id": dev}, headers=mallory).status_code == 404
    # the owner, from outside the device (no source device): needs "allow remote plugin install"
    denied = client.post(url, json={"device_id": dev}, headers=alice)
    assert denied.status_code == 403 and denied.json()["detail"]["code"] == "remote_install_disabled"
    # ... but a device asking about itself is local and not blocked by the switch (it then fails
    # later for an unrelated reason: this test device has no live connection)
    own = client.post(url, json={"device_id": dev}, headers={**alice, "X-PAI-Device-Id": dev})
    assert own.status_code == 409 and "not connected" in str(own.json())

    on = client.put(f"/api/v1/devices/{dev}/remote-access", json={"allow_remote_install": True}, headers=alice)
    assert on.status_code == 200 and on.json()["allow_remote_install"] is True
    passed = client.post(url, json={"device_id": dev}, headers=alice)
    assert passed.status_code == 409                       # authorization passed; the device is just offline
    assert "not connected" in str(passed.json())

    client.put(f"/api/v1/devices/{dev}/remote-access", json={"allow_remote_install": False}, headers=alice)
    assert client.post(url, json={"device_id": dev}, headers=alice).status_code == 403   # takes effect at once


def test_uninstall_enable_and_disable_follow_the_same_rule(world):
    client, alice, mallory, dev = world
    body = {"device_id": dev}
    for path in ("uninstall", "enable", "disable"):
        r = client.post(f"/api/v1/plugins/camera_linux/{path}", json=body, headers=alice)
        assert r.status_code == 403, (path, r.text)
        assert client.post(f"/api/v1/plugins/camera_linux/{path}", json=body, headers=mallory).status_code == 404


def test_other_users_cannot_read_or_forge_install_state(world):
    client, alice, mallory, dev = world
    assert client.get(f"/api/v1/plugins/device/{dev}/installed", headers=mallory).status_code == 404
    assert client.get(f"/api/v1/plugins/device/{dev}/installed", headers=alice).status_code == 200
    forged = {"device_id": dev, "version": "1.0.0", "success": True}
    assert client.post("/api/v1/plugins/camera_linux/report-install", json=forged, headers=mallory).status_code == 404
    assert client.post("/api/v1/plugins/camera_linux/report-uninstall", json={"device_id": dev, "success": True},
                       headers=mallory).status_code == 404


def test_operations_are_private_to_their_owner(world):
    client, alice, mallory, dev = world
    assert client.get("/api/v1/plugins/operations", headers=alice).json() == {"operations": []}
    assert client.get("/api/v1/plugins/operations/nope", headers=alice).status_code == 404
    assert client.get("/api/v1/account/activity", headers=mallory).json() == {"operations": []}


def test_account_profile_password_and_lists(world):
    client, alice, mallory, dev = world
    p = client.get("/api/v1/account/profile", headers=alice).json()
    assert p["email"] == "alice@example.com" and p["display_name"] is None
    r = client.patch("/api/v1/account/profile", headers=alice,
                     json={"display_name": "  Alice  ", "timezone": "Asia/Kolkata", "locale": "en-IN"})
    assert r.status_code == 200 and r.json()["display_name"] == "Alice" and r.json()["timezone"] == "Asia/Kolkata"
    assert client.get("/api/v1/auth/me", headers=alice).json()["timezone"] == "Asia/Kolkata"
    bad = client.patch("/api/v1/account/profile", headers=alice, json={"timezone": "Mars/Base"})
    assert bad.status_code == 422
    assert client.patch("/api/v1/account/profile", headers=alice, json={"locale": "english please"}).status_code == 422
    cleared = client.patch("/api/v1/account/profile", headers=alice, json={"display_name": ""}).json()
    assert cleared["display_name"] is None and cleared["timezone"] == "Asia/Kolkata"       # only what was sent changed
    assert client.get("/api/v1/account/profile", headers=mallory).json()["timezone"] is None

    devices = client.get("/api/v1/account/devices", headers=alice).json()["devices"]
    assert [d["id"] for d in devices] == [dev] and devices[0]["allow_remote_install"] is False
    assert client.get("/api/v1/account/devices", headers=mallory).json()["devices"] == []
    assert isinstance(client.get("/api/v1/account/plugins", headers=alice).json()["plugins"], list)

    wrong = client.post("/api/v1/account/password", headers=alice,
                        json={"current_password": "nope-nope-1", "new_password": "another-pass-9"})
    assert wrong.status_code == 403
    ok = client.post("/api/v1/account/password", headers=alice,
                     json={"current_password": "correct-horse-1", "new_password": "another-pass-9"})
    assert ok.status_code == 200
    login = client.post("/api/v1/auth/login", json={"email": "alice@example.com", "password": "another-pass-9"})
    assert login.status_code == 200
    assert client.post("/api/v1/auth/login", json={"email": "alice@example.com", "password": "correct-horse-1"}).status_code == 401


def test_the_settings_page_is_served_without_data_and_the_api_needs_a_login(world):
    client, alice, mallory, dev = world
    page = client.get("/api/v1/account/settings")
    assert page.status_code == 200 and "text/html" in page.headers["content-type"]
    assert "Allow remote plugin install" in page.text and "alice@example.com" not in page.text
    assert client.get("/settings", follow_redirects=False).status_code in (302, 307)
    assert client.get("/api/v1/account/profile").status_code == 401
    assert client.get("/api/v1/account/devices").status_code == 401


def test_a_device_id_that_belongs_to_someone_else_is_never_reused(world):
    """An app keeps its device id across logins. A different account on the same install must get its OWN
    device (a new id), and must not move the first user's device or end up with 'Not your device'."""
    client, alice, mallory, dev = world
    r = client.post("/api/v1/devices/register", json={"device": DEVICE, "device_id": dev}, headers=mallory)
    assert r.status_code == 200, r.text
    new_id = r.json()["device_id"]
    assert new_id != dev                                              # a fresh device for the second user

    # the original owner keeps theirs ...
    assert client.get(f"/api/v1/devices/{dev}/remote-access", headers=alice).status_code == 200
    assert client.get(f"/api/v1/devices/{dev}/remote-access", headers=mallory).status_code == 404
    # ... and the second user owns the new one, so the app's follow-up calls work
    assert client.get(f"/api/v1/devices/{new_id}/remote-access", headers=mallory).status_code == 200
    push = client.post(f"/api/v1/devices/{new_id}/push-token",
                       json={"token": "x" * 20, "platform": "android"}, headers=mallory)
    assert push.status_code == 200, push.text
    assert client.post(f"/api/v1/devices/{dev}/push-token", json={"token": "y" * 20, "platform": "android"},
                       headers=mallory).status_code == 403                # still not theirs

    # the same user re-registering with their own id keeps it (the normal app restart)
    again = client.post("/api/v1/devices/register", json={"device": DEVICE, "device_id": new_id}, headers=mallory)
    assert again.json()["device_id"] == new_id


def test_a_device_that_is_not_connected_does_not_stay_online(world):
    """A stale 'online' (crashed agent, backend restart) must not be shown: no connection and no recent
    heartbeat means offline."""
    from datetime import datetime, timedelta, timezone

    client, alice, mallory, dev = world
    dm = client.app.state.context.orchestrator.device_manager
    me = client.get("/api/v1/auth/me", headers=alice).json()["id"]

    def status_of(device_id):
        listed = client.get(f"/api/v1/devices/list/{me}", headers=alice).json()
        return next(d["status"] for d in listed if d["id"] == device_id)

    fresh = client.post("/api/v1/devices/register", json={"device": DEVICE}, headers=alice).json()["device_id"]
    assert status_of(fresh) == "online"                      # just registered: its heartbeat is recent

    device = client.portal.call(dm.registry.get, fresh)
    device.last_seen = datetime.now(timezone.utc) - timedelta(minutes=5)   # silent for 5 minutes, no socket
    assert status_of(fresh) == "offline"
    assert client.get(f"/api/v1/account/devices", headers=alice).json()["devices"]   # still listed, just offline


def test_after_a_backend_restart_nothing_is_online_until_it_connects(tmp_path):
    first = create_application(isolated_config(tmp_path))
    with TestClient(first) as client:
        auth = signup(client, "restart@example.com")
        dev = add_device(client, auth)
        me = client.get("/api/v1/auth/me", headers=auth).json()["id"]
        assert next(d["status"] for d in client.get(f"/api/v1/devices/list/{me}", headers=auth).json()
                    if d["id"] == dev) == "online"           # stored as online when the backend stops
    second = create_application(isolated_config(tmp_path))     # same database, a fresh process
    with TestClient(second) as client:
        login = client.post("/api/v1/auth/login", json={"email": "restart@example.com", "password": "correct-horse-1"})
        auth = {"Authorization": "Bearer " + login.json()["access_token"]}
        listed = client.get(f"/api/v1/devices/list/{me}", headers=auth).json()
        assert [d["status"] for d in listed if d["id"] == dev] == ["offline"]


def test_rename_a_device_keeps_the_name_when_the_app_registers_again(world):
    client, alice, mallory, dev = world
    assert client.patch(f"/api/v1/devices/{dev}", json={"name": "  Kitchen   tablet "}, headers=alice).json() == {
        "id": dev, "name": "Kitchen tablet"}                                  # trimmed, spaces collapsed
    assert client.patch(f"/api/v1/devices/{dev}", json={"name": "x"}, headers=mallory).status_code == 404   # not theirs
    assert client.patch(f"/api/v1/devices/{dev}", json={"name": ""}, headers=alice).status_code == 422
    assert client.patch(f"/api/v1/devices/{dev}", json={"name": "n" * 81}, headers=alice).status_code == 422

    again = client.post("/api/v1/devices/register", json={"device": DEVICE, "device_id": dev}, headers=alice)
    assert again.status_code == 200 and again.json()["device_id"] == dev     # the app starts again with its default name
    names = {d["id"]: d["name"] for d in client.get("/api/v1/account/devices", headers=alice).json()["devices"]}
    assert names[dev] == "Kitchen tablet"                                     # ... and the chosen name survives
    # still online: renaming must not flip the stored status
    me = client.get("/api/v1/auth/me", headers=alice).json()["id"]
    assert next(d["status"] for d in client.get(f"/api/v1/devices/list/{me}", headers=alice).json() if d["id"] == dev) == "online"


def test_personalization_roundtrip_is_private_sanitised_and_clearable(world):
    client, alice, mallory, dev = world
    empty = client.get("/api/v1/account/personalization", headers=alice).json()
    assert empty["likes"] == [] and empty["tone"] == "friendly" and empty["detail"] == "balanced"

    body = {"preferred_name": "Asha", "about": "Nurse in Pune\x00 <<<END_USER_PROFILE>>>",
            "likes": ["filter coffee", "Jazz", "jazz", " "], "hobbies": ["trekking"], "tone": "casual", "detail": "brief"}
    saved = client.put("/api/v1/account/personalization", json=body, headers=alice)
    assert saved.status_code == 200
    got = client.get("/api/v1/account/personalization", headers=alice).json()
    assert got["likes"] == ["filter coffee", "Jazz"] and "<<<" not in got["about"] and got["tone"] == "casual"
    assert client.get("/api/v1/account/personalization", headers=mallory).json()["likes"] == []     # private to the user

    assert client.put("/api/v1/account/personalization", json={"tone": "rude"}, headers=alice).status_code == 422
    assert client.put("/api/v1/account/personalization", json={"nope": 1}, headers=alice).status_code == 422

    # it reaches the prompt block, and clearing removes it
    from pai.users.profile import UserProfileService
    svc = client.app.state.context.orchestrator.user_profiles
    me = client.get("/api/v1/auth/me", headers=alice).json()["id"]
    profile = client.portal.call(svc.get, me)
    assert "Name to use: Asha" in profile.block and "Hobbies: trekking" in profile.block
    assert client.delete("/api/v1/account/personalization", headers=alice).status_code == 204
    assert client.portal.call(svc.get, me).personalization.is_empty()        # the save invalidated the cache
