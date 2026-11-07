---
name: photo-to-painting
description: Repaint a user's photo in an art style (impasto oil painting, watercolor, etc.) with the OpenAI image edit API, keeping the original composition. Use when the user sends a photo plus a style prompt such as "impasto oil painting, thick palette knife strokes...", or asks to turn a photo into a painting.
---

# Photo to painting

Turn the user's photo into a painting in the style they describe, using
`scripts/edit_image.py`. The script already handles the failures seen before;
use it instead of hand-written `curl` calls.

## Steps

1. Copy the uploaded image into the scratchpad (never into the repo unless asked).
2. Write the prompt (see "Prompt" below).
3. Run:
   ```bash
   python3 .claude/skills/photo-to-painting/scripts/edit_image.py \
     <input.jpg> <scratchpad>/<name>.png --prompt "<prompt>"
   ```
   High quality takes about 60–90 s; give the Bash call a timeout of at least 600000 ms.
4. Look at the result with Read, then send it with SendUserFile (`display: render`).
5. Tell the user honestly where it diverges from the prompt (garbled text,
   simplified objects, lighting that ignored the prompt), and offer a re-run.

## Prompt

Start with the instruction, then the user's style words verbatim, then
what to keep:

```
Repaint this photo as <user's style prompt>.
Keep the same composition: <list the main subjects, people (clothing, pose,
what they hold), buildings, trees, signage> in their original positions.
```

- Listing what to keep is what preserves the composition; describe the photo concretely.
- If the user's prompt describes a scene the photo doesn't contain (e.g. snow,
  wet street), add those elements onto the photo rather than replacing it.
- If the photo is very saturated and the prompt asks for "muted" or "diffused"
  light, say it explicitly and strongly ("overcast, low-contrast, desaturated"),
  otherwise the model keeps the original sunlight and colours.

## Known failures and how the script avoids them

| Symptom | Cause | Handling |
|---|---|---|
| HTTP 400 `The model 'gpt-image-1' does not exist` | That model isn't enabled on this account | Script checks `/v1/models` and falls back through `gpt-image-2` → `gpt-image-1.5` → `chatgpt-image-latest` → `gpt-image-1-mini` |
| HTTP 502 `upstream request failed` after ~30 s | The session's outbound proxy drops a connection with no data for ~30 s; non-streaming edits send nothing until the image is done (60–90 s at high quality). Image file size is not the cause. | Script always uses `stream=true` + `partial_images=2`, so data keeps flowing; `curl -N` disables buffering |
| Intermittent 5xx | Upstream hiccup | Script retries (default 2) with backoff; 4xx errors are not retried |
| Upload very slow / rejected | Input over 4 MB | Script downsizes to ≤2048 px before upload |

Other notes:
- No API key needed: `api.openai.com` credentials are injected by the proxy;
  the script sends a dummy bearer token.
- Do not pass `input_fidelity`; it was a `gpt-image-1` parameter and is untested on `gpt-image-2`.
- Output size is chosen from the photo's aspect ratio (`1024x1536` portrait,
  `1536x1024` landscape, `1024x1024` square); override with `--size`.
- If every attempt fails, the script saves the last partial preview as
  `<output>.partial.png` if one arrived; tell the user it is only a preview.
- If streaming itself fails, `--quality low` (≈15 s) fits under the 30 s limit
  as a last resort, at visibly lower quality — say so if you use it.
