---
name: openai-image
description: Generate or edit images with the OpenAI Images API (gpt-image-2). Use for text-to-image (文字生圖) and image-to-image / editing / style transfer with reference images (圖生圖), e.g. "畫一張…", "生成圖片", "把這張圖改成…", "generate an image", "edit this photo".
---

# OpenAI image generation (文字生圖 / 圖生圖)

All calls go through `scripts/openai_image.py` (Python 3 + `requests`).
Run it from the repository root:

```bash
S=.claude/skills/openai-image/scripts/openai_image.py
```

## Text-to-image (文字生圖)

```bash
python3 $S generate "一隻戴墨鏡的柴犬在台北101前自拍，電影感，黃昏" \
  --size 1536x1024 --quality high
```

## Image-to-image (圖生圖)

```bash
python3 $S edit "改成吉卜力動畫風格，保留人物構圖" --image path/to/photo.jpg
# multiple reference images (combine / composite)
python3 $S edit "把這兩件商品放在同一張木桌上拍成型錄照" --image a.png --image b.png
# local edit with a mask (transparent pixels in the mask = area to redraw)
python3 $S edit "把天空換成星空" --image photo.png --mask mask.png
# keep faces / details closer to the original
python3 $S edit "換成正式西裝" --image me.jpg --input-fidelity high
```

## Options

| flag | values | note |
|---|---|---|
| `--model` | default `gpt-image-2` | or env `OPENAI_IMAGE_MODEL` |
| `--size` | `1024x1024`, `1536x1024` (橫), `1024x1536` (直), `auto` | |
| `--quality` | `low` / `medium` / `high` / `auto` | `low` is fastest/cheapest for drafts |
| `--n` | 1–10 | number of variations |
| `--background` | `transparent` / `opaque` / `auto` | transparent needs png or webp |
| `--output-format` | `png` / `jpeg` / `webp` | |
| `--out` | folder | default `generated-images/` (git-ignored) |
| `--name` | base filename | default `<timestamp>-<gen|edit>-<prompt-slug>` |

The script prints the absolute path of each saved image and writes a
`<name>.json` sidecar (prompt, model, token usage) next to it.

## Workflow

1. Turn the user's request into a concrete prompt: subject, scene, style,
   lighting, composition, aspect ratio. Keep any text that must appear in the
   image in quotes inside the prompt. Write the prompt in the user's language
   unless English is clearly better.
2. Pick `generate` (no input image) or `edit` (user supplied images).
   For 圖生圖, the input image must be a local file: ask the user to upload it
   in the chat or put it in the repo.
3. For quick drafts use `--quality low`; for finals use `high`.
4. Run the script (it can take 30–120 s; use a timeout of at least 600000 ms).
5. Show the result: give the user the saved file path(s) (send them with
   SendUserFile when available) and the prompt used. Offer one or two concrete
   variations rather than a long menu.

## API key

- **Claude Code cloud session**: the key is stored as a network secret for
  `api.openai.com` on the environment and injected by the proxy. Do not set
  or ask for `OPENAI_API_KEY`; the script sends a placeholder that the proxy
  replaces. If you get 401/403, the saved key is missing or wrong — ask the
  user to update it in the environment settings (never in chat).
- **Local Mac**: `export OPENAI_API_KEY=sk-...` in the shell profile.

## Errors

- `403` from the proxy / `CONNECT tunnel failed`: `api.openai.com` is not
  allowed by the environment's network policy.
- `400 ... organization must be verified`: the OpenAI org needs verification
  for gpt-image models (platform.openai.com → Settings → Organization).
- `400 ... safety system`: rephrase the prompt.
