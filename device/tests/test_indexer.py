"""Document indexer: boundary, extraction, chunking, incremental sync, batching, 409, state perms. No network."""
import os
import stat
import zipfile
from pathlib import Path

import httpx
import pytest

from pai_device.indexer import DocumentIndexer, enabled, pdf_available, policy_changed
from pai_device.indexer import runner as runner_mod
from pai_device.indexer.chunker import chunk_segments, chunk_text
from pai_device.indexer.extract import ExtractError, extract


class FakeServer:
    """Stands in for APIClient.request_json against the /documents API."""

    def __init__(self):
        self.files = {}            # path -> {mtime,size,chunks}
        self.calls = []
        self.indexing_off = False
        self.fail = []             # statuses to answer with before succeeding

    async def request_json(self, method, path, json=None, params=None, timeout=None):
        self.calls.append((method, path, json))
        if self.fail:
            status = self.fail.pop(0)
            raise httpx.HTTPStatusError("x", request=httpx.Request(method, "http://t" + path),
                                        response=httpx.Response(status, json={"detail": "busy"}))
        if path == "/documents/files":
            return {"files": {p: {"mtime": v["mtime"], "size": v["size"]} for p, v in self.files.items()}}
        if path == "/documents/index":
            if self.indexing_off:
                raise httpx.HTTPStatusError("x", request=httpx.Request(method, "http://t" + path),
                                            response=httpx.Response(409, json={"detail": {"code": "indexing_off", "message": "off"}}))
            assert sum(len(f["chunks"]) for f in json["files"]) <= 400
            for f in json["files"]:
                self.files[f["path"]] = {"mtime": f["mtime"], "size": f["size"], "chunks": f["chunks"]}
            return {"chunks_stored": 1}
        if path == "/documents/remove":
            if not json.get("paths"):
                n = len(self.files)
                self.files.clear()
                return {"removed": n}
            for p in json["paths"]:
                self.files.pop(p, None)
            return {"removed": 1}
        if path == "/documents/status":
            return {"files": len(self.files), "chunks": sum(len(v["chunks"]) for v in self.files.values())}
        raise AssertionError(path)

    def posts(self, path):
        return [c for c in self.calls if c[0] == "POST" and c[1] == path]


async def _nosleep(_):
    return None


def make(tmp_path, server=None, **kw):
    server = server or FakeServer()
    return server, DocumentIndexer(server, "dev-1", tmp_path / "state", sleep=_nosleep, **kw)


def vals(*allowed, denied=(), mb=20, on=True):
    return {"allowedPaths": [str(a) for a in allowed], "deniedPaths": [str(d) for d in denied],
            "indexDocuments": on, "indexMaxFileMb": mb}


