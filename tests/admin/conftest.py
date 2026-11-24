"""Fixtures for the admin tests. Helpers live in admin_helpers.py (a unique module name: several
test directories have a conftest.py, and `from conftest import ...` would pick the wrong one)."""
from __future__ import annotations

import os
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from admin_helpers import ADMIN_EMAIL, AdminHarness, isolated_config
from pai.server.factory import create_application


@pytest.fixture(scope="session")
def harness(tmp_path_factory) -> Iterator[AdminHarness]:
    tmp = tmp_path_factory.mktemp("admin_app")
    old = os.environ.get("PAI_ADMIN_EMAILS")
    os.environ["PAI_ADMIN_EMAILS"] = ADMIN_EMAIL
    app = create_application(isolated_config(tmp))
    try:
        with TestClient(app) as client:
            yield AdminHarness(client)
    finally:
        if old is None:
            os.environ.pop("PAI_ADMIN_EMAILS", None)
        else:
            os.environ["PAI_ADMIN_EMAILS"] = old
