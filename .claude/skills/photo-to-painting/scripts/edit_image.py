#!/usr/bin/env python3
"""Repaint a photo in a given style with the OpenAI image edit API.

Always uses streaming (stream=true + partial_images): the cloud session's
outbound proxy drops connections that receive no data for ~30 s, and a
high-quality edit takes 60-90 s, so a non-streaming request fails with
HTTP 502 "upstream request failed".

Usage:
  edit_image.py INPUT OUTPUT --prompt "..." [--model gpt-image-2]
                [--size 1024x1536] [--quality high] [--retries 2]
"""
import argparse, base64, json, os, subprocess, sys, tempfile, time

PREFERRED_MODELS = ["gpt-image-2", "gpt-image-1.5", "chatgpt-image-latest", "gpt-image-1-mini"]
AUTH = "Authorization: Bearer sk-dummy"  # proxy injects the real key
MAX_UPLOAD = 4 * 1024 * 1024


def curl(args, timeout):
    return subprocess.run(["curl", "-sS", *args], capture_output=True, text=True, timeout=timeout)


def available_models():
    r = curl(["https://api.openai.com/v1/models", "-H", AUTH], 60)
    try:
        return {m["id"] for m in json.loads(r.stdout)["data"]}
    except Exception:
        return set()


def pick_model(requested):
    models = available_models()
    if not models or requested in models:
        return requested
    for m in PREFERRED_MODELS:
        if m in models:
            print(f"model {requested!r} not available, using {m!r}", file=sys.stderr)
            return m
    sys.exit(f"no image model available; got: {sorted(models)}")


def auto_size(path):
    try:
        from PIL import Image
        w, h = Image.open(path).size
    except Exception:
        return "1024x1536"
    if abs(w - h) / max(w, h) < 0.1:
        return "1024x1024"
    return "1024x1536" if h > w else "1536x1024"


def prepare(path, workdir):
    """Shrink very large uploads (keeps aspect ratio) to avoid slow/failed uploads."""
    if os.path.getsize(path) <= MAX_UPLOAD:
        return path
    from PIL import Image
    im = Image.open(path).convert("RGB")
    im.thumbnail((2048, 2048))
    out = os.path.join(workdir, "upload.jpg")
    im.save(out, quality=90)
    return out


def run(image, prompt, model, size, quality, stream_file):
    args = ["-N", "--max-time", "600", "https://api.openai.com/v1/images/edits", "-H", AUTH,
            "-F", f"model={model}", "-F", f"image=@{image}", "-F", f"prompt={prompt}",
            "-F", f"size={size}", "-F", f"quality={quality}",
            "-F", "stream=true", "-F", "partial_images=2",
            "-o", stream_file, "-w", "%{http_code}"]
    r = curl(args, 660)
    return r.stdout.strip(), r.stderr.strip()


def parse(stream_file):
    final = partial = None
    raw = open(stream_file, encoding="utf-8", errors="replace").read()
    for line in raw.splitlines():
        if not line.startswith("data:"):
            continue
        try:
            ev = json.loads(line[5:])
        except Exception:
            continue
        if ev.get("b64_json"):
            if ev.get("type", "").endswith("completed"):
                final = ev["b64_json"]
            else:
                partial = ev["b64_json"]
    if final is None:
        try:  # a non-streamed error body, e.g. {"error": {...}}
            err = json.loads(raw).get("error", {})
            return None, partial, err.get("message", raw[:300])
        except Exception:
            return None, partial, raw[:300]
    return final, partial, None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("input"); p.add_argument("output")
    p.add_argument("--prompt", required=True)
    p.add_argument("--model", default="gpt-image-2")
    p.add_argument("--size", default="auto-aspect")
    p.add_argument("--quality", default="high", choices=["low", "medium", "high"])
    p.add_argument("--retries", type=int, default=2)
    a = p.parse_args()

    model = pick_model(a.model)
    size = auto_size(a.input) if a.size == "auto-aspect" else a.size
    with tempfile.TemporaryDirectory() as wd:
        image = prepare(a.input, wd)
        stream_file = os.path.join(wd, "stream.txt")
        for attempt in range(a.retries + 1):
            t = time.time()
            code, err = run(image, a.prompt, model, size, a.quality, stream_file)
            final, partial, msg = parse(stream_file) if os.path.exists(stream_file) else (None, None, err)
            print(f"attempt {attempt + 1}: HTTP {code} in {time.time() - t:.0f}s", file=sys.stderr)
            if final:
                open(a.output, "wb").write(base64.b64decode(final))
                print(a.output)
                return
            print(f"  failed: {msg or err}", file=sys.stderr)
            if code.startswith("4"):  # bad request / moderation: retrying won't help
                break
            time.sleep(4 * (attempt + 1))
        if partial:
            out = os.path.splitext(a.output)[0] + ".partial.png"
            open(out, "wb").write(base64.b64decode(partial))
            print(f"only a partial preview was received: {out}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