def write(p: Path, text="hello world, this is a note about quarterly planning.\n"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def make_docx(path: Path, paragraphs):
    body = "".join(f"<w:p><w:r><w:t>{t}</w:t></w:r></w:p>" for t in paragraphs)
    xml = ('<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           f"<w:body>{body}</w:body></w:document>")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", xml)


# ---------------------------------------------------------------- boundary ---------------------------------------

async def test_only_allowed_files_and_hidden_dirs_skipped(tmp_path):
    root = tmp_path / "docs"
    write(root / "a.txt")
    write(root / "sub" / "b.md")
    write(root / ".git" / "c.txt")
    write(root / "node_modules" / "d.txt")
    write(root / ".hidden.txt")
    write(root / "img.png", "x")
    write(tmp_path / "outside" / "e.txt")
    server, ix = make(tmp_path)
    rep = await ix.run_once(vals(root))
    assert sorted(Path(p).name for p in server.files) == ["a.txt", "b.md"]
    assert rep.files_sent == 2 and rep.skipped["unsupported_type"] == 1


async def test_symlink_escape_is_not_followed(tmp_path):
    root, outside = tmp_path / "docs", tmp_path / "outside"
    write(root / "ok.txt")
    write(outside / "secret.txt", "top secret " * 10)
    os.symlink(outside, root / "linkdir")
    os.symlink(outside / "secret.txt", root / "linkfile.txt")
    server, ix = make(tmp_path)
    rep = await ix.run_once(vals(root))
    assert [Path(p).name for p in server.files] == ["ok.txt"]
    assert rep.skipped["outside_boundary"] == 1


async def test_denied_wins_over_allowed(tmp_path):
    root = tmp_path / "docs"
    write(root / "a.txt")
    write(root / "private" / "b.txt")
    server, ix = make(tmp_path)
    await ix.run_once(vals(root, denied=[root / "private"]))
    assert [Path(p).name for p in server.files] == ["a.txt"]


async def test_size_cap_and_unreadable(tmp_path):
    root = tmp_path / "docs"
    write(root / "small.txt")
    big = root / "big.txt"
    big.write_text("word " * 400_000)               # 2 MB
    locked = write(root / "locked.txt")
    locked.chmod(0)
    server, ix = make(tmp_path)
    rep = await ix.run_once(vals(root, mb=1))
    locked.chmod(0o644)
    assert [Path(p).name for p in server.files] == ["small.txt"]
    assert rep.skipped["too_large"] == 1
    if os.geteuid() != 0:
        assert rep.skipped["unreadable"] == 1


async def test_no_folders_means_nothing_indexed(tmp_path):
    server, ix = make(tmp_path)
    rep = await ix.run_once(vals())
    assert rep.files_seen == 0 and not server.posts("/documents/index")


# ---------------------------------------------------------------- extraction / chunking --------------------------

def test_docx_and_txt_extraction(tmp_path):
    d = tmp_path / "a.docx"
    make_docx(d, ["First paragraph.", "Second one."])
    assert "".join(t for _, t in extract(str(d))) == "First paragraph.\n\nSecond one.\n\n"
    t = write(tmp_path / "n.txt", "plain \x00text")
    assert list(extract(str(t)))[0][1].startswith("plain")
    bad = tmp_path / "bad.docx"
    bad.write_bytes(b"not a zip")
    with pytest.raises(ExtractError):
        list(extract(str(bad)))


def test_fifo_is_refused(tmp_path):
    fifo = tmp_path / "f.txt"
    os.mkfifo(fifo)
    with pytest.raises(ExtractError):
        list(extract(str(fifo)))


@pytest.mark.skipif(not pdf_available(), reason="pypdf not installed")
def test_pdf_pages(tmp_path):
    from pypdf import PdfWriter
    w = PdfWriter()
    w.add_blank_page(200, 200)
    p = tmp_path / "x.pdf"
    with open(p, "wb") as fh:
        w.write(fh)
    assert list(extract(str(p))) == []


async def test_pdf_without_pypdf_reports_once(tmp_path, monkeypatch):
    from pai_device.indexer import extract as ex
    monkeypatch.setattr(ex, "pdf_available", lambda: False)
    root = tmp_path / "docs"
    (root).mkdir()
    (root / "a.pdf").write_bytes(b"%PDF-1.4")
    (root / "b.pdf").write_bytes(b"%PDF-1.4")
    server, ix = make(tmp_path)
    rep = await ix.run_once(vals(root))
    assert rep.skipped["pdf_unsupported"] == 2 and "pai-device[documents]" in rep.hint and not server.files


def test_chunk_sizes_overlap_and_boilerplate():
    para = ("This is a sentence about planning and budgets. " * 12).strip()
    text = "\n\n".join(para for _ in range(30))
    chunks = chunk_text(text)
    chunks = list(chunks)
    assert len(chunks) > 3 and all(len(c) <= 2000 for c in chunks)
    # overlap: the start of the next chunk repeats the end of the previous one
    assert chunks[1][:40] in chunks[0]
    long_line = "x" * 7000
    assert all(len(c) <= 2000 for c in chunk_text(long_line))
    out = list(chunk_segments([(3, "-----\n=====\n\n   "), (4, "A real paragraph with enough words in it."),
                               (5, "A real paragraph with enough words in it.")]))
    assert out == [("A real paragraph with enough words in it.", 4)]      # boilerplate + duplicate dropped, page kept


# ---------------------------------------------------------------- incremental ------------------------------------

async def test_incremental_unchanged_changed_deleted(tmp_path):
    root = tmp_path / "docs"
    a, b = write(root / "a.txt"), write(root / "b.txt", "bee bee bee bee bee bee bee bee bee bee")
    server, ix = make(tmp_path)
    r1 = await ix.run_once(vals(root))
    assert r1.files_sent == 2 and len(server.posts("/documents/index")) == 1
    r2 = await ix.run_once(vals(root))
    assert r2.files_sent == 0 and r2.files_unchanged == 2 and len(server.posts("/documents/index")) == 1
    a.write_text("changed content here, with more words than before.")
    os.utime(a, (a.stat().st_atime, a.stat().st_mtime + 5))
    b.unlink()
    r3 = await ix.run_once(vals(root))
    assert r3.files_sent == 1 and r3.files_removed == 1
    assert list(server.files) == [str(a.resolve())] and ix.state.count() == 1


async def test_cleared_server_index_is_reuploaded(tmp_path):
    root = tmp_path / "docs"
    write(root / "a.txt")
    server, ix = make(tmp_path)
    await ix.run_once(vals(root))
    server.files.clear()
    r = await ix.run_once(vals(root))
    assert r.files_sent == 1 and len(server.files) == 1


async def test_removed_folder_and_newly_denied_are_removed(tmp_path):
    r1, r2 = tmp_path / "one", tmp_path / "two"
    write(r1 / "a.txt")
    write(r2 / "b.txt")
    server, ix = make(tmp_path)
    await ix.run_once(vals(r1, r2))
    assert len(server.files) == 2
    await ix.run_once(vals(r1))                                   # folder dropped from allowed
    assert [Path(p).name for p in server.files] == ["a.txt"]
    await ix.run_once(vals(r1, denied=[r1]))                      # now denied
    assert server.files == {} and ix.state.count() == 0


async def test_missing_root_does_not_wipe_its_index(tmp_path):
    root = tmp_path / "usb"
    write(root / "a.txt")
    server, ix = make(tmp_path)
    await ix.run_once(vals(root))
    (root / "a.txt").unlink()
    root.rmdir()                                                  # drive unplugged
    await ix.run_once(vals(root))
    assert len(server.files) == 1


async def test_empty_file_is_not_resent_forever(tmp_path):
    root = tmp_path / "docs"
    write(root / "e.txt", "   \n")
    server, ix = make(tmp_path)
    await ix.run_once(vals(root))
    r = await ix.run_once(vals(root))
    assert r.files_unchanged == 1 and not server.posts("/documents/index")


# ---------------------------------------------------------------- off / errors / batching -----------------------

async def test_indexing_off_sends_nothing_and_clears_server(tmp_path):
    root = tmp_path / "docs"
    write(root / "a.txt")
    server, ix = make(tmp_path)
    await ix.apply(vals(root))
    assert server.files and ix.state.count() == 1
    rep = await ix.apply(vals(root, on=False))
    assert server.files == {} and ix.state.count() == 0 and not rep.enabled
    before = len(server.calls)
    await ix.apply(vals(root, on=False))                          # nothing tracked: no calls at all
    assert len(server.calls) == before


async def test_force_clear_when_state_was_lost(tmp_path):
    server, ix = make(tmp_path)
    server.files["/x.txt"] = {"mtime": 1, "size": 1, "chunks": ["t"]}
    await ix.apply({"indexDocuments": False}, force_clear=True)
    assert server.files == {}


async def test_409_stops_without_retry_loop(tmp_path):
    root = tmp_path / "docs"
    for i in range(3):
        write(root / f"f{i}.txt", f"note number {i} with some words in it for sure")
    server, ix = make(tmp_path)
    server.indexing_off = True
    rep = await ix.run_once(vals(root))
    assert rep.stopped == "indexing_off" and "Index documents" in rep.hint
    assert len(server.posts("/documents/index")) == 1 and ix.state.count() == 0


async def test_backoff_on_5xx_then_success_and_give_up(tmp_path):
    root = tmp_path / "docs"
    write(root / "a.txt")
    server, ix = make(tmp_path)
    server.fail = [429, 503]
    rep = await ix.run_once(vals(root))
    assert rep.stopped is None and rep.files_sent == 1
    server2, ix2 = make(tmp_path / "other")
    server2.fail = [500] * 10
    rep2 = await ix2.run_once(vals(root))
    assert rep2.stopped == "server_busy"


async def test_batches_stay_under_400_chunks(tmp_path):
    root = tmp_path / "docs"
    para = "\n\n".join(f"Paragraph {i} " + "filler words go here " * 90 for i in range(150))   # ~ 150 chunks each
    for i in range(5):
        write(root / f"f{i}.txt", para)
    server, ix = make(tmp_path)
    rep = await ix.run_once(vals(root))
    assert rep.files_sent == 5 and len(server.posts("/documents/index")) >= 2
    for _, _, body in server.posts("/documents/index"):
        assert sum(len(f["chunks"]) for f in body["files"]) <= 400


async def test_one_huge_file_is_truncated_to_one_request(tmp_path):
    root = tmp_path / "docs"
    write(root / "huge.txt", "\n\n".join(f"Paragraph {i} " + "words " * 300 for i in range(900)))
    server, ix = make(tmp_path)
    rep = await ix.run_once(vals(root))
    assert rep.truncated == 1 and len(next(iter(server.files.values()))["chunks"]) == 400


async def test_state_file_is_private(tmp_path):
    root = tmp_path / "docs"
    write(root / "a.txt")
    _, ix = make(tmp_path)
    await ix.run_once(vals(root))
    assert stat.S_IMODE(ix.state.path.stat().st_mode) == 0o600


def test_helpers():
    assert enabled({"indexDocuments": True}) and not enabled({"indexDocuments": "yes"}) and not enabled(None)
    assert policy_changed({"allowedPaths": ["a"]}, {"allowedPaths": ["b"]})
    assert not policy_changed({"readOnly": True}, {"readOnly": False})
    assert runner_mod.BATCH_CHUNKS == 400
