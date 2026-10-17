#!/usr/bin/env python3
"""Small reproducible Jev image smoke test. Requires requests and Pillow."""
import argparse
import base64
import hashlib
import html
import json
import math
import os
from pathlib import Path
import statistics
import time
import uuid
from datetime import datetime, timezone

import requests
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent
ENDPOINT = "https://api.typesafe.ai/v1/systemone"


def api_key():
    key = os.environ.get("JEV_API_KEY")
    if not key:
        # Read one literal dotenv value; never execute or interpolate its contents.
        for line in (ROOT.parents[1] / ".env").read_text().splitlines():
            name, sep, value = line.strip().removeprefix("export ").partition("=")
            if sep and name.strip() == "JEV_API_KEY":
                key = value.strip().strip("\"'")
    if not key:
        raise SystemExit("JEV_API_KEY is missing")
    return key


def valid_box(b):
    return (len(b) == 4 and all(math.isfinite(v) and 0 <= v <= 1 for v in b)
            and b[0] < b[2] and b[1] < b[3])


def iou(a, b):
    if not valid_box(a) or not valid_box(b):
        return 0.0
    overlap = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    area = lambda r: (r[2] - r[0]) * (r[3] - r[1])
    return overlap / (area(a) + area(b) - overlap)


def run(manifest, output, repeats, only):
    key = api_key()
    headers = {"Authorization": f"Bearer {key}", "TypeSafe-Danger-Zone-Images": "true"}
    snapshot = output / "manifest.json"
    if snapshot.exists() and json.loads(snapshot.read_text()) != manifest:
        raise SystemExit("Output contains a different manifest; use a new output directory")
    snapshot.write_text(json.dumps(manifest, indent=2) + "\n")
    for repeat in range(1, repeats + 1):
        for case in manifest["cases"]:
            if only and case["id"] not in only:
                continue
            result_path = output / f"{case['id']}-{repeat}.json"
            if result_path.exists():
                continue
            data = (ROOT / case["file"]).read_bytes()
            if hashlib.sha256(data).hexdigest() != case["sha256"]:
                raise SystemExit(f"Image changed: {case['id']}")
            with Image.open(ROOT / case["file"]) as im:
                if im.format not in ("JPEG", "PNG") or not all(280 <= d <= 1568 for d in im.size):
                    raise SystemExit(f"Image violates API limits: {case['id']}")
            questions = dict(manifest["questions"])
            for box in case["boxes"]:
                questions[box["id"] + "_present"] = {"type": "noul", "instructions": "Is " + box["target"] + " visibly present?"}
                questions[box["id"] + "_box"] = {"type": "box", "instructions": "The smallest axis-aligned box tightly enclosing only " + box["target"] + ". Follow the object's full visible extent, crop the box where something hides it or where it runs past the image border, and never include adjacent objects, fixtures, or surfaces the target touches or hangs from. For thin or elongated objects keep the box narrow, hugging the object along its entire visible length."}
            reference = str(uuid.uuid4())
            payload = {"model": manifest["model"], "state": {"photo": reference},
                       "assets": {reference: {"type": "image", "format": "jpg", "data_base64": base64.b64encode(data).decode()}},
                       "questions": questions}
            # No automatic POST retries: ambiguous timeouts could duplicate billed work.
            start = time.perf_counter()
            try:
                response = requests.post(ENDPOINT, headers=headers, json=payload, timeout=(10, 90), allow_redirects=False)
            except requests.RequestException as exc:
                raise SystemExit(f"{case['id']}: transport failed ({type(exc).__name__}); no automatic retry") from None
            elapsed = time.perf_counter() - start
            if response.status_code != 200:
                safe_error = response.text.replace(key, "[REDACTED]")[:1000]
                raise SystemExit(f"{case['id']}: HTTP {response.status_code}: {safe_error}")
            body = response.json()
            for name, q in questions.items():
                answer = body.get("answers", {}).get(name, {})
                if answer.get("type") != q["type"]:
                    raise SystemExit(f"Invalid response shape for {name}")
            record = {"case_id": case["id"], "repeat": repeat, "at_utc": datetime.now(timezone.utc).isoformat(),
                      "elapsed_seconds": elapsed, "image_sha256": case["sha256"],
                      "questions": questions, "response": body}
            result_path.write_text(json.dumps(record, indent=2).replace(key, "[REDACTED]") + "\n")
            print(f"{case['id']} repeat {repeat}: {elapsed:.2f}s; {body.get('usage')}", flush=True)


