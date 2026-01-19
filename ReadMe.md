# PersonalAi-PAI


## Overall Architecture

```plaintext
User (via Bluetooth Mic)
         ↓
  Android or Windows App
         ↓
Speech-to-Text (locally or via server)
         ↓
     LLM API (Server-hosted)
         ↓
 Get structured command / task
         ↓
 Execute task (locally or call another service)
```

---

## Key Components

### 1. **Bluetooth Audio Input**

* Connect your **Bluetooth earbud** as the microphone input in:

  * **Android App**: Use `pyjnius` or native Android APIs
  * **Windows App**: Use `pyaudio`, `sounddevice`, or system input

### 2. **Speech-to-Text**

You have two choices:

* **Local STT**: Using `Vosk` or `Whisper` (runs on the device)
* **Server STT**: Record audio → send to server → get text

### 3. **Send Text to Server**

* Use `requests.post()` to send the transcription to your server endpoint like:

```json
POST /process_command
{
  "text": "Turn on the lights in the bedroom"
}
```

### 4. **LLM on the Server**

* Host **ChatGPT**, **LLama.cpp**, **OpenAI API**, or **GPT4All**, etc.
* Return a structured task (e.g., JSON command) back to app.

### 5. **Task Execution**

* Let the app act on it (e.g., control a smart device, fetch a web page, open app, etc.)

---