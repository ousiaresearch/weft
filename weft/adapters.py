"""Phase 3 real adapters (spec D3-6). Local models only; no network.

QwenVLM        image -> text (OCR and description), Qwen3.5-2B 8-bit via mlx-vlm
WhisperTranscriber  audio -> text, whisper large-v3-turbo q5_0 via whisper.cpp

Each adapter is a callable taking bytes and returning text, with a `model_id`.
Models load lazily on first use and stay resident for the adapter's lifetime.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

_SCRATCH = Path(os.path.expanduser("~/.hermes/profiles/arien/cache/scratch"))


def _scratch_dir() -> str:
    _SCRATCH.mkdir(parents=True, exist_ok=True)
    return str(_SCRATCH)


def _image_suffix(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    raise ValueError("unsupported image format")


class QwenVLM:
    """OCR and description in one pass. Alpha OCR is accepted per the user (Oct 25)."""

    PROMPT = (
        "Transcribe all text visible in this image exactly as written. "
        "Then give a one-sentence description of the image."
    )

    def __init__(self, model_dir, prompt: str | None = None, max_tokens: int = 512):
        self.model_dir = Path(model_dir)
        self.prompt = prompt or self.PROMPT
        self.max_tokens = max_tokens
        self.model_id = "Qwen3.5-2B-8bit (mlx-vlm)"
        self._model = None
        self._processor = None
        self._config = None
        self._apply = None

    def _load(self):
        import json
        from mlx_vlm import load
        from mlx_vlm.prompt_utils import apply_chat_template
        self._model, self._processor = load(str(self.model_dir))
        self._config = json.loads((self.model_dir / "config.json").read_text())
        self._apply = apply_chat_template

    def __call__(self, data: bytes) -> str:
        if self._model is None:
            self._load()
        from mlx_vlm import generate
        fd, path = tempfile.mkstemp(suffix=_image_suffix(data), dir=_scratch_dir())
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            formatted = self._apply(self._processor, self._config, self.prompt, num_images=1)
            result = generate(self._model, self._processor, prompt=formatted,
                              image=path, verbose=False, max_tokens=self.max_tokens)
        finally:
            os.unlink(path)
        return (getattr(result, "text", None) or str(result)).strip()


class WhisperTranscriber:
    """Audio is converted to 16 kHz mono WAV with ffmpeg, then transcribed locally."""

    def __init__(self, model_path):
        self.model_path = Path(model_path)
        self.model_id = "whisper-large-v3-turbo-q5_0 (whisper.cpp)"
        self._model = None

    def _load(self):
        from pywhispercpp.model import Model
        self._model = Model(str(self.model_path))

    def _to_wav(self, data: bytes) -> str:
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            raise RuntimeError("ffmpeg not found")
        fd_in, src = tempfile.mkstemp(suffix=".bin", dir=_scratch_dir())
        fd_out, dst = tempfile.mkstemp(suffix=".wav", dir=_scratch_dir())
        os.close(fd_out)
        try:
            with os.fdopen(fd_in, "wb") as fh:
                fh.write(data)
            subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", src,
                            "-ar", "16000", "-ac", "1", dst], check=True, timeout=600)
        finally:
            os.unlink(src)
        return dst

    def __call__(self, data: bytes) -> str:
        if self._model is None:
            self._load()
        wav = self._to_wav(data)
        try:
            segments = self._model.transcribe(wav)
            return " ".join(seg.text.strip() for seg in segments).strip()
        finally:
            os.unlink(wav)
