---
name: image-generation
description: Create or edit images with the OpenAI Images API. Covers text-to-image (generate from a description) and image-to-image (restyle a photo, edit part of an image with a mask, combine several reference images). Use whenever the user asks to draw, generate, render, illustrate, restyle or edit a picture, or sends a photo together with an image prompt (style words like "oil painting", "watercolor", "masterpiece").
---

# Image generation

Claude cannot output images itself. In this environment `api.openai.com` is a
connected service (the proxy injects the key), so images are made through the
OpenAI Images API with `scripts/gen_image.py`. Use the script, not hand-written
`curl`: it already handles every failure seen so far.

## 1. Before calling the API

- **Confirm intent and cost when it is ambiguous.** A photo plus a list of
  style words almost always means "make this picture"; say what you are about
  to do in one line and proceed. If the message could mean something else
  (rewrite the prompt, explain it, critique the photo), ask first. Each
  image costs the user money; don't generate batches or re-runs nobody asked for.
- **Pick the mode:**
  | The user wants… | Mode | Script flags |
  |---|---|---|
  | A new image from a description | text-to-image | `--prompt` only |
  | Their photo in another style / with changes | image-to-image | `--image photo.jpg` |
  | Only one region changed | masked edit | `--image photo.png --mask mask.png` |
  | Elements from several pictures combined | multi-reference | `--image a.jpg --image b.jpg …` (up to 16) |
- Copy uploads into the scratchpad and write outputs there. Don't put images
  in the repo unless the user asks.

## 2. Run the script

```bash
python3 .claude/skills/image-generation/scripts/gen_image.py OUT.png --prompt "..." [options]
```

Give the Bash call `timeout: 600000`: high quality takes 60–90 s per image.

| Option | Default | Notes |
|---|---|---|
| `--image FILE` | — | Repeatable. Present → edit endpoint; absent → generate endpoint |
| `--mask FILE` | — | PNG with alpha, same size as the first image, < 4 MB. Transparent = area to change. Reported as unreliable on gpt-image-2: describe the change precisely in the prompt too |
| `--model` | `gpt-image-2` | Checked against `/v1/models`; falls back to `gpt-image-1.5` → `chatgpt-image-latest` → `gpt-image-1-mini` |
| `--size` | `auto` | `auto` = match the first input image's aspect ratio (else square); `square`/`portrait`/`landscape`; or `WxH` |
| `--quality` | `high` | `low` (~10–15 s, drafts) · `medium` · `high` · `auto` |
| `--n` | 1 | Number of variants; made as sequential requests → `OUT-1.png`, `OUT-2.png`… |
| `--output-format` | png | `png` / `jpeg` / `webp`; match the output file's extension |
| `--output-compression` | — | 0–100, jpeg/webp only |
| `--background` | — | `transparent` (png/webp only), `opaque`, `auto` |
| `--moderation` | — | `auto` / `low` |
| `--dry-run` | — | Print the request without sending it |

Size rules for gpt-image-2 (the script's `auto` already follows them): width and
height divisible by 16; aspect ratio between 1:3 and 3:1; 655,360–8,294,400
total pixels; max 3840x2160; above 2560x1440 is experimental. Other models
only accept the presets 1024x1024, 1024x1536, 1536x1024.

## 3. Write the prompt

Structure: **what to do → subject → style → what to keep / constraints.**

- **Text-to-image:** describe subject, setting, composition (framing, camera
  angle, foreground/background), lighting, palette, and medium/style. Concrete
  nouns beat adjectives. Put any text that must appear in the image in quotes,
  and keep it short.
- **Image-to-image:** start with the instruction ("Repaint this photo as…",
  "Change only the sky to…"), then the user's style words verbatim, then a
  concrete "Keep the same composition: …" listing the main subjects, people
  (pose, clothing, what they hold), buildings and objects. Listing what to keep
  is what preserves the photo.
- **Multi-reference:** refer to inputs by order: "the woman from the first
  image sitting on the bench from the second image".
- **Style vs. source conflicts:** the source photo's light and colours win
  unless you push hard. If the prompt says muted / overcast / diffused but the
  photo is sunny and saturated, write it explicitly ("overcast, low-contrast,
  desaturated, no direct sunlight").
- If the prompt describes things the photo lacks (snow, wet street), say to
  add them to this scene rather than replace it.
- `input_fidelity` does not exist for gpt-image-2 (input images are always high
  fidelity); don't pass it.

## 4. Check and deliver

1. Look at every result with Read before sending it.
2. Send with SendUserFile (`display: "render"`), full absolute paths.
3. Report honestly: what matched the prompt, and where it didn't (garbled
   small text/signage, simplified or distorted objects, lighting or palette
   the model ignored). Offer one concrete re-run idea; don't re-run unasked.

## Known failures (handled by the script)

| Symptom | Cause | Handling |
|---|---|---|
| HTTP 400 `The model 'gpt-image-1' does not exist` | Model not enabled on this account | Model check + fallback list |
| HTTP 502 `upstream request failed` after ~30 s | The session proxy drops connections idle for ~30 s; non-streamed calls send nothing until the image is done | Always `stream=true` + `partial_images`; `curl -N` |
| HTTP 400 `Width and height must both be divisible by 16` | Hand-picked size | `--size auto` computes a valid size; 4xx is not retried |
| Intermittent 5xx | Upstream hiccup | 2 retries with backoff |
| Large uploads slow/rejected | Input > 20 MB | Downsized to ≤ 2048 px (API limit is 50 MB per image) |
| All attempts fail | — | Last partial preview saved as `*.partial.png`; tell the user it's only a preview |

Last resort if streaming itself breaks: `--quality low` finishes in ~15 s,
under the proxy limit, at visibly lower quality. Say so if you use it.

## Not covered here

- `gpt-image-2.5-*` / `gpt-image-2.6-*` models appear in `/v1/models`, and some
  OpenAI docs mention extra quality levels (`xhigh`, `max`) and formats for them.
  These are untested; use them only if the user asks, and check the docs first.
- Multi-turn iterative editing via the Responses API `image_generation` tool is
  not wrapped by the script. For a follow-up tweak, re-run an edit with the
  previous output as `--image`.
- `developers.openai.com` is not reachable from this environment; use
  WebSearch to look up API details.
