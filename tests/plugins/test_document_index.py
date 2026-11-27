"""Document search: chunks per user/device, boundary enforced at write AND query time, remote-use rule."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from pai.documents.index import Chunk, DocumentIndex, IndexedFile, IndexError_, allowed_by_policy, policy_from_effective
from pai.documents.service import DocumentService
from pai.memory.utils import _matches_where


class FakeStore:
    """VectorStore stand-in: word-overlap 'search', Chroma-style where."""
    def __init__(self): self.docs: Dict[str, Dict[str, Any]] = {}; self.client = object()
    async def initialize(self): pass
    async def add_documents(self, texts, metadatas, ids):
        for t, m, i in zip(texts, metadatas, ids): self.docs[i] = {"id": i, "text": t, "metadata": m}
        return len(texts)
    async def delete_by_metadata(self, where):
        gone = [i for i, d in self.docs.items() if _matches_where(d["metadata"], where)]
        for i in gone: del self.docs[i]
        return len(gone)
    async def delete_many(self, ids):
        n = 0
        for i in ids: n += self.docs.pop(i, None) is not None
        return n
    async def get_by_metadata(self, where, limit=100, **_):
        return [d for d in self.docs.values() if _matches_where(d["metadata"], where)][:limit]
    async def count(self, *, where=None):
        return len([d for d in self.docs.values() if not where or _matches_where(d["metadata"], where)])
    async def search(self, query, limit=5, *, where=None):
        q = set(query.lower().split())
        scored = [(len(q & set(d["text"].lower().split())), d) for d in self.docs.values()
                  if not where or _matches_where(d["metadata"], where)]
        return [{**d, "distance": 1 - s} for s, d in sorted(scored, key=lambda x: -x[0]) if s > 0][:limit]


POLICY = {"allowed": ["/home/me/Documents"], "denied": ["/home/me/Documents/private"], "read_only": False}


def test_boundary_rules():
    assert allowed_by_policy("/home/me/Documents/a.pdf", POLICY)
    assert not allowed_by_policy("/home/me/Documents/private/a.pdf", POLICY)
    assert not allowed_by_policy("/home/me/Documents2/a.pdf", POLICY)          # prefix sibling
    assert not allowed_by_policy("/etc/passwd", POLICY)
    assert not allowed_by_policy("/home/me/x", {"allowed": [], "denied": []})  # nothing allowed = nothing indexed
    assert policy_from_effective({"allowedPaths": ["/a"]}) is None             # indexing switch off
    assert policy_from_effective({"indexDocuments": True, "allowedPaths": ["/a"]})["allowed"] == ["/a"]


async def test_write_refuses_files_outside_the_boundary_and_replace_drops_old_chunks():
    idx = DocumentIndex(FakeStore())
    r = await idx.replace_files("u", "d1", [
        IndexedFile("/home/me/Documents/ins.pdf", 1, 10, [Chunk("flood cover excluded", 2), Chunk("fire cover included", 3)]),
        IndexedFile("/home/me/Documents/private/x.txt", 1, 1, [Chunk("secret")]),
        IndexedFile("/etc/shadow", 1, 1, [Chunk("root")])], policy=POLICY)
    assert r == {"chunks_stored": 2, "files_skipped": 2}
    await idx.replace_files("u", "d1", [IndexedFile("/home/me/Documents/ins.pdf", 2, 5, [Chunk("only one now")])], policy=POLICY)
    assert await idx.count("u", "d1") == 1


async def test_search_is_per_user_and_filters_stale_chunks_of_folders_no_longer_allowed():
    idx = DocumentIndex(FakeStore())
    wide = {"allowed": ["/home/me"], "denied": [], "read_only": False}
    await idx.replace_files("alice", "d1", [IndexedFile("/home/me/Documents/ins.pdf", 1, 1, [Chunk("flood cover excluded")]),
                                            IndexedFile("/home/me/Taxes/t.pdf", 1, 1, [Chunk("flood tax refund")])], policy=wide)
    await idx.replace_files("bob", "d9", [IndexedFile("/home/me/Documents/b.pdf", 1, 1, [Chunk("flood bob secret")])], policy=wide)
    hits = await idx.search("alice", "flood", policies={"d1": wide})
    assert {h["path"] for h in hits} == {"/home/me/Documents/ins.pdf", "/home/me/Taxes/t.pdf"}   # never bob's
    narrowed = {"allowed": ["/home/me/Documents"], "denied": [], "read_only": False}   # user removed Taxes later
    hits = await idx.search("alice", "flood", policies={"d1": narrowed})
    assert [h["path"] for h in hits] == ["/home/me/Documents/ins.pdf"]
    assert await idx.search("alice", "flood", policies={}) == []                         # no device allowed = nothing


async def test_limits_and_removal():
    idx = DocumentIndex(FakeStore())
    with pytest.raises(IndexError_):
        await idx.replace_files("u", "d", [IndexedFile("/home/me/Documents/a", 1, 1, [Chunk("x")] * 501)], policy=POLICY)
    await idx.replace_files("u", "d", [IndexedFile("/home/me/Documents/a.txt", 1, 1, [Chunk("alpha")]),
                                       IndexedFile("/home/me/Documents/sub/b.txt", 1, 1, [Chunk("beta")])], policy=POLICY)
    assert await idx.remove("u", "d", prefix="/home/me/Documents/sub") == 1
    assert (await idx.status("u", "d"))["files"] == 1
    assert await idx.remove("u", "d") == 1 and await idx.count("u") == 0


# ── service: remote-use rule and the indexing switch ─────────────────────────

class Devices:
    def __init__(self, rows): self.rows = rows
    async def get(self, did): return self.rows.get(did)
    async def list_devices(self, user_id): return [d for d in self.rows.values() if d.user_id == user_id]


class Plugins:
    def __init__(self, effective): self.effective = effective
    async def list_device_plugins(self, did): return [{"plugin_id": "files", "is_installed": True}] if did in self.effective else []
    async def get_device_config(self, did, pid): return {"effective": self.effective[did]}


def _svc(effective, devices):
    return DocumentService(DocumentIndex(FakeStore()), Plugins(effective), Devices(devices))


async def test_search_skips_devices_with_indexing_off_or_remote_use_off():
    on = {"indexDocuments": True, "allowedPaths": ["/home/me"]}
    devs = {"phone": SimpleNamespace(id="phone", user_id="u", allow_remote_invoke=False),
            "pc": SimpleNamespace(id="pc", user_id="u", allow_remote_invoke=False),
            "off": SimpleNamespace(id="off", user_id="u", allow_remote_invoke=True),
            "other": SimpleNamespace(id="other", user_id="someone-else", allow_remote_invoke=True)}
    svc = _svc({"phone": on, "pc": {**on}, "off": {"allowedPaths": ["/home/me"]}, "other": on}, devs)
    # from the phone itself: only the phone (the pc denies remote use); 'off' has indexing off; 'other' is not u's
    assert set(await svc.search_policies("u", "phone")) == {"phone"}
    # from the web (no source device): remote use is required everywhere
    assert await svc.search_policies("u", None) == {}
    devs["pc"].allow_remote_invoke = True
    assert set(await svc.search_policies("u", "phone")) == {"phone", "pc"}


# ── routes ───────────────────────────────────────────────────────────────────

def _client(effective, devices, user="u"):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from pai.api.dependencies import get_current_user
    from pai.api.routes import documents as routes
    from pai.app_context import get_app_context
    svc = _svc(effective, devices)
    ctx = SimpleNamespace(orchestrator=SimpleNamespace(documents=svc, device_manager=svc.devices))
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_app_context] = lambda: ctx
    return TestClient(app)


def test_only_the_device_itself_can_index_and_only_when_indexing_is_on():
    on = {"indexDocuments": True, "allowedPaths": ["/home/me/Documents"]}
    devs = {"phone": SimpleNamespace(id="phone", user_id="u", allow_remote_invoke=True)}
    body = {"device_id": "phone", "files": [{"path": "/home/me/Documents/a.txt", "chunks": [{"text": "hello flood"}]}]}
    c = _client({"phone": on}, devs)
    assert c.post("/documents/index", json=body).status_code == 403                        # no device header (web)
    assert c.post("/documents/index", json=body, headers={"X-PAI-Device-Id": "other"}).status_code == 403
    ok = c.post("/documents/index", json=body, headers={"X-PAI-Device-Id": "phone"})
    assert ok.status_code == 200 and ok.json()["chunks_stored"] == 1
    r = c.post("/documents/search", json={"query": "flood"}, headers={"X-PAI-Device-Id": "phone"})
    assert [x["path"] for x in r.json()["results"]] == ["/home/me/Documents/a.txt"]
    off = _client({"phone": {"allowedPaths": ["/home/me/Documents"]}}, devs)
    assert off.post("/documents/index", json=body, headers={"X-PAI-Device-Id": "phone"}).status_code == 409
    stranger = _client({"phone": on}, devs, user="mallory")
    assert stranger.post("/documents/remove", json={"device_id": "phone"}).status_code == 404
