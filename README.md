# SYLVIA

SYLVIA is a local macOS voice assistant with a cyber-style Tkinter interface, speech recognition, text-to-speech, system utilities, weather lookup, and a local GGUF language model brain.

## Features

- Animated desktop UI with a central microphone control
- Local speech-to-text using Faster Whisper
- Local GGUF inference through `ctransformers`
- macOS text-to-speech using the built-in `say` voice engine
- Deterministic tools for time, battery, storage, system status, weather, web search, and URL extraction
- English, Hindi, and Roman Hinglish response modes
- Optional user persona file for personalizing replies locally

## Project Structure

```text
.
|-- main.py            # Main Tkinter desktop UI
|-- brain.py           # Local model loading, prompt building, and response generation
|-- mic.py             # Microphone capture, VAD, and speech-to-text pipeline
|-- tts.py             # macOS text-to-speech pipeline
|-- automation.py      # System, weather, search, and URL helper tools
|-- orchestrator.py    # Routes tool-style requests before model generation
|-- emotion.py         # Lightweight emotion tracking
|-- weather.py         # macOS location and weather lookup
`-- BRAIN/             # Local model config/tokenizer files; GGUF model is not committed
```

## Requirements

- macOS
- Python 3.10+
- Tkinter available for your Python install
- Microphone permission enabled for your terminal or IDE
- A local GGUF model at `BRAIN/model.gguf`

The `BRAIN/model.gguf` file is intentionally ignored by git because it is large. Keep it locally or download/provide it after cloning the repository.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Download the local model file from Hugging Face:

```bash
mkdir -p BRAIN
curl -L "https://huggingface.co/TheBloke/TinyLlama-1.1B-Chat-v1.0-GGUF/resolve/main/tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf" -o BRAIN/model.gguf
```

Or download it manually from:

```text
https://huggingface.co/TheBloke/TinyLlama-1.1B-Chat-v1.0-GGUF
```

SYLVIA expects the file here:

```text
BRAIN/model.gguf
```

Then start the assistant:

```bash
python main.py
```

For a terminal-only chat loop, run:

```bash
python brain.py
```

## Notes

- `tts_outputs/` contains generated speech files and is ignored by git.
- `user_persona.txt` is ignored because it can contain personal local context.
- Weather and location features use macOS CoreLocation and Open-Meteo/Nominatim requests.
- The voice backend uses macOS system voices through the `say` command.

## Repository Description

A local macOS voice assistant with a cyberpunk Tkinter UI, Whisper speech recognition, macOS TTS, weather/system tools, and a GGUF-powered AI brain.
