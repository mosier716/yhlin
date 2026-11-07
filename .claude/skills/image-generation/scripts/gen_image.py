#!/usr/bin/env python3
"""Generate or edit images with the OpenAI Images API.

Text-to-image:  gen_image.py OUT.png --prompt "..."
Image-to-image: gen_image.py OUT.png --prompt "..." --image in.jpg [--image ref2.png] [--mask mask.png]

Every request is streamed (stream=true + partial_images): the cloud session's
outbound proxy cuts connections that receive no data for ~30 s, and a
medium/high quality image takes 30-90 s, so non-streamed calls fail with
HTTP 502 "upstream request failed".

With --n > 1 the script makes n sequential streamed requests and writes
OUT-1.png, OUT-2.png, ...
"""
import argparse, base64, json, math, os, subprocess, sys, tempfile, time

API = "https://api.openai.com/v1/images"
AUTH = "Authorization: Bearer sk-dummy"  # the proxy injects the real key
PREFERRED = ["gpt-image-2", "gpt-image-1.5", "chatgpt-image-latest", "gpt-image-1-mini"]
PRESETS = {"square": "1024x1024", "portrait": "1024x1536", "landscape": "1536x1024"}
FREE_SIZE_MODELS = ("gpt-image-2",)  # accept any WxH within the constraints below
MAX_UPLOAD = 20 * 1024 * 1024


def log(*a):
    print(*a, file=sys.stderr)


def curl(args, timeout):
    return subprocess.run(["curl", "-sS", *args], capture_output=True, text=True, timeout=timeout)


def pick_model(requested):
    r = curl([f"https://api.openai.com/v1/models", "-H", AUTH], 60)
    try:
        models = {m["id"] for m in json.loads(r.stdout)["data"]}
    except Exception:
        return requested  # can't list; let the API decide
    if requested in models:
        return requested
    for m in PREFERRED:
        if m in models:
            log(f"model {requested!r} not available, using {m!r}")
            return m
    sys.exit(f"no image model available; image-like models: {sorted(x for x in models if 'image' in x)}")


def free_size(w, h, target_px=1024 * 1536):
    """WxH with the photo's aspect ratio for gpt-image-2: sides divisible by 16,
    ratio clamped to 1:3..3:1, ~target_px pixels (within 655,360..8,294,400)."""
    r = min(max(w / h, 1 / 3), 3)
    target_px = min(max(target_px, 655_360), 8_294_400)
    W = math.sqrt(target_px * r)
    H = W / r
    W, H = int(round(W / 16) * 16), int(round(H / 16) * 16)
    W, H = min(W, 3840), min(H, 3840)
    while W / H > 3:  # rounding can push the ratio just past the limit
        W -= 16
    while H / W > 3:
        H -= 16
    return f"{W}x{H}"


def preset_for(w, h):
    if abs(w - h) / max(w, h) < 0.1:
        return PRESETS["square"]
    return PRESETS["portrait"] if h > w else PRESETS["landscape"]


def resolve_size(size, model, first_image):
    if size in PRESETS:
        return PRESETS[size]
    if size != "auto":
        return size  # explicit WxH or "auto"-like value passed through
    if not first_image:
        return PRESETS["square"]
    try:
        from PIL import Image
        w, h = Image.open(first_image).size
    except Exception:
        return PRESETS["portrait"]
    return free_size(w, h) if model.startswith(FREE_SIZE_MODELS) else preset_for(w, h)


def shrink(path, workdir, i):
    if os.path.getsize(path) <= MAX_UPLOAD:
        return path
    from PIL import Image
    im = Image.open(path).convert("RGB")
    im.thumbnail((2048, 2048))
    out = os.path.join(workdir, f"upload{i}.jpg")
    im.save(out, quality=92)
    log(f"downsized {path} for upload")
    return out


def build_args(a, model, size, images, stream_file, body_file):
    common = {"model": model, "prompt": a.prompt, "size": size, "quality": a.quality,
              "stream": "true", "partial_images": str(a.partial_images)}
    for k in ("background", "output_format", "output_compression", "moderation"):
        v = getattr(a, k)
        if v is not None:
            common[k] = str(v)
    tail = ["-o", stream_file, "-w", "%{http_code}"]
    if images:  # image-to-image: multipart
        args = ["-N", "--max-time", "600", f"{API}/edits", "-H", AUTH]
        for k, v in common.items():
            args += ["-F", f"{k}={v}"]
        field = "image[]" if len(images) > 1 else "image"
        for p in images:
            args += ["-F", f"{field}=@{p}"]
        if a.mask:
            args += ["-F", f"mask=@{a.mask}"]
        return args + tail
    # text-to-image: JSON body (written to a file to avoid shell quoting issues)
    body = dict(common, stream=True, partial_images=a.partial_images)
    if "output_compression" in body:
        body["output_compression"] = int(body["output_compression"])
    json.dump(body, open(body_file, "w"))
    return ["-N", "--max-time", "600", f"{API}/generations", "-H", AUTH,
            "-H", "Content-Type: application/json", "--data-binary", f"@{body_file}"] + tail