def summarize(manifest, output):
    records = [json.loads(p.read_text()) for p in sorted(output.glob("*.json")) if p.name not in ("manifest.json", "summary.json")]
    if not records:
        raise SystemExit("No results to summarize")
    rows, binary, choices, boxes, absent, targeted = [], [], [], [], [], []
    for r in records:
        case = next(c for c in manifest["cases"] if c["id"] == r["case_id"])
        answers = r["response"]["answers"]
        entries = []
        for name, expected in case["expected"].items():
            a = answers[name]
            actual = a["noul"] >= manifest["binary_threshold"] if a["type"] == "noul" else a["choice"]
            row = {"case": case["id"], "repeat": r["repeat"], "question": name, "expected": expected, "actual": actual, "value": a.get("noul", a.get("confidence")), "correct": None if expected is None else actual == expected}
            (binary if a["type"] == "noul" else choices).append(row)
            entries.append(f"<tr><td>{name}</td><td>{expected}</td><td>{actual}</td><td>{row['value']:.3f}</td><td>{'unscored' if expected is None else 'OK' if row['correct'] else 'MISS'}</td></tr>")
        im = Image.open(ROOT / case["file"]).convert("RGB")
        draw = ImageDraw.Draw(im)
        for target in case["boxes"]:
            a = answers[target["id"] + "_box"]
            b = [a["box"][k] for k in ("left", "top", "right", "bottom")]
            presence = answers[target["id"] + "_present"]["noul"]
            expected = target["expected"]
            row = {"case": case["id"], "repeat": r["repeat"], "target": target["id"], "box": b, "confidence": a.get("confidence"), "presence": presence, "valid": valid_box(b), "iou": iou(b, expected) if expected else None, "gated_in": presence >= manifest["binary_threshold"]}
            (boxes if expected else absent).append(row)
            targeted.append({"expected": expected is not None, "actual": row["gated_in"]})
            for coords, color in [(expected, "#00ff88"), (b, "#ff3980")]:
                if coords and valid_box(coords):
                    pixels = [coords[0] * im.width, coords[1] * im.height, coords[2] * im.width, coords[3] * im.height]
                    draw.rectangle(pixels, outline=color, width=3)
                    if color == "#ff3980":
                        x, y = pixels[:2]
                        draw.text((x + 3, y + 3), target["id"], fill="white", stroke_width=2, stroke_fill="black")
            entries.append(f"<tr><td>BOX {target['id']}</td><td>{'present' if expected else 'absent'}</td><td>presence={presence:.3f}</td><td>IoU={row['iou'] if expected else 'n/a'}</td><td>box confidence={row['confidence']}</td></tr>")
        filename = f"{case['id']}-{r['repeat']}.jpg"
        im.save(output / filename, quality=90)
        rows.append(f"<article><h2>{case['id']} · run {r['repeat']} · {r['elapsed_seconds']:.2f}s</h2><p>{html.escape(case['annotation_notes'])}</p><img src='{filename}'><table><tr><th>Question</th><th>Label</th><th>Prediction</th><th>Value</th><th>Result</th></tr>{''.join(entries)}</table></article>")
    first = [r for r in binary if r["repeat"] == 1]
    comparable = [r for r in choices if r["expected"] is not None and r["repeat"] == 1]
    summary = {"requests": len(records), "model_versions": sorted({r['response']['model'] for r in records}),
               "first_run_binary": {"correct": sum(r["correct"] for r in first), "total": len(first), "tp": sum(r["actual"] and r["expected"] for r in first), "fp": sum(r["actual"] and not r["expected"] for r in first), "fn": sum(not r["actual"] and r["expected"] for r in first)},
               "first_run_choice": {"correct": sum(r["correct"] for r in comparable), "total": len(comparable)},
               "first_run_boxes_iou_ge_05": sum(r["iou"] >= .5 for r in boxes if r["repeat"] == 1),
               "first_run_box_count": sum(r["repeat"] == 1 for r in boxes),
               "median_latency_seconds": statistics.median(r["elapsed_seconds"] for r in records),
               "min_latency_seconds": min(r["elapsed_seconds"] for r in records), "max_latency_seconds": max(r["elapsed_seconds"] for r in records),
               "input_tokens": sum(r["response"]["usage"]["input_tokens"] for r in records),
               "binary_results": binary, "choice_results": choices, "box_results": boxes, "absent_boxes": absent,
               "target_presence_correct": sum(r["expected"] == r["actual"] for r in targeted), "target_presence_total": len(targeted)}
    summary["estimated_usd_at_published_rate"] = summary["input_tokens"] * .042 / 1_000_000
    summary["changed_shared_decisions_on_repeat"] = [r for r in binary + choices if r["repeat"] > 1 and any(s["case"] == r["case"] and s["question"] == r["question"] and s["repeat"] == 1 and s["actual"] != r["actual"] for s in binary + choices)]
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    sources = json.loads((ROOT / "sources.json").read_text())
    credits = ''.join(f"<li>{s['id']}: <a href='{html.escape(s['source_url'])}'>{html.escape(s['title'])}</a> — {html.escape(s['author'])}, {html.escape(s['license'])}. {html.escape(s['changes'])} <a href='{html.escape(s.get('license_url') or s['source_url'])}'>License</a>. Annotated derivatives retain the source license.</li>" for s in sources)
    page = "<!doctype html><meta charset='utf-8'><title>Jev image evaluation</title><style>body{font:16px system-ui;background:#111827;color:#eee;max-width:1100px;margin:40px auto;padding:0 20px}article{margin:36px 0;padding:22px;background:#1f2937;border-radius:14px}img{max-width:100%;max-height:720px}table{width:100%;border-collapse:collapse;font-size:14px}th,td{text-align:left;padding:8px;border-bottom:1px solid #555}a{color:#8bcfff}li{margin:12px 0}</style><h1>Jev 1.14 image smoke test</h1><p>Six public source photos + one crop; labels frozen before requests. Green = approximate reference box. Pink = Jev box, including absent targets shown for debugging. A box is not evidence of presence. No patient data.</p><pre>" + html.escape(json.dumps({k:v for k,v in summary.items() if not isinstance(v,list)},indent=2)) + "</pre>" + ''.join(rows) + "<h2>Image credits</h2><ul>" + credits + "</ul>"
    (output / "gallery.html").write_text(page)
    print(json.dumps({k:v for k,v in summary.items() if not isinstance(v,list)}, indent=2))


