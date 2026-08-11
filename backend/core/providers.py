"""Content and model providers used by Infinity Code.

Holds provider config (endpoints + API keys) persisted to a JSON file in the
app data dir, never in the repo. Exposes connectivity tests and two real
generation helpers (image via FAL, speech via ElevenLabs) used by the
Executive Assistant. Paid calls are gated by an explicit allow flag so nothing
spends money unattended.
"""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("infinity.providers")

# Curated "other apps of interest" — surfaced in the UI, not yet wired.
OTHER_INTEGRATIONS = [
    {"name": "Runway", "kind": "paid", "note": "video generation"},
    {"name": "Suno", "kind": "paid", "note": "music generation"},
    {"name": "Ollama", "kind": "local", "note": "local LLMs"},
    {"name": "Automatic1111", "kind": "local", "note": "local image gen"},
]

# Masked placeholder markers: the real unicode chars used by _mask() plus
# their latin-1 mojibake (older frontend builds re-sent the masked form as
# mojibake, which would otherwise read back as a "configured" key).
_MASKED_MARKERS = ("…", "•••", "â€¦", "â€¢")


def _looks_masked(value: Any) -> bool:
    """True when a stored value is a masked placeholder, not a real key/URL."""
    if not isinstance(value, str):
        return False
    return any(marker in value for marker in _MASKED_MARKERS)


