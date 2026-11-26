"""pai-device voice: the wire protocol with a fake socket and a fake audio backend (no hardware)."""
import argparse
import asyncio
import io
import json
import sys
from pathlib import Path

import pytest
import websockets
from websockets.frames import Close

sys.path.insert(0, str(Path(__file__).parent))
from cli_kit import Routes, make_client, make_ctx  # noqa: E402

from pai_device import voicecli  # noqa: E402
from pai_device.voicecli import AudioUnavailable, SoundDeviceAudio, VoiceClient  # noqa: E402

READY = {"type": "session.ready", "voice_session_id": "v1", "chat_session_id": "c1", "input_sample_rate": 16000,
         "output_sample_rate": 24000, "output_format": "pcm_s16le",
         "limits": {"max_utterance_seconds": 30, "idle_timeout_seconds": 120, "max_session_seconds": 3600},
         "capabilities": {"stt": "airouter", "tts": "piper", "partial_transcripts": False, "barge_in": True}}


class FakeWS:
    def __init__(self, script, then=None):
        self.script, self.then, self.sent, self.closed = list(script), then, [], False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.closed = True

    async def send(self, data):
        self.sent.append(data)

    def json_sent(self):
        return [json.loads(m) for m in self.sent if isinstance(m, str)]

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for item in self.script:
            await asyncio.sleep(0)
            yield item if isinstance(item, (bytes, str)) else json.dumps(item)
        if self.then is not None:
            raise self.then
        await asyncio.Event().wait()                 # stay open until the client leaves


class FakeAudio:
    def __init__(self):
        self.can_play, self.started, self.played, self.cleared, self.stopped = True, None, [], 0, False
        self._playing = False

    async def start(self, on_frame, *, input_rate, output_rate, want_input):
        self.started = (input_rate, output_rate, want_input)
        self.on_frame = on_frame

    def play(self, pcm):
        self.played.append(pcm)
        self._playing = True

    def clear(self):
        self.cleared += 1
        self._playing = False

    @property
    def playing(self):
        return self._playing

    async def stop(self):
        self.stopped = True


def make(tmp_path, ws, audio=None, **kw):
    ctx = make_ctx(make_client(Routes()), tmp_path)
    out = io.StringIO()
    audio = FakeAudio() if audio is None else audio
    client = VoiceClient(ctx, audio, connect=lambda url, **k: ws, out=out, device_id="dev-1", **kw)
    return client, audio, out


@pytest.mark.asyncio
async def test_session_start_then_a_full_spoken_turn(tmp_path, capsys):
    ws = FakeWS([
        READY,
        {"type": "state", "state": "listening", "turn_id": "t1"},
        {"type": "transcript.final", "turn_id": "t1", "text": "what time is it"},
        {"type": "assistant.status", "turn_id": "t1", "stage": "planning", "message": "Understanding your request..."},
        {"type": "assistant.text.delta", "turn_id": "t1", "response_id": "r1", "delta": "It is "},
        {"type": "assistant.text.delta", "turn_id": "t1", "response_id": "r1", "delta": "noon."},
        {"type": "assistant.audio.start", "response_id": "r1", "sample_rate": 24000, "format": "pcm_s16le"},
        b"\x01\x02" * 100, b"\x03\x04" * 100,
        {"type": "assistant.audio.end", "response_id": "r1"},
        {"type": "assistant.text.done", "turn_id": "t1", "response_id": "r1", "text": "It is noon."},
        {"type": "session.ended", "reason": "client_left"},
    ])
    client, audio, out = make(tmp_path, ws, language="en")
    assert await client.run() == 0
    start = ws.json_sent()[0]
    assert start == {"type": "session.start", "input_sample_rate": 16000, "language": "en", "device_id": "dev-1",
                     "client": {"name": "pai-device-cli", "version": "1"}}        # first frame; no empty fields
    assert audio.started == (16000, 24000, True)                                 # rates come from session.ready
    assert audio.played == [b"\x01\x02" * 100, b"\x03\x04" * 100]
    assert "you: what time is it\n" in out.getvalue() and "pai: It is noon.\n" in out.getvalue()
    assert "Understanding your request" in capsys.readouterr().err
    assert audio.stopped and ws.json_sent()[-1] == {"type": "session.end"}
    assert (tmp_path / "state" / "chat_session").read_text() == "c1"             # shared with `chat`


