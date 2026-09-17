# SceneForge Render Service

The one piece of this project that Lovable can't build — a small Python service
that runs moviepy + ffmpeg to actually assemble your scene images, narration,
and text cards into video. Tested end-to-end before being handed to you (scene
rendering, narration audio sync, and multi-scene concatenation all confirmed
working).

## Deploy (free, ~5 minutes)

1. Push this `render-service/` folder to its own GitHub repo (separate from
   your Lovable frontend repo). Make sure `Dockerfile` is in the repo root
   alongside `app.py`.
2. Go to [render.com](https://render.com), sign up (no credit card needed).
3. New → Blueprint → connect the repo. Render will read `render.yaml` and
   set everything up automatically.
4. Once deployed, you'll get a URL like `https://sceneforge-render-service.onrender.com`
   — this is what your Lovable app calls.

## Free tier notes (important)

- Spins down after 15 minutes idle; the next request takes 30-60 seconds to
  wake up. Fine for a hobby project, just don't expect instant response on
  the first call of a session.
- 512MB RAM. This is why the service renders **one scene at a time**
  (`/render-scene`) and stitches the final video with ffmpeg's fast
  stream-copy concat (`/concat-scenes`) instead of loading an entire
  11-13 minute video into memory at once. Keep individual scenes to a
  handful of shots each — if a single scene has a huge number of shots,
  split it into two.

## API

### `POST /render-scene`
Renders one scene (a list of shots) to an MP4.

```json
{
  "scene_id": "scene-01",
  "narration_audio_url": "https://.../scene01-narration.mp3",
  "shots": [
    { "type": "black", "duration": 3 },
    { "type": "text_card", "text": "$1.75 BILLION", "duration": 4 },
    { "type": "image", "image_url": "https://images.pexels.com/...", "duration": 4, "caption": "Person using smartphone", "zoom": true }
  ]
}
```
Returns the rendered MP4 file directly.

Shot types:
- `image` — a Pexels (or other) photo URL, Ken Burns zoom applied by default, optional burned-in caption
- `text_card` — a full-frame text card (like your script's "$1.75 BILLION" or date-transition cards), optional smaller subtext line
- `black` — a plain black hold, for hooks/pauses

### `POST /concat-scenes`
Stitches already-rendered scene videos into the final video, in order.

```json
{
  "project_id": "quibi-video",
  "scene_video_urls": [
    "https://your-supabase-project.supabase.co/storage/v1/object/public/scene-videos/scene-01.mp4",
    "https://your-supabase-project.supabase.co/storage/v1/object/public/scene-videos/scene-02.mp4"
  ]
}
```
Returns the final MP4 file directly.

### `GET /health`
Simple uptime check — call this first if you're not sure the service has
woken up yet.

## How this fits into the full app

1. Lovable's `generate-scene-video` edge function calls `/render-scene` for
   each scene once its shots (images + text cards + narration) are ready,
   then uploads the returned MP4 to your Supabase `scene-videos` bucket.
2. Once every scene is rendered, Lovable's `assemble-video` edge function
   calls `/concat-scenes` with all the scene video URLs in order, and
   uploads the result to your `final-videos` bucket.

This replaces the earlier ffmpeg.wasm-in-browser approach — for an
11-13 minute documentary-style video with lots of shots, doing the heavy
lifting server-side (even on a free, occasionally-slow instance) is far
more reliable than asking a visitor's browser to do it.