class ProviderManager:
    """Loads/saves provider config and runs connectivity tests + generation."""

    DEFAULTS: Dict[str, Any] = {
        "comfyui_url": "http://127.0.0.1:8188",
        "fal_key": "",
        "novita_key": "",
        "elevenlabs_key": "",
        "deepseek_key": "",
        "dashscope_key": "",
        "openrouter_key": "",
        "moonshot_key": "",
        "minimax_key": "",
        "glm_key": "",
        "nvidia_key": "",
    }

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.cfg: Dict[str, Any] = dict(self.DEFAULTS)
        self._load()

    # --- persistence ------------------------------------------------------- #
    def _load(self) -> None:
        try:
            if self.path.is_file():
                data = json.loads(self.path.read_text(encoding="utf-8-sig"))
                if isinstance(data, dict):
                    self.cfg.update({k: data.get(k, v) for k, v in self.DEFAULTS.items()})
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not load providers.json: %s", exc)

    def save(self, updates: Dict[str, Any]) -> None:
        for key in self.DEFAULTS:
            if key in updates and isinstance(updates[key], str):
                self.cfg[key] = updates[key].strip()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Atomic write: a crash / full disk mid-write must not zero out the
            # user's saved API keys. Write to a temp file, then os.replace.
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self.cfg, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as exc:
            logger.error("Could not save providers.json: %s", exc)

    @staticmethod
    def _mask(value: str) -> str:
        value = str(value or "")
        if not value:
            return ""
        return (value[:4] + "…" + value[-3:]) if len(value) > 8 else "•••"

    def public(self) -> Dict[str, Any]:
        """Config with keys masked + a configured flag per provider."""
        return {
            "comfyui_url": self.cfg["comfyui_url"],
            "fal_key": self._mask(self.cfg["fal_key"]),
            "novita_key": self._mask(self.cfg["novita_key"]),
            "elevenlabs_key": self._mask(self.cfg["elevenlabs_key"]),
            "deepseek_key": self._mask(self.cfg["deepseek_key"]),
            "dashscope_key": self._mask(self.cfg["dashscope_key"]),
            "openrouter_key": self._mask(self.cfg["openrouter_key"]),
            "moonshot_key": self._mask(self.cfg["moonshot_key"]),
            "minimax_key": self._mask(self.cfg["minimax_key"]),
            "glm_key": self._mask(self.cfg["glm_key"]),
            "nvidia_key": self._mask(self.cfg["nvidia_key"]),
            "configured": self._configured(),
            "other": OTHER_INTEGRATIONS,
        }

    def _configured(self) -> Dict[str, bool]:
        """True per provider only when a real (non-default, non-masked) value
        is present -- a fresh install must not report anything configured."""
        out: Dict[str, bool] = {
            "comfyui": isinstance(self.cfg["comfyui_url"], str)
            and bool(self.cfg["comfyui_url"])
            and self.cfg["comfyui_url"] != self.DEFAULTS["comfyui_url"],
        }
        for label, field in (
            ("fal", "fal_key"),
            ("novita", "novita_key"),
            ("elevenlabs", "elevenlabs_key"),
            ("deepseek", "deepseek_key"),
            ("dashscope", "dashscope_key"),
            ("openrouter", "openrouter_key"),
            ("moonshot", "moonshot_key"),
            ("minimax", "minimax_key"),
            ("glm", "glm_key"),
            ("nvidia", "nvidia_key"),
        ):
            raw = self.cfg.get(field, "")
            out[label] = isinstance(raw, str) and bool(raw) and not _looks_masked(raw)
        return out

    # --- helpers ----------------------------------------------------------- #
    @staticmethod
    def _get(url: str, headers: Optional[Dict[str, str]] = None, timeout: int = 8) -> int:
        req = urllib.request.Request(url, headers=headers or {})
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return int(resp.status)

    def test(self, name: str) -> Dict[str, Any]:
        """Return {ok, detail} for a provider connectivity check."""
        try:
            if name == "comfyui":
                if not self.cfg["comfyui_url"]:
                    return {"ok": False, "detail": "No ComfyUI URL set."}
                url = self.cfg["comfyui_url"].rstrip("/") + "/system_stats"
                self._get(url, timeout=3)
                return {"ok": True, "detail": "ComfyUI reachable."}
            if name == "elevenlabs":
                if not self.cfg["elevenlabs_key"]:
                    return {"ok": False, "detail": "No API key set."}
                self._get(
                    "https://api.elevenlabs.io/v1/voices",
                    {"xi-api-key": self.cfg["elevenlabs_key"]},
                )
                return {"ok": True, "detail": "ElevenLabs key valid."}
            if name == "fal":
                if not self.cfg["fal_key"]:
                    return {"ok": False, "detail": "No API key set."}
                # Cheap platform API call that actually validates the key.
                self._get(
                    "https://api.fal.ai/v1/models?limit=1",
                    {"Authorization": "Key " + self.cfg["fal_key"]},
                )
                return {"ok": True, "detail": "FAL key valid."}
            if name == "novita":
                if not self.cfg["novita_key"]:
                    return {"ok": False, "detail": "No API key set."}
                self._get(
                    "https://api.novita.ai/v3/model",
                    {"Authorization": "Bearer " + self.cfg["novita_key"]},
                )
                return {"ok": True, "detail": "Novita key valid."}
            if name == "deepseek":
                if not self.cfg["deepseek_key"]:
                    return {"ok": False, "detail": "No API key set."}
                req = urllib.request.Request(
                    "https://api.deepseek.com/user/balance",
                    headers={"Authorization": "Bearer " + self.cfg["deepseek_key"]},
                )
                with urllib.request.urlopen(req, timeout=8) as resp:  # noqa: S310
                    return {"ok": True, "detail": f"DeepSeek key valid (HTTP {resp.status})."}
            if name == "dashscope":
                if not self.cfg["dashscope_key"]:
                    return {"ok": False, "detail": "No API key set."}
                self._get(
                    "https://dashscope.aliyuncs.com/compatible-mode/v1/models",
                    {"Authorization": "Bearer " + self.cfg["dashscope_key"]},
                )
                return {"ok": True, "detail": "Qwen / DashScope key valid."}
            if name == "openrouter":
                if not self.cfg["openrouter_key"]:
                    return {"ok": False, "detail": "No API key set."}
                self._get(
                    "https://openrouter.ai/api/v1/models",
                    {"Authorization": "Bearer " + self.cfg["openrouter_key"]},
                )
                return {"ok": True, "detail": "OpenRouter key valid."}
            if name == "moonshot":
                if not self.cfg["moonshot_key"]:
                    return {"ok": False, "detail": "No API key set."}
                self._get(
                    "https://api.moonshot.ai/v1/models",
                    {"Authorization": "Bearer " + self.cfg["moonshot_key"]},
                )
                return {"ok": True, "detail": "Kimi / Moonshot key valid."}
            if name == "minimax":
                if not self.cfg["minimax_key"]:
                    return {"ok": False, "detail": "No API key set."}
                self._get(
                    "https://api.minimax.io/v1/models",
                    {"Authorization": "Bearer " + self.cfg["minimax_key"]},
                )
                return {"ok": True, "detail": "MiniMax key valid."}
            if name == "glm":
                if not self.cfg["glm_key"]:
                    return {"ok": False, "detail": "No API key set."}
                self._get(
                    "https://open.bigmodel.cn/api/paas/v4/models",
                    {"Authorization": "Bearer " + self.cfg["glm_key"]},
                )
                return {"ok": True, "detail": "GLM / Zhipu key valid."}
            if name == "nvidia":
                if not self.cfg["nvidia_key"]:
                    return {"ok": False, "detail": "No API key set."}
                self._get(
                    "https://integrate.api.nvidia.com/v1/models",
                    {"Authorization": "Bearer " + self.cfg["nvidia_key"]},
                )
                return {"ok": True, "detail": "NVIDIA NIM key valid."}
        except urllib.error.HTTPError as exc:
            return {"ok": False, "detail": f"HTTP {exc.code}"}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "detail": str(exc)[:120]}
        return {"ok": False, "detail": "Unknown provider."}

    # --- generation (used by the Executive Assistant) ---------------------- #
    def generate_image(self, prompt: str, out_dir: Path, allow_paid: bool) -> str:
        """Text-to-image. FAL FLUX schnell (paid, gated). Returns a file path."""
        if not self.cfg["fal_key"]:
            return (
                "Image generation needs a FAL.AI key (Settings → Providers), "
                "or a local ComfyUI workflow. Not configured."
            )
        if not allow_paid:
            return "Paid action blocked. Enable 'Allow actions' to run FAL image generation (~$0.003)."
        body = json.dumps({"prompt": prompt, "image_size": "square_hd"}).encode()
        req = urllib.request.Request(
            "https://fal.run/fal-ai/flux/schnell",
            data=body,
            headers={
                "Authorization": "Key " + self.cfg["fal_key"],
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310
                data = json.load(resp)
            url = data["images"][0]["url"]
            img = urllib.request.urlopen(url, timeout=60).read()  # noqa: S310
            out_dir.mkdir(parents=True, exist_ok=True)
            dest = out_dir / (f"img_{abs(hash(prompt)) % 100000}.png")
            dest.write_bytes(img)
            # Served by the backend at /media/<name> so the chat can display it.
            media_url = f"http://127.0.0.1:8000/media/{dest.name}"
            return (
                f"Image generated and saved to {dest}. "
                f"Display it to the user by including this markdown: "
                f"![generated image]({media_url})"
            )
        except Exception as exc:  # noqa: BLE001
            return f"FAL image generation failed: {str(exc)[:160]}"

    def generate_video(
        self,
        prompt: str,
        start_path: Path,
        end_path: Optional[Path],
        out_path: Path,
        duration: int = 4,
    ) -> Dict[str, Any]:
        """Generate an image-to-video clip through fal's queue REST API.

        The browser upload is intentionally kept local; fal accepts base64 data
        URIs for file inputs, so no public tunnel or credential-bearing URL is
        needed. ``FAL_VIDEO_MODEL`` can override the model slug while the
        default (Vidu Q2) supports both a start frame and an optional end frame.
        """
        key = str(self.cfg.get("fal_key") or "").strip()
        if not key:
            raise RuntimeError("Video generation needs a FAL.AI key (Settings → Providers).")
        if not start_path.is_file():
            raise FileNotFoundError(f"Start frame not found: {start_path}")

        def data_uri(path: Path) -> str:
            mime = mimetypes.guess_type(path.name)[0] or "image/png"
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            return f"data:{mime};base64,{encoded}"

        model = os.environ.get("FAL_VIDEO_MODEL", "fal-ai/vidu/q2/image-to-video").strip()
        payload: Dict[str, Any] = {
            "prompt": prompt[:3000],
            "image_url": data_uri(start_path),
            "duration": str(max(2, min(8, int(duration)))),
            # 720p Vidu output renders soft/hazy; default to 1080p for
            # crisp clips. Override via FAL_VIDEO_RESOLUTION (e.g. "720p")
            # for faster/cheaper drafts.
            "resolution": os.environ.get("FAL_VIDEO_RESOLUTION", "1080p").strip(),
        }
        if end_path is not None:
            if not end_path.is_file():
                raise FileNotFoundError(f"End frame not found: {end_path}")
            payload["end_image_url"] = data_uri(end_path)

        headers = {
            "Authorization": "Key " + key,
            "Content-Type": "application/json",
        }

        def request_json(url: str, body: Optional[bytes] = None, timeout: int = 30) -> Dict[str, Any]:
            req = urllib.request.Request(url, data=body, headers=headers, method="POST" if body is not None else "GET")
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                parsed = json.loads(resp.read().decode("utf-8"))
            if not isinstance(parsed, dict):
                raise RuntimeError("FAL returned an invalid JSON response.")
            return parsed

        request = request_json(
            f"https://queue.fal.run/{model}",
            json.dumps(payload).encode("utf-8"),
            timeout=60,
        )
        request_id = str(request.get("request_id") or "").strip()
        if not request_id:
            raise RuntimeError(f"FAL did not return a request id: {request}")
        status_url = f"https://queue.fal.run/{model}/requests/{request_id}/status"
        result_url = f"https://queue.fal.run/{model}/requests/{request_id}"
        deadline = time.monotonic() + 600
        last_status = "IN_QUEUE"
        while time.monotonic() < deadline:
            status = request_json(status_url, timeout=30)
            last_status = str(status.get("status") or last_status)
            if last_status in {"COMPLETED", "FAILED", "CANCELLED"}:
                break
            time.sleep(3)
        if last_status != "COMPLETED":
            raise RuntimeError(f"FAL video request {request_id} ended with {last_status}.")
        result = request_json(result_url, timeout=60)
        video = result.get("video") if isinstance(result.get("video"), dict) else {}
        video_url = str(video.get("url") or result.get("video_url") or "").strip()
        if not video_url:
            raise RuntimeError(f"FAL completed without a video URL: {result}")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(video_url, timeout=120) as resp:  # noqa: S310
            out_path.write_bytes(resp.read())
        if not out_path.is_file() or out_path.stat().st_size == 0:
            raise RuntimeError("FAL returned an empty video file.")
        return {"path": str(out_path), "request_id": request_id, "model": model}

    def text_to_speech(self, text: str, out_dir: Path, allow_paid: bool) -> str:
        """ElevenLabs TTS (paid, gated). Returns a file path."""
        if not self.cfg["elevenlabs_key"]:
            return "Text-to-speech needs an ElevenLabs key (Settings → Providers). Not configured."
        if not allow_paid:
            return "Paid action blocked. Enable 'Allow actions' to run ElevenLabs TTS."
        voice = "21m00Tcm4TlvDq8ikWAM"  # default public voice (Rachel)
        body = json.dumps(
            {"text": text[:2000], "model_id": "eleven_multilingual_v2"}
        ).encode()
        req = urllib.request.Request(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice}",
            data=body,
            headers={
                "xi-api-key": self.cfg["elevenlabs_key"],
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:  # noqa: S310
                audio = resp.read()
            out_dir.mkdir(parents=True, exist_ok=True)
            dest = out_dir / (f"tts_{abs(hash(text)) % 100000}.mp3")
            dest.write_bytes(audio)
            _ = base64  # reserved for future inline previews
            media_url = f"http://127.0.0.1:8000/media/{dest.name}"
            return f"Speech generated. Play it at: {media_url} (saved to {dest})"
        except Exception as exc:  # noqa: BLE001
            return f"ElevenLabs TTS failed: {str(exc)[:160]}"


def estimate_cost(name: str, args: Dict[str, Any]) -> Optional[float]:
    """Rough USD estimate for a paid tool call, for the approval card. None = free/unknown."""
    if name == "generate_image":
        return 0.003  # FAL FLUX schnell, flat per image
    if name == "text_to_speech":
        # ElevenLabs multilingual v2 ~ $0.00018/char (Creator tier), capped at 2000 chars.
        chars = min(len(str(args.get("text") or "")), 2000)
        return round(chars * 0.00018, 4)
    return None


__all__ = ["ProviderManager", "OTHER_INTEGRATIONS", "estimate_cost"]
