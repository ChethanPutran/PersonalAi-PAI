# Realtime voice

A ChatGPT-voice-style conversation with the assistant, **streaming end to end**:

```
mic PCM ─► EnergyVAD ─► STT stream ─► transcript ─► orchestrator (plan · tools · memory)
                         │ partials                        │ reply tokens (streamed)
                         ▼                                 ▼
                    live captions                 StreamingSanitizer ─► SpeechChunker (sentences)
                                                                          │
speaker ◄─ PCM ◄──────────────── TTS stream ◄─────────────────────────────┘
```

* The user's speech is fed to STT **while they talk** (partial transcripts).
* The reply is spoken **sentence by sentence while the LLM is still generating** — the first
  audio starts after the first clause, not after the whole answer.
* **Barge-in**: speech over the assistant cancels the LLM stream, the TTS and the upstream
  connections immediately; the client is told to flush its queued audio. History keeps only
  the part the user actually heard.

## Wire protocol — `WS /api/v1/voice/stream?token=<jwt>`
Typed in `models.py` (the single source of truth).

| direction | frame | content |
|---|---|---|
| → server | binary | raw PCM16LE mono at `input_sample_rate` (20–100 ms per frame) |
| → server | text | `session.start` (first!), `input.text`, `response.cancel`, `session.update`, `ping`, `session.end` |
| ← client | binary | raw PCM16LE mono at `output_sample_rate`, between `assistant.audio.start` / `.end` |
| ← client | text | `session.ready`, `state`, `speech.started/stopped`, `transcript.partial/final`, `assistant.status`, `assistant.text.delta/done`, `assistant.audio.start/end`, `assistant.interrupted`, `error`, `pong`, `session.ended` |

On `assistant.interrupted` the client **must drop audio it has queued but not yet played**.
Clients should enable platform echo cancellation (the server also ignores speech that never
grows into a real barge-in while the assistant is talking, to stop it hearing itself).

Close codes: 4401 unauthenticated · 4403 session belongs to someone else · 4429 too many
sessions · 4503 voice unavailable. `GET /api/v1/voice/capabilities` reports availability.

## Text chat streams too
`POST /api/v1/chat/stream` (SSE: `status`, `delta`, `done`, `error`; honours `Idempotency-Key`;
closing the connection stops the request) and `chat.delta` / `chat.reply_done` events on the
chat status WebSocket. Voice and text share one mechanism: the orchestrator pushes sanitized
text into a `ReplySink` (`orchestration/streaming.py`).

## Security model
* **Nothing the model streams reaches the screen or the speaker unsanitized.**
  `security/guards/stream_guard.py` redacts secrets and strips tool-call envelopes
  *incrementally* (words are released whole; suspicious lines are held until judged).
* **Voice requests have their own origin (`Origin.VOICE`).** Speech can be spoofed (another
  person, a TV, a recording), so sensitive actions started by voice need **on-screen
  approval** like autonomous ones. **Spoken confirmations are never accepted** — the voice
  prompt (chat.reply v1.2.0) tells the model to point the user to their screen.
* Transcripts are user text: they go through the same PromptGuard, memory guard and
  authorization pipeline as typed text.
* Limits: per-user concurrent sessions, utterance length, idle and total duration, frame size.

## Providers — chosen by config (`voice.stt`, `voice.tts`)
| | OpenAI (needs a key) | **Free / local (no key)** |
|---|---|---|
| STT | `openai_realtime` (streaming, partials) · `openai_batch` | **`vosk`** — streaming with partials, CPU, ~40 MB model |
| TTS | `openai` (streaming PCM) | **`piper`** — natural neural voice, CPU · `espeak` — tiny, robotic |

`auto` (the default) uses OpenAI when `OPENAI_API_KEY` / `voice.api_key` is set, otherwise the
free engines — so voice works out of the box. Install them with
`pip install 'ai-personal-agent[voice-local]'`; models download once into `voice.models_dir`
(only from alphacephei.com / huggingface.co over HTTPS, written atomically, zip-slip checked).
Other languages/voices: set `voice.vosk_model` / `voice.piper_voice`.

Any other engine plugs in with one call and is then selected by name in the config:
```python
from pai.voice.providers.factory import register_stt, register_tts
register_tts("my_engine", lambda cfg: MyTTS(...))        # voice.tts: my_engine
```
`VoiceService.build` never raises: if nothing usable is configured the server still starts and
voice reports `voice_unavailable` with the reason.

## Not verified against live services
The OpenAI request/event shapes were written from the documented API and tested against
local fakes only. Run a real session once with a key before relying on them.
