"""
SceneForge Render Service
--------------------------
A small FastAPI service that does the one thing Lovable/Supabase can't:
run moviepy + ffmpeg to assemble scene images + narration + captions into video.

Two endpoints, kept deliberately separate so this fits inside Render's free-tier
512MB RAM limit even for a long (11-13 min) documentary-style video:

  POST /render-scene   -> renders ONE scene (a handful of shots) to a video file
  POST /concat-scenes  -> stitches already-rendered scene videos into the final video
                           (uses ffmpeg's stream-copy concat, not moviepy, so it's fast and light)

Deploy this on Render.com's free web service tier (see render.yaml + README.md).
The Lovable frontend calls this service directly over HTTPS.
"""

import os
import uuid
import tempfile
import requests
from typing import List, Optional, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from moviepy import (
    ImageClip, TextClip, ColorClip, CompositeVideoClip,
    concatenate_videoclips, AudioFileClip, AudioClip, afx
)

app = FastAPI(title="SceneForge Render Service")

FONT_PATH = os.path.join(os.path.dirname(__file__), "fonts", "Oswald-Bold.ttf")
VIDEO_SIZE = (1280, 720)  # 720p — keep it lighter than 1080p for free-tier RAM/CPU
FPS = 24


# ---------- request schemas ----------

class Shot(BaseModel):
    type: Literal["image", "text_card", "black"]
    duration: float  # seconds
    image_url: Optional[str] = None          # required if type == "image"
    text: Optional[str] = None                # required if type == "text_card"
    subtext: Optional[str] = None              # optional smaller line under text_card
    caption: Optional[str] = None              # optional burned-in caption (for image shots)
    zoom: bool = True                          # Ken Burns effect for image shots


class RenderSceneRequest(BaseModel):
    scene_id: str
    shots: List[Shot]
    narration_audio_url: Optional[str] = None  # full narration track for this scene, if any


class ConcatRequest(BaseModel):
    project_id: str
    scene_video_urls: List[str]  # in final order


# ---------- helpers ----------

def download_to_temp(url: str, suffix: str) -> str:
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    path = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4()}{suffix}")
    with open(path, "wb") as f:
        f.write(resp.content)
    return path


def build_image_shot_clip(shot: Shot):
    img_path = download_to_temp(shot.image_url, ".jpg")
    clip = ImageClip(img_path).with_duration(shot.duration)

    # fit/crop to VIDEO_SIZE while preserving aspect ratio (center-crop)
    clip = clip.resized(height=VIDEO_SIZE[1])
    if clip.w < VIDEO_SIZE[0]:
        clip = clip.resized(width=VIDEO_SIZE[0])
    clip = clip.cropped(
        x_center=clip.w / 2, y_center=clip.h / 2,
        width=VIDEO_SIZE[0], height=VIDEO_SIZE[1]
    )

    if shot.zoom:
        d = shot.duration
        clip = clip.resized(lambda t: 1 + 0.06 * (t / d))

    layers = [clip]

    if shot.caption:
        cap = TextClip(
            text=shot.caption, font=FONT_PATH, font_size=42, color="white",
            stroke_color="black", stroke_width=2,
            size=(int(VIDEO_SIZE[0] * 0.85), None), method="caption",
        ).with_duration(shot.duration).with_position(("center", VIDEO_SIZE[1] - 130))
        layers.append(cap)

    return CompositeVideoClip(layers, size=VIDEO_SIZE).with_duration(shot.duration)


def build_text_card_clip(shot: Shot):
    bg = ColorClip(size=VIDEO_SIZE, color=(10, 10, 12)).with_duration(shot.duration)
    layers = [bg]

    main = TextClip(
        text=shot.text, font=FONT_PATH, font_size=80, color="white",
        size=(int(VIDEO_SIZE[0] * 0.9), None), method="caption",
    ).with_duration(shot.duration).with_position("center")
    layers.append(main)

    if shot.subtext:
        sub = TextClip(
            text=shot.subtext, font=FONT_PATH, font_size=36, color="#aaaaaa",
            size=(int(VIDEO_SIZE[0] * 0.8), None), method="caption",
        ).with_duration(shot.duration).with_position(("center", 0.65), relative=True)
        layers.append(sub)

    return CompositeVideoClip(layers, size=VIDEO_SIZE).with_duration(shot.duration)


def build_black_clip(shot: Shot):
    return ColorClip(size=VIDEO_SIZE, color=(0, 0, 0)).with_duration(shot.duration)


# ---------- endpoints ----------

@app.post("/render-scene")
def render_scene(req: RenderSceneRequest):
    if not req.shots:
        raise HTTPException(400, "No shots provided")

    clips = []
    try:
        for shot in req.shots:
            if shot.type == "image":
                if not shot.image_url:
                    raise HTTPException(400, "image_url required for image shot")
                clips.append(build_image_shot_clip(shot))
            elif shot.type == "text_card":
                if not shot.text:
                    raise HTTPException(400, "text required for text_card shot")
                clips.append(build_text_card_clip(shot))
            elif shot.type == "black":
                clips.append(build_black_clip(shot))

        scene_clip = concatenate_videoclips(clips, method="compose")

        # Every scene MUST end up with an audio track (even silent), otherwise
        # ffmpeg's fast stream-copy concat in /concat-scenes breaks on mismatched
        # streams between scenes that have narration and scenes that don't.
        if req.narration_audio_url:
            audio_path = download_to_temp(req.narration_audio_url, ".mp3")
            audio = AudioFileClip(audio_path)
            if audio.duration > scene_clip.duration:
                audio = audio.subclipped(0, scene_clip.duration)
            elif audio.duration < scene_clip.duration:
                # pad with silence so it still matches scene length exactly
                pad = AudioClip(lambda t: [0, 0], duration=scene_clip.duration - audio.duration, fps=44100)
                from moviepy import concatenate_audioclips
                audio = concatenate_audioclips([audio, pad])
        else:
            # stereo silence — MUST match channel count of narrated scenes,
            # or ffmpeg's stream-copy concat in /concat-scenes will silently corrupt output
            audio = AudioClip(lambda t: [0, 0], duration=scene_clip.duration, fps=44100)

        scene_clip = scene_clip.with_audio(audio)

        out_path = os.path.join(tempfile.gettempdir(), f"scene-{req.scene_id}.mp4")
        scene_clip.write_videofile(
            out_path, fps=FPS, codec="libx264", audio_codec="aac", logger=None
        )
    finally:
        for c in clips:
            try:
                c.close()
            except Exception:
                pass

    return FileResponse(out_path, media_type="video/mp4", filename=f"scene-{req.scene_id}.mp4")


@app.post("/concat-scenes")
def concat_scenes(req: ConcatRequest):
    if not req.scene_video_urls:
        raise HTTPException(400, "No scene videos provided")

    local_paths = [download_to_temp(url, ".mp4") for url in req.scene_video_urls]

    list_file = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4()}.txt")
    with open(list_file, "w") as f:
        for p in local_paths:
            f.write(f"file '{p}'\n")

    out_path = os.path.join(tempfile.gettempdir(), f"final-{req.project_id}.mp4")

    # ffmpeg concat demuxer: stream-copies instead of re-encoding, so this is fast
    # and light on RAM even for an 11-13 minute final video.
    import subprocess
    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", list_file, "-c", "copy", out_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise HTTPException(500, f"ffmpeg concat failed: {result.stderr[-2000:]}")

    return FileResponse(out_path, media_type="video/mp4", filename=f"final-{req.project_id}.mp4")


@app.get("/health")
def health():
    return {"status": "ok"}
