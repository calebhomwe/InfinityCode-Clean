# Video Understanding — Capability Reference (from YouTube tutorial transcript)

> Source: YouTube tutorial (transcript pasted 2026-08-10) on Claude Code / Codex
> raw-video understanding: drop an MP4, the agent breaks it into frames + audio
> + transcript and processes all of it with vision capabilities.
> Raw transcript: `docs/VIDEO_UNDERSTANDING_TRANSCRIPT.txt` (same folder).

## The technique (behind the scenes)

1. **Frames**: the video is broken into micro-frames (sampled stills).
2. **Audio**: the soundtrack is pulled and processed (transcript + silences).
3. **Marriage**: frames + audio/transcript are handed to the vision-language
   model, which watches "everything you say, how you say it, and what you
   looked like when you said it."
4. Context budget: frames are heavy — lower-resolution videos and fewer
   frames keep long videos inside the context window.

## The three use cases in the video

1. **Website improvements from a Loom link/recording**: screen-record a walk-
   through of your site + voice feedback -> the agent watches it end to end
   (not just the transcript) and produces a change list, optionally executes
   it against the codebase. No mega-prompts needed.
2. **Clone a 3D/award-winning website from a recording**: walk through a
   reference site, say which parts you like/don't like -> the recording is
   the whole prompt. The video pairs it with a generated 8s clip as the
   site's foundation (video -> images -> site).
3. **Rebuild a SaaS platform for yourself**: record yourself using the paid
   tool, narrating which features you value -> agent builds your own
   pay-as-you-wish internal version without 10-20 clarification prompts.

## Bonus: hack "record and replay" to audit workflows

Instead of building a skill, record a manual process (e.g., browsing X for
video ideas) and ask the agent to WATCH and produce an SOP + automation
opportunities (e.g., via the X API). Same capability, different output.

## How Infinity Code implements this (local-first, $0)

- **Frames**: `ffmpeg -i <video> -vf fps=1 <frames_dir>/frame_%04d.png`
  (ffmpeg is installed on this machine).
- **Vision**: `backend/core/vision_assist.py` -> local `qwen3-vl:8b` via
  Ollama (already wired local-first, free, `think:false`).
- **Transcript**: optional `faster-whisper` (install to enable; not yet
  installed on this machine).
- **Tool**: `Tools/video_understanding.py` (see below) — frames are sampled,
  each described by Qwen-VL, and the descriptions + optional transcript are
  assembled into one structured report the swarm/chat can act on.
- This mirrors the "weak-model equalizer" philosophy: even a cheap model can
  answer accurately when the vision layer supplies the visual truth.
