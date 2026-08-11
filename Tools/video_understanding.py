"""Video understanding: frames + transcript -> local Qwen-VL analysis.

Implements the Claude Code / Codex "raw video" pattern (see
docs/VIDEO_UNDERSTANDING_TUTORIAL.md) on Infinity Code's local-first stack:

  ffmpeg frame extraction -> qwen3-vl:8b (Ollama) describes each sampled
  frame -> optional transcript (faster-whisper if installed) -> one
  structured report the chat/swarm can act on.

Usage:
  python Tools/video_understanding.py <video.mp4> [--max-frames 12] [--fps 1]
                                       [--out report.md]

Zero cloud, zero cost (vision route is local-first; OpenRouter is fallback).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FRAME_GLOB = "frame_%04d.png"


def extract_frames(video: str, out_dir: str, fps: float) -> int:
    """ffmpeg -i video -vf fps=X -> out_dir/frame_%04d.png. Returns frame count."""
    pattern = os.path.join(out_dir, FRAME_GLOB)
    proc = subprocess.run(
        ["ffmpeg", "-y", "-i", video, "-vf", f"fps={fps}", pattern],
        capture_output=True,
        text=True,
        timeout=600,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg failed: " + (proc.stderr or proc.stdout)[-400:])
    frames = sorted(f for f in os.listdir(out_dir) if f.endswith(".png"))
    return len(frames)


def sample_frames(frames: list, max_frames: int) -> list:
    """Evenly sample at most max_frames frames across the video."""
    if len(frames) <= max_frames:
        return frames
    step = len(frames) / max_frames
    picked = []
    for i in range(max_frames):
        picked.append(frames[int(i * step)])
    return picked


def transcript_of(video: str) -> str:
    """faster-whisper transcript if installed; empty string otherwise."""
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError:
        return ""
    model = WhisperModel("small", device="cpu", compute_type="int8")
    segments, _info = model.transcribe(video, language="en")
    return "\n".join(s.text.strip() for s in segments if s.text.strip())


def main() -> int:
    parser = argparse.ArgumentParser(description="Local video understanding (frames -> Qwen-VL)")
    parser.add_argument("video", help="path to the MP4/recording")
    parser.add_argument("--max-frames", type=int, default=12, help="frames to analyze (sampled evenly)")
    parser.add_argument("--fps", type=float, default=1.0, help="frame extraction rate")
    parser.add_argument("--out", default=None, help="report output path (default: <video>.analysis.md)")
    args = parser.parse_args()

    if not os.path.exists(args.video):
        print(f"video not found: {args.video}")
        return 1

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + r"\backend")
    try:
        from core import vision_assist  # noqa: E402
    except ImportError:
        from backend.core import vision_assist  # type: ignore[no-redef]

    with tempfile.TemporaryDirectory(prefix="vid_frames_") as tmp:
        print(f"extracting frames ({args.fps} fps)...")
        total = extract_frames(args.video, tmp, args.fps)
        print(f"{total} frames extracted")
        frames = sample_frames(sorted(f for f in os.listdir(tmp) if f.endswith(".png")), args.max_frames)

        print(f"analyzing {len(frames)} sampled frames with local Qwen-VL...")
        descriptions = []
        for name in frames:
            path = os.path.join(tmp, name)
            desc = vision_assist.describe_image(path, "What is happening in this video frame?")
            if desc:
                descriptions.append(f"### {name}\n{desc.strip()}")
            print(f"  {name}: {'ok' if desc else 'no description (route failed)'}")

        print("transcript (optional)...")
        transcript = transcript_of(args.video)

    report = [f"# Video analysis: {os.path.basename(args.video)}",
              f"- Frames extracted: {total}, analyzed: {len(frames)}"]
    if transcript:
        report.append(f"- Transcript ({len(transcript.splitlines())} lines):\n\n{transcript}")
    else:
        report.append("- Transcript: not available (install faster-whisper to enable)")
    report.append("\n## Frame-by-frame (Qwen-VL)\n" + "\n\n".join(descriptions))

    out = args.out or os.path.splitext(args.video)[0] + ".analysis.md"
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(report))
    print(f"report written to {out} ({len(descriptions)} frame descriptions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