@pytest.mark.asyncio
async def test_interruption_drops_queued_audio_as_the_protocol_requires(tmp_path):
    ws = FakeWS([READY, b"\x00\x00" * 50,
                 {"type": "assistant.interrupted", "response_id": "r1", "reason": "barge_in", "spoken_text": "It"},
                 {"type": "session.ended", "reason": "x"}])
    client, audio, out = make(tmp_path, ws)
    await client.run()
    assert audio.cleared == 1 and not audio.playing


@pytest.mark.asyncio
async def test_a_fatal_error_ends_the_session_with_a_failure_code(tmp_path, capsys):
    ws = FakeWS([READY, {"type": "error", "code": "voice_unavailable", "message": "no speech engine", "fatal": True}])
    client, _, _ = make(tmp_path, ws)
    assert await client.run() == 1
    assert "✗ no speech engine" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_close_codes_become_plain_messages(tmp_path, capsys):
    ws = FakeWS([], then=websockets.ConnectionClosedError(Close(4429, ""), None))
    client, _, _ = make(tmp_path, ws)
    assert await client.run() == 1
    assert "too many voice sessions" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_microphone_frames_are_sent_but_gated_while_the_assistant_speaks(tmp_path):
    ws = FakeWS([READY])
    client, audio, _ = make(tmp_path, ws)
    task = asyncio.create_task(client.run())
    for _ in range(20):
        await asyncio.sleep(0)
    frame = b"\x10\x00" * 640
    client.on_frame(frame)                                   # idle: goes out
    await asyncio.sleep(0.01)
    assert frame in ws.sent
    audio.play(b"\x00\x00")                                  # assistant audio is playing: our own speaker
    client.on_frame(b"\x77" * 1280)
    await asyncio.sleep(0.01)
    assert b"\x77" * 1280 not in ws.sent
    client.barge_in = True                                   # headphones + --barge-in: talk over it
    client.on_frame(b"\x78" * 1280)
    await asyncio.sleep(0.01)
    assert b"\x78" * 1280 in ws.sent
    client._stop.set()
    await task


@pytest.mark.asyncio
async def test_push_to_talk_only_sends_while_talking_then_flushes_silence(tmp_path):
    ws = FakeWS([READY])
    presses = iter(["", ""])                                 # Enter to start, Enter to send

    async def read_line(prompt=""):
        try:
            return next(presses)
        except StopIteration:
            await asyncio.Event().wait()
    client, _, _ = make(tmp_path, ws, ptt=True, read_line_fn=read_line)
    client.on_frame(b"\x01" * 1280)
    assert client._frames.empty()                            # not talking: dropped
    task = asyncio.create_task(client.run())
    for _ in range(100):
        await asyncio.sleep(0)
    assert not client.talking                                # the second Enter already ended it
    silence = [m for m in ws.sent if isinstance(m, bytes)]
    assert len(silence) == 25 and set(silence) == {b"\x00" * 1280}      # 1 s of silence lets the server's VAD end the turn
    client._stop.set()
    await task


@pytest.mark.asyncio
async def test_text_only_sends_input_text_cancel_and_quits(tmp_path):
    ws = FakeWS([READY])
    lines = iter(["hello there", "", "/cancel", "/quit"])

    async def read_line(prompt=""):
        return next(lines)
    client, audio, out = make(tmp_path, ws, text_only=True, read_line_fn=read_line)
    assert await client.run() == 0
    sent = ws.json_sent()
    assert {"type": "input.text", "text": "hello there"} in sent and {"type": "response.cancel"} in sent
    assert sent[-1] == {"type": "session.end"}
    assert audio.started[2] is False                          # speaker only, no microphone
    client.on_frame(b"\x01" * 1280)
    assert client._frames.empty()


@pytest.mark.asyncio
async def test_text_only_without_audio_support_still_works(tmp_path, capsys):
    class NoAudio(FakeAudio):
        async def start(self, *a, **k):
            raise AudioUnavailable("audio needs the 'voice' extra")
    ws = FakeWS([READY, {"type": "assistant.text.done", "turn_id": "t", "response_id": "r", "text": "hi"},
                 {"type": "session.ended", "reason": "x"}])
    client, _, out = make(tmp_path, ws, audio=NoAudio(), text_only=True,
                          read_line_fn=lambda p: asyncio.sleep(3600))
    assert await client.run() == 0
    assert client.audio is None and "pai: hi" in out.getvalue()
    assert "read the answers instead" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# The sounddevice backend, with a fake module
# ---------------------------------------------------------------------------

