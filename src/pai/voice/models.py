"""Wire protocol of the realtime voice WebSocket (`/voice/stream`) — typed, no raw dicts.

Frames
  client → server   BINARY : raw PCM16LE mono at `input_sample_rate` (20–100 ms per frame)
                    TEXT   : one JSON object per frame, discriminated by `type` (ClientMessage)
  server → client   BINARY : raw PCM16LE mono at `output_sample_rate`, only between
                             `assistant.audio.start` and `assistant.audio.end`
                    TEXT   : one JSON object per frame, discriminated by `type` (ServerEvent)

Interruption: when the user speaks over the assistant (or sends `response.cancel`) the
server stops, sends `assistant.interrupted` and the client MUST drop any audio it has
queued but not yet played.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class VoiceState(StrEnum):
    IDLE = "idle"              # connected, nobody talking
    LISTENING = "listening"    # user is speaking
    THINKING = "thinking"      # transcript sent to the assistant, no audio yet
    SPEAKING = "speaking"      # assistant audio is being streamed


class InterruptReason(StrEnum):
    BARGE_IN = "barge_in"
    CANCEL = "cancel"
    NEW_INPUT = "new_input"


# ------------------------------- client → server -----------------------------

class _Msg(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClientInfo(BaseModel):
    name: str = "unknown"
    version: str = "0"


class SessionStart(_Msg):
    type: Literal["session.start"] = "session.start"
    session_id: Optional[str] = None          # chat session to continue (history + memory)
    device_id: Optional[str] = None
    language: Optional[str] = None
    voice: Optional[str] = None
    input_sample_rate: Optional[int] = None
    client: ClientInfo = Field(default_factory=ClientInfo)


class SessionUpdate(_Msg):
    type: Literal["session.update"] = "session.update"
    language: Optional[str] = None
    voice: Optional[str] = None


class InputText(_Msg):
    """A typed message inside the voice session (answered with voice too)."""
    type: Literal["input.text"] = "input.text"
    text: str = Field(min_length=1, max_length=4000)


class ResponseCancel(_Msg):
    """Manual interrupt (e.g. the user taps the orb)."""
    type: Literal["response.cancel"] = "response.cancel"


class Ping(_Msg):
    type: Literal["ping"] = "ping"
    id: Optional[str] = None


class SessionEnd(_Msg):
    type: Literal["session.end"] = "session.end"


ClientMessage = Annotated[
    Union[SessionStart, SessionUpdate, InputText, ResponseCancel, Ping, SessionEnd],
    Field(discriminator="type"),
]
client_message_adapter: TypeAdapter = TypeAdapter(ClientMessage)


# ------------------------------- server → client -----------------------------

class Limits(BaseModel):
    max_utterance_seconds: float
    idle_timeout_seconds: float
    max_session_seconds: float


class Capabilities(BaseModel):
    stt: str
    tts: str
    partial_transcripts: bool
    barge_in: bool


class SessionReady(_Msg):
    type: Literal["session.ready"] = "session.ready"
    voice_session_id: str
    chat_session_id: str
    input_sample_rate: int
    output_sample_rate: int
    output_format: Literal["pcm_s16le"] = "pcm_s16le"
    limits: Limits
    capabilities: Capabilities


class StateChanged(_Msg):
    type: Literal["state"] = "state"
    state: VoiceState
    turn_id: Optional[str] = None


class SpeechStarted(_Msg):
    type: Literal["speech.started"] = "speech.started"
    turn_id: str


class SpeechStopped(_Msg):
    type: Literal["speech.stopped"] = "speech.stopped"
    turn_id: str


class TranscriptPartial(_Msg):
    type: Literal["transcript.partial"] = "transcript.partial"
    turn_id: str
    text: str


class TranscriptFinal(_Msg):
    type: Literal["transcript.final"] = "transcript.final"
    turn_id: str
    text: str


class AssistantStatus(_Msg):
    """Progress while the assistant plans / runs tools (from chat.status events)."""
    type: Literal["assistant.status"] = "assistant.status"
    turn_id: str
    stage: str
    message: str = ""


class AssistantTextDelta(_Msg):
    type: Literal["assistant.text.delta"] = "assistant.text.delta"
    turn_id: str
    response_id: str
    delta: str


class AssistantTextDone(_Msg):
    type: Literal["assistant.text.done"] = "assistant.text.done"
    turn_id: str
    response_id: str
    text: str


class AssistantAudioStart(_Msg):
    type: Literal["assistant.audio.start"] = "assistant.audio.start"
    response_id: str
    sample_rate: int
    format: Literal["pcm_s16le"] = "pcm_s16le"


class AssistantAudioEnd(_Msg):
    type: Literal["assistant.audio.end"] = "assistant.audio.end"
    response_id: str


class AssistantInterrupted(_Msg):
    type: Literal["assistant.interrupted"] = "assistant.interrupted"
    response_id: Optional[str] = None
    reason: InterruptReason
    spoken_text: str = ""        # what had actually been sent to be spoken


class ErrorEvent(_Msg):
    type: Literal["error"] = "error"
    code: str
    message: str
    fatal: bool = False


class Pong(_Msg):
    type: Literal["pong"] = "pong"
    id: Optional[str] = None


class SessionEnded(_Msg):
    type: Literal["session.ended"] = "session.ended"
    reason: str


ServerEvent = Union[
    SessionReady, StateChanged, SpeechStarted, SpeechStopped, TranscriptPartial,
    TranscriptFinal, AssistantStatus, AssistantTextDelta, AssistantTextDone,
    AssistantAudioStart, AssistantAudioEnd, AssistantInterrupted, ErrorEvent, Pong, SessionEnded,
]
