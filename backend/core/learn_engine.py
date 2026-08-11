"""Tutorial ingestion pipeline: YouTube URL -> transcribed steps -> skill JSON.

Downloads audio with yt-dlp, transcribes locally with Whisper (model "base"),
extracts instruction-bearing segments, and saves an unverified skill to the
skill library. Nothing is marked verified until it has actually been executed
and checked by the skill engine.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, TypedDict
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

STEP_KEYWORDS: List[str] = [
    "first",
    "next",
    "then",
    "now",
    "step",
    "click",
    "open",
    "create",
]

YTDLP_TIMEOUT_SECONDS: int = 600
WHISPER_TIMEOUT_SECONDS: int = 1800
MAX_STEPS: int = 50
FALLBACK_SEGMENT_COUNT: int = 10

# Hosts yt-dlp is allowed to fetch from in this pipeline.
_ALLOWED_YT_HOSTS: frozenset[str] = frozenset(
    {
        "youtube.com",
        "www.youtube.com",
        "youtu.be",
        "m.youtube.com",
        "music.youtube.com",
        "www.youtu.be",
    }
)


def _is_allowed_youtube_url(url: str) -> bool:
    """Reject non-http(s) URLs and hosts outside the YouTube domain set."""
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    return host in _ALLOWED_YT_HOSTS

_DEFAULT_SKILLS_DIR: Path = Path(__file__).resolve().parents[1] / "skills"


class SkillStep(TypedDict):
    """One instruction extracted from the transcript."""

    order: int
    instruction: str
    start_time: float
    end_time: float


class ExtractedSkill(TypedDict):
    """A skill distilled from a tutorial, saved to skills/{name}.json."""

    name: str
    topic: str
    source_url: str
    source_type: str
    steps: List[SkillStep]
    verification_command: str
    verified: bool
    created_at: str


class LearnEngineError(RuntimeError):
    """Raised when tutorial ingestion fails at any stage."""


class LearnEngine:
    """Turns YouTube tutorials into executable skill definitions."""

    def __init__(self, skills_dir: Optional[Path] = None) -> None:
        self.skills_dir: Path = Path(skills_dir) if skills_dir else _DEFAULT_SKILLS_DIR
        try:
            self.skills_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise LearnEngineError(
                f"Cannot create skills directory {self.skills_dir}: {exc}"
            ) from exc

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _slugify(topic: str, url: str) -> str:
        slug: str = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-") or "skill"
        url_hash: str = hashlib.md5(url.encode("utf-8")).hexdigest()[:8]
        return f"{slug}-{url_hash}"

    @staticmethod
    def _resolve_tool(tool_name: str) -> str:
        """Find a CLI tool, preferring the running interpreter's Scripts dir.

        The server may run from a venv whose Scripts/ is not on the process
        PATH, so bare names would miss the venv-installed yt-dlp/whisper.
        """
        interpreter_dir: Path = Path(sys.executable).resolve().parent
        for candidate in (
            interpreter_dir / f"{tool_name}.exe",
            interpreter_dir / tool_name,
        ):
            if candidate.is_file():
                return str(candidate)
        found: Optional[str] = shutil.which(tool_name)
        return found if found else tool_name

    @classmethod
    def _run_tool(
        cls, command: List[str], timeout: int, tool_name: str
    ) -> subprocess.CompletedProcess:
        command = [cls._resolve_tool(command[0]), *command[1:]]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise LearnEngineError(
                f"{tool_name} is not installed or not on PATH. "
                f"Install it first (pip install {tool_name})."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise LearnEngineError(
                f"{tool_name} timed out after {timeout}s."
            ) from exc
        except OSError as exc:
            raise LearnEngineError(f"{tool_name} failed to launch: {exc}") from exc

        if result.returncode != 0:
            stderr_tail: str = (result.stderr or "")[-500:]
            raise LearnEngineError(
                f"{tool_name} exited with code {result.returncode}: {stderr_tail}"
            )
        return result

    @staticmethod
    def _extract_steps(segments: List[Dict[str, Any]]) -> List[SkillStep]:
        """Keep segments that carry instructions (keyword match)."""
        matched: List[Dict[str, Any]] = []
        for segment in segments:
            text: str = str(segment.get("text", "")).strip()
            if not text:
                continue
            lowered: str = text.lower()
            if any(keyword in lowered for keyword in STEP_KEYWORDS):
                matched.append(segment)

        # A tutorial with no keyword hits still has content — take the opening
        # segments rather than returning an empty, useless skill.
        if not matched:
            matched = [s for s in segments if str(s.get("text", "")).strip()][
                :FALLBACK_SEGMENT_COUNT
            ]

        steps: List[SkillStep] = []
        for order, segment in enumerate(matched[:MAX_STEPS], start=1):
            try:
                steps.append(
                    SkillStep(
                        order=order,
                        instruction=str(segment.get("text", "")).strip(),
                        start_time=float(segment.get("start", 0.0)),
                        end_time=float(segment.get("end", 0.0)),
                    )
                )
            except (TypeError, ValueError):
                continue
        return steps

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def ingest_youtube(self, url: str, topic: str) -> ExtractedSkill:
        """Download, transcribe, and distill a YouTube tutorial into a skill."""
        if not url or not url.strip():
            raise LearnEngineError("ingest_youtube() requires a non-empty URL.")
        if not topic or not topic.strip():
            raise LearnEngineError("ingest_youtube() requires a non-empty topic.")
        if not _is_allowed_youtube_url(url):
            raise LearnEngineError(
                "Only youtube.com / youtu.be URLs are allowed for ingestion."
            )

        name: str = self._slugify(topic, url)
        workdir: Path = Path(tempfile.mkdtemp(prefix="infinity_learn_"))
        try:
            # 1. Download audio.
            audio_template: str = str(workdir / "audio.%(ext)s")
            self._run_tool(
                [
                    "yt-dlp",
                    "-x",
                    "--audio-format",
                    "mp3",
                    "--no-playlist",
                    "-o",
                    audio_template,
                    url,
                ],
                timeout=YTDLP_TIMEOUT_SECONDS,
                tool_name="yt-dlp",
            )
            audio_files: List[Path] = sorted(workdir.glob("audio.*"))
            if not audio_files:
                raise LearnEngineError(
                    f"yt-dlp reported success but no audio file found in {workdir}."
                )
            audio_path: Path = audio_files[0]

            # 2. Transcribe locally.
            self._run_tool(
                [
                    "whisper",
                    str(audio_path),
                    "--model",
                    "base",
                    "--output_format",
                    "json",
                    "--output_dir",
                    str(workdir),
                    "--fp16",
                    "False",
                ],
                timeout=WHISPER_TIMEOUT_SECONDS,
                tool_name="whisper",
            )
            transcript_path: Path = workdir / f"{audio_path.stem}.json"
            if not transcript_path.is_file():
                raise LearnEngineError(
                    f"Whisper finished but transcript not found at {transcript_path}."
                )

            # 3. Parse segments.
            try:
                transcript: Dict[str, Any] = json.loads(
                    transcript_path.read_text(encoding="utf-8")
                )
            except (json.JSONDecodeError, OSError) as exc:
                raise LearnEngineError(
                    f"Could not parse Whisper JSON output: {exc}"
                ) from exc
            segments: List[Dict[str, Any]] = list(transcript.get("segments", []))
            if not segments:
                raise LearnEngineError("Transcript contained no segments.")

            steps: List[SkillStep] = self._extract_steps(segments)
            if not steps:
                raise LearnEngineError("No usable instruction steps extracted.")

            # 4. Build and save the skill.
            skill_path: Path = self.skills_dir / f"{name}.json"
            verification_command: str = (
                f'python -c "import json; json.load(open(r\'{skill_path}\', '
                f"encoding='utf-8')); print('skill json ok')\""
            )
            skill: ExtractedSkill = ExtractedSkill(
                name=name,
                topic=topic.strip(),
                source_url=url.strip(),
                source_type="youtube",
                steps=steps,
                verification_command=verification_command,
                verified=False,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            try:
                skill_path.write_text(
                    json.dumps(skill, indent=2, ensure_ascii=False), encoding="utf-8"
                )
            except OSError as exc:
                raise LearnEngineError(
                    f"Could not save skill to {skill_path}: {exc}"
                ) from exc

            logger.info("Ingested skill %r (%d steps) -> %s", name, len(steps), skill_path)
            return skill
        finally:
            # 5. Cleanup downloaded/intermediate files.
            shutil.rmtree(workdir, ignore_errors=True)


__all__ = [
    "LearnEngine",
    "LearnEngineError",
    "ExtractedSkill",
    "SkillStep",
    "STEP_KEYWORDS",
]
