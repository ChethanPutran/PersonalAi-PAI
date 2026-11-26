"""``pai-device voice``: talk to the assistant by voice over ``WS /voice/stream``.

Protocol (the backend's ``voice/models.py`` is the source of truth)::

    client -> server  binary  PCM16LE mono, 16 kHz, 40 ms frames
                      text    session.start (first!), input.text, response.cancel, session.end
    server -> client  binary  PCM16LE mono (rate in session.ready) between assistant.audio.start/end
                      text    session.ready, state, speech.*, transcript.partial/final,
                              assistant.status, assistant.text.delta/done, assistant.audio.*,
                              assistant.interrupted (drop queued audio!), error, session.ended

The microphone and speaker use the optional ``sounddevice`` extra (``pip install
'pai-device[voice]'``); without it, ``--text-only`` still works (you type, you read, and you hear the
answer if a speaker exists). The audio side sits behind ``AudioBackend`` so the protocol logic is
testable without hardware.

Echo: a laptop speaker leaks into its microphone. By default the microphone is NOT sent while the
assistant is speaking (use headphones plus ``--barge-in`` to interrupt it by talking).
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sys
import threading
from typing import Any, Awaitable, Callable, Dict, Optional, Protocol, TextIO

import httpx
import websockets

from .cli_support import Ctx, dim, error_text, read_line
from .chatcli import load_session, save_session

INPUT_RATE = 16000
FRAME_MS = 40
SILENCE_FLUSH_MS = 1000          # sent after push-to-talk ends so the server's VAD closes the utterance
CLOSE_TEXT = {
    4401: "the backend rejected the login (token expired?)",
    4403: "that conversation belongs to another user",
    4429: "too many voice sessions are open for this account",
    4503: "voice is not available on this backend",
}
CLIENT_NAME = "pai-device-cli"


class AudioUnavailable(RuntimeError):
    """No usable audio (missing extra, no device)."""


class AudioBackend(Protocol):
    can_play: bool
    async def start(self, on_frame: Callable[[bytes], None], *, input_rate: int, output_rate: int,
                    want_input: bool) -> None: ...
    def play(self, pcm: bytes) -> None: ...
    def clear(self) -> None: ...
    @property
    def playing(self) -> bool: ...
    async def stop(self) -> None: ...


class SoundDeviceAudio:
    """Microphone in / speaker out through ``sounddevice`` (PortAudio). Callbacks run on PortAudio's
    thread; mic frames are handed to the event loop, playback reads a lock-guarded byte buffer."""

    def __init__(self, sd: Any = None, frame_ms: int = FRAME_MS) -> None:
        self._sd = sd
        self._frame_ms = frame_ms
        self._in = None
        self._out = None
        self._buf = bytearray()
        self._lock = threading.Lock()
        self.can_play = False

    def _module(self):
        if self._sd is not None:
            return self._sd
        try:
            import sounddevice
        except ImportError as exc:
            raise AudioUnavailable("audio needs the 'voice' extra: pip install 'pai-device[voice]' "
                                   "(or use --text-only)") from exc
        except OSError as exc:                        # PortAudio library missing
            raise AudioUnavailable(f"PortAudio is not installed on this machine ({exc}); "
                                   "install it (e.g. apt install libportaudio2) or use --text-only") from exc
        return sounddevice

    async def start(self, on_frame, *, input_rate: int, output_rate: int, want_input: bool) -> None:
        sd = self._module()
        loop = asyncio.get_running_loop()
        if want_input:
            try:
                sd.check_input_settings(samplerate=input_rate, channels=1, dtype="int16")
            except Exception as exc:
                raise AudioUnavailable(f"no usable microphone ({exc}); use --text-only to type instead") from exc

            def in_cb(indata, frames, time_info, status):          # PortAudio thread
                loop.call_soon_threadsafe(on_frame, bytes(indata))

            self._in = sd.RawInputStream(samplerate=input_rate, channels=1, dtype="int16",
                                         blocksize=input_rate * self._frame_ms // 1000, callback=in_cb)
            self._in.start()
        try:
            def out_cb(outdata, frames, time_info, status):        # PortAudio thread
                need = frames * 2
                with self._lock:
                    chunk = bytes(self._buf[:need])
                    del self._buf[:need]
                outdata[:len(chunk)] = chunk
                if len(chunk) < need:
                    outdata[len(chunk):] = b"\x00" * (need - len(chunk))

            self._out = sd.RawOutputStream(samplerate=output_rate, channels=1, dtype="int16", callback=out_cb)
            self._out.start()
            self.can_play = True
        except Exception as exc:
            self.can_play = False
            dim(f"(no speaker available: {exc}; showing text only)")

    def play(self, pcm: bytes) -> None:
        if self.can_play:
            with self._lock:
                self._buf += pcm

    def clear(self) -> None:
        with self._lock:
            self._buf.clear()

    @property
    def playing(self) -> bool:
        with self._lock:
            return len(self._buf) > 0

    async def stop(self) -> None:
        for stream in (self._in, self._out):
            if stream is not None:
                with contextlib.suppress(Exception):
                    stream.stop()
                    stream.close()
        self._in = self._out = None
        self.clear()


class VoiceClient:
    def __init__(
        self, ctx: Ctx, audio: Optional[AudioBackend], *, text_only: bool = False, ptt: bool = False,
        barge_in: bool = False, language: Optional[str] = None, voice: Optional[str] = None,
        session_id: Optional[str] = None, device_id: Optional[str] = None,
        connect: Optional[Callable[..., Any]] = None, out: Optional[TextIO] = None,
        read_line_fn: Optional[Callable[[str], Awaitable[str]]] = None,
    ) -> None:
        self.ctx, self.audio = ctx, audio
        self.text_only, self.ptt, self.barge_in = text_only, ptt, barge_in
        self.language, self.voice, self.session_id, self.device_id = language, voice, session_id, device_id
        self._connect = connect or websockets.connect
        self.out = out or sys.stdout
        self._read_line = read_line_fn or read_line
        self.state = "idle"
        self.talking = False                    # push-to-talk: the user is holding the floor
        self.exit_code = 0
        self._ws = None
        self._frames: "asyncio.Queue[bytes]" = asyncio.Queue(maxsize=50)
        self._reply_open = False
        self._partial_len = 0
        self._stop = asyncio.Event()

    # ------------------------------------------------------------ wire helpers

    async def _send_json(self, payload: Dict[str, Any]) -> None:
        if self._ws is not None:
            await self._ws.send(json.dumps(payload))

    def _start_message(self) -> Dict[str, Any]:
        msg: Dict[str, Any] = {"type": "session.start", "input_sample_rate": INPUT_RATE,
                               "client": {"name": CLIENT_NAME, "version": "1"}}
        for key, value in (("session_id", self.session_id), ("device_id", self.device_id),
                           ("language", self.language), ("voice", self.voice)):
            if value:
                msg[key] = value
        return msg

    # ------------------------------------------------------------ microphone

    def on_frame(self, frame: bytes) -> None:
        """A microphone frame (event-loop thread). Gate it, then queue it for the sender."""
        if self.text_only or self._stop.is_set():
            return
        if self.ptt and not self.talking:
            return
        speaking = self.state == "speaking" or (self.audio is not None and self.audio.playing)
        if speaking and not self.barge_in:
            return                                   # our own speaker would be heard as the user
        self._enqueue(frame)

    def _enqueue(self, frame: bytes) -> None:
        if self._frames.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                self._frames.get_nowait()            # drop the oldest rather than fall behind
        self._frames.put_nowait(frame)

    async def _sender(self) -> None:
        while True:
            frame = await self._frames.get()
            await self._ws.send(frame)

    async def _push_to_talk(self) -> None:
        """Enter starts talking, Enter again sends: after the last audio, a second of silence
        lets the server's voice-activity detection end the utterance."""
        silence = b"\x00" * (INPUT_RATE * FRAME_MS // 1000 * 2)
        while not self._stop.is_set():
            await self._read_line("[Enter] to talk… ")
            self.talking = True
            dim("● listening: press Enter to send")
            await self._read_line("")
            self.talking = False
            for _ in range(SILENCE_FLUSH_MS // FRAME_MS):
                self._enqueue(silence)

    async def _typed(self) -> None:
        """--text-only: each line is sent as input.text; /cancel interrupts, /quit leaves."""
        while not self._stop.is_set():
            try:
                line = (await self._read_line("you> ")).strip()
            except (EOFError, KeyboardInterrupt):
                self._stop.set()
                return
            if not line:
                continue
            if line in ("/quit", "/exit", "/q"):
                self._stop.set()
                return
            if line == "/cancel":
                await self._send_json({"type": "response.cancel"})
                continue
            await self._send_json({"type": "input.text", "text": line[:4000]})

    # ------------------------------------------------------------ events

    def _say(self, text: str) -> None:
        self.out.write(text)
        self.out.flush()

    def _clear_partial(self) -> None:
        if self._partial_len and self.out.isatty():
            self._say("\r" + " " * self._partial_len + "\r")
        self._partial_len = 0

    def _close_reply(self) -> None:
        if self._reply_open:
            self._say("\n")
            self._reply_open = False

    async def handle_event(self, ev: Dict[str, Any]) -> None:
        kind = ev.get("type")
        if kind == "session.ready":
            caps = ev.get("capabilities") or {}
            dim(f"connected (speech-to-text: {caps.get('stt')}, text-to-speech: {caps.get('tts')})")
            if ev.get("chat_session_id"):
                self.session_id = ev["chat_session_id"]
                save_session(self.ctx, self.session_id)
            if self.audio is not None:
                try:
                    await self.audio.start(
                        self.on_frame, input_rate=int(ev.get("input_sample_rate") or INPUT_RATE),
                        output_rate=int(ev.get("output_sample_rate") or 24000), want_input=not self.text_only)
                except AudioUnavailable as exc:
                    if not self.text_only:
                        raise
                    dim(f"({exc}; you will read the answers instead of hearing them)")
                    self.audio = None
            if self.text_only:
                dim("type a message; /cancel interrupts, /quit leaves")
            elif self.ptt:
                dim("push-to-talk: Enter to start and stop talking. Ctrl+C leaves.")
            else:
                dim("listening: just talk. Ctrl+C leaves." + ("" if self.barge_in else " (use headphones + --barge-in to interrupt by talking)"))
        elif kind == "state":
            new = ev.get("state", "idle")
            if new != self.state and new != "idle":
                dim(f"[{new}]")
            self.state = new
        elif kind == "transcript.partial":
            if self.out.isatty():
                self._clear_partial()
                text = f"you: {ev.get('text', '')}…"
                self._partial_len = len(text)
                self._say(text)
        elif kind == "transcript.final":
            self._clear_partial()
            self._say(f"you: {ev.get('text', '')}\n")
        elif kind == "assistant.status":
            if ev.get("message"):
                dim(f"· {ev['message']}")
        elif kind == "assistant.text.delta":
            if not self._reply_open:
                self._say("pai: ")
                self._reply_open = True
            self._say(ev.get("delta", ""))
        elif kind == "assistant.text.done":
            if not self._reply_open and ev.get("text"):
                self._say("pai: " + ev["text"])
                self._reply_open = True
            self._close_reply()
        elif kind == "assistant.interrupted":
            if self.audio is not None:
                self.audio.clear()                   # the protocol requires dropping queued audio
            self._close_reply()
            dim("(interrupted)")
        elif kind == "error":
            self._close_reply()
            print(f"✗ {ev.get('message') or ev.get('code')}", file=sys.stderr)
            if ev.get("fatal"):
                self.exit_code = 1
                self._stop.set()
        elif kind == "session.ended":
            dim(f"session ended ({ev.get('reason', '')})")
            self._stop.set()

    # ------------------------------------------------------------ main loop

    async def run(self) -> int:
        token = await self.ctx.client.ensure_token()
        url = f"{self.ctx.config.ws_url}/voice/stream?token={token}"
        tasks = []
        try:
            async with self._connect(url, max_size=2 * 1024 * 1024) as ws:
                self._ws = ws
                await self._send_json(self._start_message())
                if not self.text_only:
                    tasks.append(asyncio.create_task(self._sender(), name="voice.mic"))
                    if self.ptt:
                        tasks.append(asyncio.create_task(self._push_to_talk(), name="voice.ptt"))
                else:
                    tasks.append(asyncio.create_task(self._typed(), name="voice.typed"))
                reader = asyncio.create_task(self._receive(ws), name="voice.rx")
                stopper = asyncio.create_task(self._stop.wait(), name="voice.stop")
                tasks += [reader, stopper]
                done, _ = await asyncio.wait([reader, stopper], return_when=asyncio.FIRST_COMPLETED)
                if reader in done and reader.exception() is not None:
                    raise reader.exception()
        except websockets.ConnectionClosed as exc:
            code = exc.rcvd.code if exc.rcvd is not None else None
            if code in CLOSE_TEXT:
                print(f"✗ {CLOSE_TEXT[code]}", file=sys.stderr)
                self.exit_code = 1
        except (OSError, websockets.InvalidHandshake) as exc:
            print(f"✗ can't open the voice connection: {exc}", file=sys.stderr)
            self.exit_code = 1
        finally:
            self._stop.set()
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if self._ws is not None:
                with contextlib.suppress(Exception):
                    await self._ws.send(json.dumps({"type": "session.end"}))
            if self.audio is not None:
                await self.audio.stop()
            self._close_reply()
        return self.exit_code

    async def _receive(self, ws) -> None:
        async for message in ws:
            if isinstance(message, (bytes, bytearray)):
                if self.audio is not None:
                    self.audio.play(bytes(message))
                continue
            try:
                ev = json.loads(message)
            except ValueError:
                continue
            await self.handle_event(ev)
            if self._stop.is_set():
                return


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------

async def cmd_voice(args, ctx: Ctx, *, audio: Optional[AudioBackend] = None,
                    connect: Optional[Callable[..., Any]] = None) -> int:
    try:
        caps = await ctx.client.request_json("GET", "/voice/capabilities")
    except httpx.HTTPStatusError as exc:
        print(f"✗ {error_text(exc)[0]}", file=sys.stderr)
        return 1
    if not caps or not caps.get("available"):
        print(f"✗ voice is not available on this backend: {(caps or {}).get('reason') or 'not configured'}",
              file=sys.stderr)
        return 1

    if audio is None:
        audio = SoundDeviceAudio()
    if args.ptt and args.text_only:
        print("error: --ptt and --text-only don't go together", file=sys.stderr)
        return 2
    if not args.text_only and not sys.stdin.isatty() and args.ptt:
        print("error: --ptt needs a terminal", file=sys.stderr)
        return 2

    client = VoiceClient(
        ctx, audio, text_only=args.text_only, ptt=args.ptt, barge_in=args.barge_in, language=args.language,
        voice=args.voice, session_id=args.session or (None if args.new else load_session(ctx)),
        device_id=args.device or ctx.device_id, connect=connect)
    # Probe audio before connecting so a missing microphone fails with a clear message, not mid-session.
    if not args.text_only and isinstance(audio, SoundDeviceAudio):
        try:
            sd = audio._module()
            sd.check_input_settings(samplerate=INPUT_RATE, channels=1, dtype="int16")
        except AudioUnavailable as exc:
            print(f"✗ {exc}", file=sys.stderr)
            return 2
        except Exception as exc:
            print(f"✗ no usable microphone ({exc}); use --text-only to type instead", file=sys.stderr)
            return 2
    try:
        return await client.run()
    except asyncio.CancelledError:
        dim("\nbye")
        return 0
    except AudioUnavailable as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2


def register(sub: "argparse._SubParsersAction", common: argparse.ArgumentParser) -> None:
    sp = sub.add_parser(
        "voice", parents=[common],
        help="talk to the assistant by voice (needs: pip install 'pai-device[voice]')",
        description="Streams your microphone to the backend and plays the spoken answer as it is generated. "
                    "Use headphones (the microphone is muted while the assistant speaks unless --barge-in).")
    sp.add_argument("--ptt", action="store_true", help="push-to-talk: Enter starts and stops talking")
    sp.add_argument("--text-only", action="store_true", help="type your messages, still hear (and read) the answers")
    sp.add_argument("--barge-in", action="store_true",
                    help="keep the microphone open while the assistant speaks so you can interrupt it (use headphones)")
    sp.add_argument("--language", help="language hint, e.g. en")
    sp.add_argument("--voice", help="voice name (depends on the backend's speech provider)")
    sp.add_argument("--session", help="continue this conversation id (default: the last one, shared with chat)")
    sp.add_argument("--new", action="store_true", help="start a new conversation")


COMMANDS = {"voice": cmd_voice}
