"""Remote plugin install / use is owner-controlled, per device, and fail-closed."""
from types import SimpleNamespace

import pytest

from pai.security.remote_access import (
    DEVICE_NOT_FOUND, REMOTE_INSTALL_DISABLED, REMOTE_INVOKE_DISABLED, remote_access_denial,
)


class Devices:
    def __init__(self, *devices):
        self._d = {d.id: d for d in devices}

    async def get(self, device_id):
        return self._d.get(device_id)


def dev(id, user="u1", install=False, invoke=False, name="Laptop"):
    return SimpleNamespace(id=id, user_id=user, name=name,
                           allow_remote_install=install, allow_remote_invoke=invoke)


async def check(devices, *, target="B", source="A", kind="install", user="u1"):
    return await remote_access_denial(
        devices, user_id=user, target_device_id=target, source_device_id=source, kind=kind)


@pytest.mark.asyncio
async def test_remote_is_denied_until_the_target_device_opts_in():
    devices = Devices(dev("A"), dev("B"))
    assert (await check(devices, kind="install")).code == REMOTE_INSTALL_DISABLED
    assert (await check(devices, kind="invoke")).code == REMOTE_INVOKE_DISABLED


@pytest.mark.asyncio
async def test_each_switch_is_independent():
    only_install = Devices(dev("A"), dev("B", install=True))
    assert await check(only_install, kind="install") is None
    assert (await check(only_install, kind="invoke")).code == REMOTE_INVOKE_DISABLED
    only_invoke = Devices(dev("A"), dev("B", invoke=True))
    assert await check(only_invoke, kind="invoke") is None
    assert (await check(only_invoke, kind="install")).code == REMOTE_INSTALL_DISABLED


@pytest.mark.asyncio
async def test_a_device_acting_on_itself_is_local_and_always_allowed():
    devices = Devices(dev("B"))
    assert await check(devices, target="B", source="B", kind="install") is None
    assert await check(devices, target="B", source="B", kind="invoke") is None


@pytest.mark.asyncio
async def test_no_source_device_counts_as_remote():
    """The web, a schedule or an agent has no source device: it needs the opt-in like any remote caller."""
    devices = Devices(dev("B"))
    assert (await check(devices, source=None, kind="invoke")).code == REMOTE_INVOKE_DISABLED


@pytest.mark.asyncio
async def test_someone_elses_or_unknown_device_is_not_found_even_if_opted_in_and_spoofed_local():
    devices = Devices(dev("B", user="victim", install=True, invoke=True))
    for kind in ("install", "invoke"):
        denial = await check(devices, target="B", source="B", kind=kind, user="attacker")
        assert denial.code == DEVICE_NOT_FOUND                  # claiming to be B does not help
    assert (await check(devices, target="nope")).code == DEVICE_NOT_FOUND
    assert (await check(devices, target=None)).code == DEVICE_NOT_FOUND


@pytest.mark.asyncio
async def test_message_names_the_device_and_says_how_to_fix_it():
    denial = await check(Devices(dev("B", name="Work PC")), kind="invoke")
    assert "Work PC" in denial.message and "Allow remote plugin use" in denial.message
