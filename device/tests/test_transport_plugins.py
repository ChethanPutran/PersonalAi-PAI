"""APIClient plugin methods must hit routes the backend really serves."""
import httpx
import pytest

from pai_device.transport import APIClient

INFO = {"id": "camera_linux", "name": "Camera", "version": "1.0.0", "compatible": True,
        "platforms": {"linux": {"artifact": "linux/x", "entrypoint": "x"}},
        "packageUrl": "/api/v1/plugins/artifacts/camera/1.0.0/linux.tar.gz", "checksum": "sha256:ABC"}


def _client(handler) -> APIClient:
    c = APIClient("http://t/api/v1", "e", "p")
    c._http = httpx.AsyncClient(base_url="http://t/api/v1", transport=httpx.MockTransport(handler))

    async def _tok():
        c._token = "tok"
        return "tok"
    c.ensure_token = _tok
    return c


@pytest.mark.asyncio
async def test_index_manifest_and_download(tmp_path):
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.url.path, dict(req.url.params), req.headers.get("authorization")))
        if req.url.path == "/api/v1/plugins/catalog":
            return httpx.Response(200, json={"dev1": [INFO], "dev2": [INFO, {**INFO, "id": "x", "compatible": False}]})
        if req.url.path == "/api/v1/plugins/camera_linux/manifest.json":
            return httpx.Response(200, json={"id": "camera_linux"})
        if req.url.path == "/api/v1/plugins/artifacts/camera/1.0.0/linux.tar.gz":
            return httpx.Response(200, content=b"pkg")
        return httpx.Response(404)

    c = _client(handler)
    index = await c.fetch_index()
    assert [p["id"] for p in index["plugins"]] == ["camera_linux"]
    v = index["plugins"][0]["versions"]["1.0.0"]
    assert v["sha256"] == {"linux": "abc"}      # prefix stripped, lower-cased and list(v["artifacts"]) == ["linux"]

    assert (await c.fetch_manifest("camera_linux", "1.0.0"))["id"] == "camera_linux"
    assert seen[-1][1] == {"version": "1.0.0"}

    out = await c.download_artifact(INFO["packageUrl"], tmp_path / "d" / "p.tar.gz")
    assert out.read_bytes() == b"pkg" and seen[-1][2] == "Bearer tok"


@pytest.mark.asyncio
async def test_absolute_url_never_gets_our_token(tmp_path):
    seen = []

    def handler(req):
        seen.append(req.headers.get("authorization"))
        return httpx.Response(200, content=b"x")

    await _client(handler).download_artifact("https://s3.example/pkg?sig=1", tmp_path / "p")
    assert seen == [None]


@pytest.mark.asyncio
async def test_every_call_names_the_device_it_comes_from():
    seen = []

    def handler(req):
        seen.append(req.headers.get("x-pai-device-id"))
        return httpx.Response(200, json={})

    c = _client(handler)
    await c._request("GET", "/plugins/x")           # before registration: no header
    c.device_id = "dev-1"
    await c._request("GET", "/plugins/x")
    assert seen == [None, "dev-1"]