class FakeStream:
    def __init__(self, kind, **kw):
        self.kind, self.kw, self.started, self.closed = kind, kw, False, False
        self.callback = kw["callback"]

    def start(self):
        self.started = True

    def stop(self):
        pass

    def close(self):
        self.closed = True


class FakeSD:
    def __init__(self, input_ok=True, output_ok=True):
        self.input_ok, self.output_ok, self.streams = input_ok, output_ok, []

    def check_input_settings(self, **kw):
        if not self.input_ok:
            raise RuntimeError("no default input device")

    def RawInputStream(self, **kw):
        s = FakeStream("in", **kw)
        self.streams.append(s)
        return s

    def RawOutputStream(self, **kw):
        if not self.output_ok:
            raise RuntimeError("no output device")
        s = FakeStream("out", **kw)
        self.streams.append(s)
        return s


@pytest.mark.asyncio
async def test_sounddevice_backend_moves_frames_to_the_loop_and_pads_playback_with_silence():
    sd = FakeSD()
    audio = SoundDeviceAudio(sd=sd)
    got = []
    await audio.start(got.append, input_rate=16000, output_rate=24000, want_input=True)
    inp, out = sd.streams
    assert inp.kw["blocksize"] == 640 and inp.kw["samplerate"] == 16000 and out.kw["samplerate"] == 24000
    inp.callback(memoryview(b"\x01\x02" * 640), 640, None, None)        # PortAudio's thread
    await asyncio.sleep(0.01)
    assert got == [b"\x01\x02" * 640]

    audio.play(b"\x05\x06" * 3)
    assert audio.playing
    buf = bytearray(20)
    out.callback(buf, 10, None, None)                                    # asks for 10 frames = 20 bytes
    assert bytes(buf) == b"\x05\x06" * 3 + b"\x00" * 14                  # underrun is silence, not noise
    assert not audio.playing
    audio.play(b"\x09" * 8)
    audio.clear()
    assert not audio.playing
    await audio.stop()
    assert inp.closed and out.closed


@pytest.mark.asyncio
async def test_sounddevice_backend_reports_missing_hardware_clearly(capsys):
    with pytest.raises(AudioUnavailable, match="no usable microphone"):
        await SoundDeviceAudio(sd=FakeSD(input_ok=False)).start(lambda f: None, input_rate=16000, output_rate=24000,
                                                                want_input=True)
    audio = SoundDeviceAudio(sd=FakeSD(output_ok=False))                 # no speaker: carry on without sound
    await audio.start(lambda f: None, input_rate=16000, output_rate=24000, want_input=True)
    assert audio.can_play is False
    audio.play(b"\x00\x00")
    assert not audio.playing
    assert "no speaker" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------------

def vargs(**kw):
    base = dict(command="voice", ptt=False, text_only=False, barge_in=False, language=None, voice=None, session=None,
                new=False, json=False, device=None)
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.mark.asyncio
async def test_voice_refuses_cleanly_when_the_backend_has_no_voice(tmp_path, capsys):
    r = Routes()
    r.add("GET", "/voice/capabilities", json_body={"available": False, "reason": "no speech engine installed"})
    assert await voicecli.cmd_voice(vargs(), make_ctx(make_client(r), tmp_path)) == 1
    assert "no speech engine installed" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_voice_explains_the_missing_audio_extra_and_bad_flag_combinations(tmp_path, monkeypatch, capsys):
    r = Routes()
    r.add("GET", "/voice/capabilities", json_body={"available": True, "stt": "x", "tts": "y"})
    ctx = make_ctx(make_client(r), tmp_path)
    assert await voicecli.cmd_voice(vargs(ptt=True, text_only=True), ctx, audio=FakeAudio()) == 2

    def no_module(self):
        raise AudioUnavailable("audio needs the 'voice' extra: pip install 'pai-device[voice]' (or use --text-only)")
    monkeypatch.setattr(SoundDeviceAudio, "_module", no_module)
    assert await voicecli.cmd_voice(vargs(), ctx) == 2
    assert "pai-device[voice]" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_voice_runs_a_session_through_the_command(tmp_path):
    r = Routes()
    r.add("GET", "/voice/capabilities", json_body={"available": True, "stt": "x", "tts": "y"})
    ws = FakeWS([READY, {"type": "session.ended", "reason": "bye"}])
    ctx = make_ctx(make_client(r), tmp_path)
    code = await voicecli.cmd_voice(vargs(text_only=True), ctx, audio=FakeAudio(), connect=lambda url, **k: ws)
    assert code == 0 and ws.json_sent()[0]["type"] == "session.start"
