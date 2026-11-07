#!/usr/bin/env python3
"""OpenAI Images API helper: text-to-image (generate) and image-to-image (edit).

Usage:
  openai_image.py generate "prompt" [options]
  openai_image.py edit "prompt" --image in.png [--image ref2.png] [--mask mask.png] [options]

API key: read from OPENAI_API_KEY. In a Claude Code cloud session the key is
injected by the network proxy, so when the variable is unset a placeholder is
sent and the proxy replaces it.
"""
import argparse
import base64
import datetime as dt
import json
import mimetypes
import os
import re
import sys
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("This script needs the 'requests' package: pip install requests")

API_BASE = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
DEFAULT_MODEL = os.environ.get("OPENAI_IMAGE_MODEL", "gpt-image-2")
DEFAULT_OUT = os.environ.get("OPENAI_IMAGE_OUT", "generated-images")
TIMEOUT = 600


def api_key():
    return os.environ.get("OPENAI_API_KEY") or "proxy-injected"


def common_fields(args):
    fields = {"model": args.model, "prompt": args.prompt}
    for name in ("size", "quality", "background", "output_format", "moderation"):
        value = getattr(args, name, None)
        if value:
            fields[name] = value
    if args.n and args.n != 1:
        fields["n"] = args.n
    if args.output_compression is not None:
        fields["output_compression"] = args.output_compression
    return fields


def slug(text, limit=40):
    s = re.sub(r"[^\w一-鿿-]+", "-", text.strip()).strip("-")
    return (s[:limit] or "image").lower()


def save_images(data, args, mode):
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    ext = (args.output_format or "png").replace("jpeg", "jpg")
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    base = args.name or f"{stamp}-{mode}-{slug(args.prompt)}"
    saved = []
    for i, item in enumerate(data.get("data", []), start=1):
        suffix = f"-{i}" if len(data["data"]) > 1 else ""
        path = out_dir / f"{base}{suffix}.{ext}"
        if item.get("b64_json"):
            path.write_bytes(base64.b64decode(item["b64_json"]))
        elif item.get("url"):
            r = requests.get(item["url"], timeout=TIMEOUT)
            r.raise_for_status()
            path.write_bytes(r.content)
        else:
            continue
        saved.append(str(path.resolve()))
        if item.get("revised_prompt"):
            print(f"revised_prompt[{i}]: {item['revised_prompt']}", file=sys.stderr)
    meta = {
        "mode": mode,
        "model": args.model,
        "prompt": args.prompt,
        "files": saved,
        "usage": data.get("usage"),
    }
    (out_dir / f"{base}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    return saved


def check(resp):
    if resp.ok:
        return resp.json()
    try:
        err = resp.json().get("error", {})
        msg = err.get("message") or resp.text
    except ValueError:
        msg = resp.text
    sys.exit(f"OpenAI API error {resp.status_code}: {msg}")


def cmd_generate(args):
    resp = requests.post(
        f"{API_BASE}/images/generations",
        headers={"Authorization": f"Bearer {api_key()}", "Content-Type": "application/json"},
        json=common_fields(args),
        timeout=TIMEOUT,
    )
    return save_images(check(resp), args, "gen")


def cmd_edit(args):
    fields = common_fields(args)
    if args.input_fidelity:
        fields["input_fidelity"] = args.input_fidelity
    files = []
    handles = []
    try:
        for p in args.image:
            fh = open(p, "rb")
            handles.append(fh)
            mime = mimetypes.guess_type(p)[0] or "image/png"
            files.append(("image[]", (Path(p).name, fh, mime)))
        if args.mask:
            fh = open(args.mask, "rb")
            handles.append(fh)
            files.append(("mask", (Path(args.mask).name, fh, "image/png")))
        data = {k: str(v) for k, v in fields.items()}
        resp = requests.post(
            f"{API_BASE}/images/edits",
            headers={"Authorization": f"Bearer {api_key()}"},
            data=data,
            files=files,
            timeout=TIMEOUT,
        )
    finally:
        for fh in handles:
            fh.close()
    return save_images(check(resp), args, "edit")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_common(p):
        p.add_argument("prompt", help="Text prompt")
        p.add_argument("--model", default=DEFAULT_MODEL, help=f"default: {DEFAULT_MODEL}")
        p.add_argument("--size", help="e.g. 1024x1024, 1536x1024, 1024x1536, auto")
        p.add_argument("--quality", help="low | medium | high | auto")
        p.add_argument("--n", type=int, default=1, help="number of images")
        p.add_argument("--background", help="transparent | opaque | auto")
        p.add_argument("--output-format", dest="output_format", help="png | jpeg | webp")
        p.add_argument("--output-compression", dest="output_compression", type=int, help="0-100 for jpeg/webp")
        p.add_argument("--moderation", help="auto | low")
        p.add_argument("--out", default=DEFAULT_OUT, help=f"output folder (default: {DEFAULT_OUT})")
        p.add_argument("--name", help="base filename (without extension)")

    g = sub.add_parser("generate", help="text-to-image")
    add_common(g)
    g.set_defaults(func=cmd_generate)

    e = sub.add_parser("edit", help="image-to-image / edit with reference images")
    add_common(e)
    e.add_argument("--image", action="append", required=True, help="input image (repeatable)")
    e.add_argument("--mask", help="PNG mask; transparent areas are regenerated")
    e.add_argument("--input-fidelity", dest="input_fidelity", help="low | high")
    e.set_defaults(func=cmd_edit)

    args = ap.parse_args()
    for p in getattr(args, "image", None) or []:
        if not Path(p).is_file():
            sys.exit(f"Input image not found: {p}")
    for path in args.func(args):
        print(path)


if __name__ == "__main__":
    main()