def self_check():
    assert iou([0, 0, 1, 1], [0, 0, 1, 1]) == 1
    assert iou([0, 0, .5, .5], [.5, .5, 1, 1]) == 0
    assert math.isclose(iou([0, 0, 1, 1], [0, 0, .5, 1]), .5)
    assert not valid_box([.8, 0, .2, 1])
    assert not valid_box([0, 0, float('nan'), 1])
    manifest = json.loads((ROOT / 'manifest.json').read_text())
    for case in manifest['cases']:
        assert hashlib.sha256((ROOT / case['file']).read_bytes()).hexdigest() == case['sha256']
        assert all(b['expected'] is None or valid_box(b['expected']) for b in case['boxes'])
    print('IoU, invalid-box, label-coordinate, and image-integrity checks passed.')


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "summarize", "self-check"))
    parser.add_argument("--output", type=Path, default=ROOT / "results-2026-10-16")
    parser.add_argument("--repeats", type=int, choices=(1, 2, 3), default=2)
    parser.add_argument("--only", nargs="+")
    args = parser.parse_args()
    if args.action == "self-check":
        self_check()
    else:
        args.output.mkdir(parents=True, exist_ok=True)
        manifest_path = args.output / "manifest.json" if args.action == "summarize" else ROOT / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        if args.action == "run":
            run(manifest, args.output, args.repeats, args.only)
        summarize(manifest, args.output)