def parse(stream_file):
    raw = open(stream_file, encoding="utf-8", errors="replace").read() if os.path.exists(stream_file) else ""
    final = partial = None
    for line in raw.splitlines():
        if not line.startswith("data:"):
            continue
        try:
            ev = json.loads(line[5:])
        except Exception:
            continue
        if ev.get("b64_json"):
            if str(ev.get("type", "")).endswith("completed"):
                final = ev["b64_json"]
            else:
                partial = ev["b64_json"]
        if ev.get("error"):
            return None, partial, ev["error"].get("message", str(ev["error"]))
    if final:
        return final, partial, None
    try:
        return None, partial, json.loads(raw).get("error", {}).get("message", raw[:300])
    except Exception:
        return None, partial, raw[:300] or "empty response"


def one(a, model, size, images, out, wd):
    stream_file, body_file = os.path.join(wd, "stream.txt"), os.path.join(wd, "body.json")
    args = build_args(a, model, size, images, stream_file, body_file)
    if a.dry_run:
        log("DRY RUN curl", " ".join(x if " " not in x else repr(x) for x in args))
        if os.path.exists(body_file):
            log("body:", open(body_file).read())
        return True
    for attempt in range(a.retries + 1):
        if os.path.exists(stream_file):
            os.remove(stream_file)
        t = time.time()
        code = curl(args, 660).stdout.strip()
        final, partial, msg = parse(stream_file)
        log(f"{os.path.basename(out)} attempt {attempt + 1}: HTTP {code} in {time.time() - t:.0f}s")
        if final:
            open(out, "wb").write(base64.b64decode(final))
            print(out)
            return True
        log(f"  failed: {msg}")
        if code.startswith("4"):  # bad parameter / moderation: retrying won't help
            break
        time.sleep(4 * (attempt + 1))
    if partial:
        p = os.path.splitext(out)[0] + ".partial.png"
        open(p, "wb").write(base64.b64decode(partial))
        log(f"  saved last partial preview only: {p}")
    return False


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("output", help="output file; extension should match --output-format")
    p.add_argument("--prompt", required=True)
    p.add_argument("--image", action="append", default=[], help="input/reference image (repeatable) -> edit mode")
    p.add_argument("--mask", help="PNG with alpha, same size as first --image; transparent = area to change")
    p.add_argument("--model", default="gpt-image-2")
    p.add_argument("--size", default="auto",
                   help="auto (match first input image, else square) | square | portrait | landscape | WxH")
    p.add_argument("--quality", default="high", choices=["low", "medium", "high", "auto"])
    p.add_argument("--n", type=int, default=1, help="number of images (sequential requests)")
    p.add_argument("--background", choices=["transparent", "opaque", "auto"])
    p.add_argument("--output-format", dest="output_format", choices=["png", "jpeg", "webp"])
    p.add_argument("--output-compression", dest="output_compression", type=int, help="0-100, jpeg/webp only")
    p.add_argument("--moderation", choices=["auto", "low"])
    p.add_argument("--partial-images", dest="partial_images", type=int, default=2)
    p.add_argument("--retries", type=int, default=2)
    p.add_argument("--dry-run", action="store_true", help="print the request without sending it")
    a = p.parse_args()

    if a.mask and not a.image:
        sys.exit("--mask needs --image")
    if a.background == "transparent" and a.output_format == "jpeg":
        sys.exit("transparent background needs png or webp")
    for f in a.image + ([a.mask] if a.mask else []):
        if not os.path.isfile(f):
            sys.exit(f"not found: {f}")

    model = a.model if a.dry_run else pick_model(a.model)
    size = resolve_size(a.size, model, a.image[0] if a.image else None)
    log(f"mode={'edit' if a.image else 'generate'} model={model} size={size} quality={a.quality} n={a.n}")

    base, ext = os.path.splitext(a.output)
    outs = [a.output] if a.n == 1 else [f"{base}-{i + 1}{ext}" for i in range(a.n)]
    ok = 0
    with tempfile.TemporaryDirectory() as wd:
        images = [shrink(f, wd, i) for i, f in enumerate(a.image)]
        for out in outs:
            ok += one(a, model, size, images, out, wd)
    if ok < len(outs):
        sys.exit(f"{len(outs) - ok} of {len(outs)} images failed")


if __name__ == "__main__":
    main()
