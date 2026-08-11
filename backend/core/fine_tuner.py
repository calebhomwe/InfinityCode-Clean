"""Hosted fine-tune clients for Together AI and Fireworks.

Wraps the minimal provider APIs needed to upload a JSONL dataset, start a LoRA
fine-tune job, and poll its status. Missing API keys degrade gracefully.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.error import HTTPError
from urllib.request import Request, urlopen

logger = logging.getLogger("infinity.fine_tuner")

DEFAULT_TIMEOUT_SECONDS: int = 120


def _encode_multipart(
    fields: Dict[str, str], file_path: Path, file_field: str = "file"
) -> tuple[bytes, str]:
    """Build a multipart/form-data payload for file uploads."""
    boundary = f"----InfinityBoundary{uuid.uuid4().hex}"
    mime, _ = mimetypes.guess_type(str(file_path))
    content_type = mime or "application/octet-stream"
    file_name = file_path.name
    file_bytes = file_path.read_bytes()

    lines: list[bytes] = []
    for name, value in fields.items():
        lines.append(f"--{boundary}".encode())
        lines.append(f'Content-Disposition: form-data; name="{name}"'.encode())
        lines.append(b"")
        lines.append(value.encode())

    lines.append(f"--{boundary}".encode())
    lines.append(
        f'Content-Disposition: form-data; name="{file_field}"; filename="{file_name}"'.encode()
    )
    lines.append(f"Content-Type: {content_type}".encode())
    lines.append(b"")
    lines.append(file_bytes)
    lines.append(f"--{boundary}--".encode())
    lines.append(b"")

    body = b"\r\n".join(lines)
    headers = f"multipart/form-data; boundary={boundary}"
    return body, headers


def _http_json(
    method: str,
    url: str,
    headers: Optional[Dict[str, str]] = None,
    body: Optional[bytes] = None,
) -> Dict[str, Any]:
    """Make a JSON HTTP call and return the parsed response (or error dict)."""
    req_headers = dict(headers or {})
    req = Request(url, data=body, headers=req_headers, method=method)  # noqa: S310
    try:
        with urlopen(req, timeout=DEFAULT_TIMEOUT_SECONDS) as resp:  # noqa: S310
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
        except OSError:
            detail = ""
        return {"error": f"HTTP {exc.code}: {detail}"}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


class FineTuner:
    """Provider-agnostic wrapper for Together AI / Fireworks fine-tuning."""

    VALID_PROVIDERS: frozenset[str] = frozenset({"together", "fireworks"})

    def __init__(
        self,
        provider: str = "together",
        api_key: Optional[str] = None,
        account: Optional[str] = None,
    ) -> None:
        provider = provider.lower().strip()
        if provider not in self.VALID_PROVIDERS:
            raise ValueError(f"provider must be one of {sorted(self.VALID_PROVIDERS)}")
        self.provider = provider
        self.api_key = api_key or self._default_key(provider)
        self.account = account or os.environ.get("FIREWORKS_ACCOUNT", "")

    @staticmethod
    def _default_key(provider: str) -> Optional[str]:
        env_map = {
            "together": "TOGETHER_API_KEY",
            "fireworks": "FIREWORKS_API_KEY",
        }
        return (os.environ.get(env_map[provider]) or "").strip() or None

    def configured(self) -> bool:
        return bool(self.api_key)

    # ---------------------- Together AI ---------------------- #
    def _together_upload(self, file_path: Path) -> Dict[str, Any]:
        if not self.api_key:
            return {"error": "Together API key not configured"}
        body, content_type = _encode_multipart({"purpose": "fine-tune"}, file_path)
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": content_type,
        }
        return _http_json(
            "POST", "https://api.together.xyz/v1/files", headers=headers, body=body
        )

    def _together_create_job(
        self,
        training_file_id: str,
        model: str,
        suffix: Optional[str],
        epochs: Optional[int],
    ) -> Dict[str, Any]:
        if not self.api_key:
            return {"error": "Together API key not configured"}
        payload: Dict[str, Any] = {
            "training_file": training_file_id,
            "model": model,
            "n_epochs": epochs if epochs is not None else 1,
        }
        if suffix:
            payload["suffix"] = suffix
        body = json.dumps(payload).encode()
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        return _http_json(
            "POST", "https://api.together.xyz/v1/fine-tunes", headers=headers, body=body
        )

    def _together_status(self, job_id: str) -> Dict[str, Any]:
        if not self.api_key:
            return {"error": "Together API key not configured"}
        headers = {"Authorization": f"Bearer {self.api_key}"}
        return _http_json(
            "GET", f"https://api.together.xyz/v1/fine-tunes/{job_id}", headers=headers
        )

    # ---------------------- Fireworks ---------------------- #
    def _fireworks_upload(self, file_path: Path) -> Dict[str, Any]:
        if not self.api_key:
            return {"error": "Fireworks API key not configured"}
        if not self.account:
            return {"error": "Fireworks account not configured"}
        body, content_type = _encode_multipart({"displayName": file_path.stem}, file_path)
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": content_type,
        }
        url = f"https://api.fireworks.ai/v1/accounts/{self.account}/datasets"
        return _http_json("POST", url, headers=headers, body=body)

    def _fireworks_create_job(
        self,
        dataset_id: str,
        model: str,
        suffix: Optional[str],
        epochs: Optional[int],
    ) -> Dict[str, Any]:
        if not self.api_key:
            return {"error": "Fireworks API key not configured"}
        if not self.account:
            return {"error": "Fireworks account not configured"}
        payload: Dict[str, Any] = {
            "baseModel": model,
            "dataset": dataset_id,
            "epochs": epochs if epochs is not None else 1,
        }
        if suffix:
            payload["displayName"] = suffix
        body = json.dumps(payload).encode()
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        url = f"https://api.fireworks.ai/v1/accounts/{self.account}/fineTuningJobs"
        return _http_json("POST", url, headers=headers, body=body)

    def _fireworks_status(self, job_id: str) -> Dict[str, Any]:
        if not self.api_key:
            return {"error": "Fireworks API key not configured"}
        if not self.account:
            return {"error": "Fireworks account not configured"}
        headers = {"Authorization": f"Bearer {self.api_key}"}
        url = f"https://api.fireworks.ai/v1/accounts/{self.account}/fineTuningJobs/{job_id}"
        return _http_json("GET", url, headers=headers)

    # ---------------------- Public API ---------------------- #
    def upload_and_tune(
        self,
        file_path: Path,
        model: str,
        suffix: Optional[str] = None,
        epochs: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Upload a JSONL dataset and start a fine-tune job.

        Returns a normalised dict with ok, job_id, provider, status, and raw.
        """
        file_path = Path(file_path)
        if not file_path.is_file():
            return {"ok": False, "error": f"File not found: {file_path}"}

        suffix = suffix or f"infinity-{int(time.time())}"

        if self.provider == "together":
            upload = self._together_upload(file_path)
            if "error" in upload:
                return {"ok": False, "provider": self.provider, "error": upload["error"]}
            file_id = upload.get("id") or upload.get("file", {}).get("id")
            if not file_id:
                return {"ok": False, "provider": self.provider, "error": "upload returned no file id", "raw": upload}
            job = self._together_create_job(file_id, model, suffix, epochs)
            if "error" in job:
                return {"ok": False, "provider": self.provider, "error": job["error"], "file_id": file_id}
            return {
                "ok": True,
                "provider": self.provider,
                "job_id": job.get("id"),
                "status": job.get("status", "unknown"),
                "raw": job,
            }

        if self.provider == "fireworks":
            upload = self._fireworks_upload(file_path)
            if "error" in upload:
                return {"ok": False, "provider": self.provider, "error": upload["error"]}
            dataset_id = upload.get("id") or upload.get("dataset", {}).get("id")
            if not dataset_id:
                return {"ok": False, "provider": self.provider, "error": "upload returned no dataset id", "raw": upload}
            job = self._fireworks_create_job(dataset_id, model, suffix, epochs)
            if "error" in job:
                return {"ok": False, "provider": self.provider, "error": job["error"], "dataset_id": dataset_id}
            return {
                "ok": True,
                "provider": self.provider,
                "job_id": job.get("id"),
                "status": job.get("status", "unknown"),
                "raw": job,
            }

        return {"ok": False, "error": f"unsupported provider {self.provider}"}

    def status(self, job_id: str) -> Dict[str, Any]:
        """Return the status of a running or completed fine-tune job."""
        if not self.api_key:
            return {"ok": False, "error": f"{self.provider} API key not configured"}
        if self.provider == "together":
            return self._together_status(job_id)
        if self.provider == "fireworks":
            return self._fireworks_status(job_id)
        return {"ok": False, "error": f"unsupported provider {self.provider}"}

    def public_config(self) -> Dict[str, Any]:
        """Safe summary for the UI (no raw keys)."""
        return {
            "provider": self.provider,
            "configured": self.configured(),
            "account": self.account if self.provider == "fireworks" else None,
        }


__all__ = ["FineTuner"]
