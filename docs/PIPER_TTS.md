# Piper local neural TTS (P0.4)

Free, offline neural narration for this project using
[`piper-tts`](https://pypi.org/project/piper-tts/) (maintained as
[OHF-Voice/piper1-gpl](https://github.com/OHF-Voice/piper1-gpl), GPL-3.0-or-later).

Inference runs on **CPU via ONNX Runtime**. CUDA is not required.

## Fallback order (`TTS_PROVIDER=auto`)

1. **Edge TTS** — best quality when the free Microsoft endpoint works  
2. **Piper** — local neural TTS (this doc)  
3. **Windows SAPI / pyttsx3** — last-resort robotic fallback  

## Install engine (once)

```bash
pip install piper-tts
# or from a downloaded wheel if PyPI is slow:
# pip install tools/wheels/piper_tts-1.8.0-cp39-abi3-win_amd64.whl
```

Models are **not** downloaded automatically by the app.

## Recommended voice (default)

| Field | Value |
|-------|--------|
| Voice | `en_US-ljspeech-medium` |
| Style | Clear US English female (LJSpeech), good for educational narration |
| Sample rate | 22 050 Hz, mono WAV |
| Quality | medium |
| Dataset license | Public domain (LJ Speech) — see MODEL_CARD |
| Engine license | GPL-3.0-or-later (`piper-tts`) |

**Why not `en_US-lessac-medium`?** Lessac-trained / lessac-finetuned voices have a
research-only corpus license chain that is awkward for YouTube publishing.
LJSpeech is public domain and trained from scratch.

Other cleaner options (see Piper `COMMERCIAL_USE.md`): `en_US-kathleen-medium`,
`en_US-joe-medium`, `en_GB-cori-medium` (CC BY attribution).

## Explicit model download

Store voices on the project data drive (not C:):

```text
G:\youtube-uploader\data\models\piper\
  en_US-ljspeech-medium.onnx
  en_US-ljspeech-medium.onnx.json
  MODEL_CARD_en_US-ljspeech-medium   (optional but recommended)
```

Download with the Piper CLI (explicit — run yourself):

```bash
python -m piper.download_voices en_US-ljspeech-medium ^
  --download-dir G:\youtube-uploader\data\models\piper
```

Or from Hugging Face (`rhasspy/piper-voices`):

```text
en/en_US/ljspeech/medium/en_US-ljspeech-medium.onnx
en/en_US/ljspeech/medium/en_US-ljspeech-medium.onnx.json
en/en_US/ljspeech/medium/MODEL_CARD
```

## Configuration (`backend/.env`)

```env
TTS_PROVIDER=auto
PIPER_MODEL_DIR=G:\youtube-uploader\data\models\piper
PIPER_VOICE=en_US-ljspeech-medium
# Optional absolute override:
# PIPER_MODEL_PATH=G:\youtube-uploader\data\models\piper\en_US-ljspeech-medium.onnx
PIPER_LENGTH_SCALE=1.0
```

Force Piper only (no Edge):

```env
TTS_PROVIDER=piper
```

## Licensing notes (not legal advice)

- **Engine** `piper-tts`: GPL-3.0-or-later.
- **Voice weights**: rights come from each voice’s training data / MODEL_CARD.
  Keep the MODEL_CARD next to the `.onnx` files.
- Prefer voices listed in Piper’s commercial-use doc when publishing publicly.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `Piper voice model not found` | Download the `.onnx` + `.onnx.json` into `PIPER_MODEL_DIR` |
| Auto chain skips Piper | Check logs; package missing or model absent → Edge→SAPI |
| Slow first call | Model load is one-time per process; subsequent synth is faster |
| Pacing too fast/slow | Adjust `PIPER_LENGTH_SCALE` (>1 slower, <1 faster) |
