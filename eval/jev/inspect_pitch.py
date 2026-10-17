#!/usr/bin/env python3
"""Run the current Jev adapter on the two pitch photos, or render saved responses offline."""
import argparse
import html
import io
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from PIL import ImageDraw, ImageFont

from step_wise import db
from step_wise.config import ROOT, Settings
from step_wise.pipeline import jev
from step_wise.pipeline.http import ExternalFailure, post_json
from step_wise.pipeline.photos import decode, prepared_image

LABELS = {"rug_near": "Tappeto vicino", "rug_far": "Tappeto distante", "footstool": "Poggiapiedi",
          "floor_cable": "Cavo a terra", "bathtub": "Vasca", "grab_bar": "Maniglione"}
COLORS = ("#fa759e", "#55d9e8", "#ffb45e", "#bcabff", "#a4de72", "#f4da63")


def render(output):
    cards = []
    credits = json.loads((ROOT / "pitch/assets/photos/credits.json").read_text())
    for name, title in (("corridoio", "Corridoio"), ("bagno", "Bagno")):
        path = output / f"{name}.json"
        if not path.exists():
            continue
        record = json.loads(path.read_text())
        if record["status"] != "succeeded":
            cards.append(f"<article><h2>{title}</h2><p>{html.escape(record['error'])}</p></article>")
            continue
        source = (ROOT / record["source"]).read_bytes()
        if db.digest(source) != record["source_sha256"]:
            raise SystemExit(f"Source photo changed: {name}")
        image, _ = decode(source)
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype("DejaVuSans.ttf", max(17, round(image.width / 52)))
        rows, count = [], 0
        for index, candidate in enumerate(record["candidates"]):
            label = LABELS.get(candidate["question_id"], "Candidato generico")
            answer = candidate["provider_response"]
            evidence = candidate.get("evidence")
            confidence = answer["box"].get("confidence")
            if evidence:
                count += 1
                color = COLORS[index % len(COLORS)]
                box = evidence["box"]
                draw.rectangle(box, outline=color, width=max(3, round(image.width / 250)))
                text = f"{count}. {label}"
                bounds = draw.textbbox((0, 0), text, font=font)
                left = min(box[0], max(0, image.width - bounds[2] - 16))
                top = max(0, box[1] - bounds[3] - 12)
                draw.rectangle((left, top, left + bounds[2] + 16, top + bounds[3] + 12), fill=color)
                draw.text((left + 8, top + 3), text, fill="#101214", font=font)
            state = {"present": "presente", "absent": "assente nell'immagine", "unknown": "incerto"}[candidate["presence"]]
            rows.append(f"<tr><td>{html.escape(label)}</td><td>{state}</td><td>{answer['presence']['noul']:.2f}</td><td>{confidence if confidence is not None else 'n/a'}</td><td>{'sì' if evidence else 'no'}</td></tr>")
        image.save(output / f"{name}-jev.jpg", quality=94)
        credit = credits[name]
        latency = record["elapsed_seconds"]
        cards.append(f"<article><h2>{title} <small>{count} regioni · {latency:.3f} s</small></h2><img src='{name}-jev.jpg' alt='Oggetti localizzati da Jev: {title}'><div class='table'><table><thead><tr><th>Oggetto</th><th>Presenza</th><th>Punteggio</th><th>Confidenza box</th><th>Box mostrata</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div><p><a href='{name}.json'>Risposta API e domande complete</a> · <a href='{html.escape(credit['page'], quote=True)}'>{html.escape(credit['title'])}</a></p></article>")
    page = """<!doctype html><html lang="it"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Jev · oggetti visibili, risultati reali</title><style>body{font:16px/1.55 system-ui;background:#101214;color:#eee;max-width:1200px;margin:40px auto;padding:0 24px}h1{line-height:1.15}h2{display:flex;gap:18px;align-items:baseline;flex-wrap:wrap}small{font-size:14px;color:#bac2cc;font-weight:400}article{background:#1b1e22;padding:24px;border-radius:12px;margin:28px 0}img{display:block;max-width:100%;max-height:900px;margin:auto}a{color:#9ccaff}.note{color:#bfc3ca;max-width:940px}.table{overflow:auto}table{border-collapse:collapse;width:100%;font-size:14px;margin:24px 0}td,th{text-align:left;padding:10px;border-bottom:1px solid #3a414a;white-space:nowrap}@media(max-width:600px){body{padding:0 12px}article{padding:14px}}</style><h1>Jev · oggetti visibili, risultati reali</h1><p>Domande specifiche e indipendenti su sei tipi di oggetto. Le stesse domande sono inviate a entrambe le foto, senza profilo, nomi dei file o hotspot della presentazione.</p><p class="note">Mostriamo una box solo con presenza ≥ 0.75 e confidenza box ≥ 0.50. Sono soglie provvisorie, non calibrate. Un oggetto localizzato è un candidato da verificare, non un pericolo già confermato; un supporto non visibile non prova che manchi in tutta la stanza. Tutte le risposte, incluse quelle incerte o soppresse, restano nella tabella e nel JSON.</p>""" + "".join(cards) + "</html>"
    (output / "index.html").write_text(page)


def run(output):
    settings = Settings.from_env()
    if not settings.jev_api_key:
        raise SystemExit("JEV_API_KEY is missing")
    for name in ("corridoio", "bagno"):
        source = ROOT / f"pitch/assets/photos/{name}.jpg"
        image, _ = decode(source.read_bytes())
        prepared, transform = prepared_image(image)
        stream = io.BytesIO()
        prepared.save(stream, "JPEG", quality=92)
        image_bytes = stream.getvalue()
        payload = jev.request_payload(image_bytes, [])
        record = {"source": source.relative_to(ROOT).as_posix(), "source_sha256": db.digest(source.read_bytes()),
                  "prepared_sha256": db.digest(image_bytes), "upright_size": list(image.size), "transform": transform,
                  "endpoint": jev.ENDPOINT, "requested_model": jev.MODEL, "questions": payload["questions"],
                  "thresholds": {"presence": jev.PRESENCE_THRESHOLD, "box_confidence": jev.BOX_CONFIDENCE_THRESHOLD}}
        result_path = output / f"{name}.json"
        if result_path.exists():
            old = json.loads(result_path.read_text())
            if any(old.get(key) != value for key, value in record.items()):
                raise SystemExit(f"Saved {name} has different inputs; choose a fresh output directory")
            print(f"{name}: saved result retained; no new API call", flush=True)
            continue
        (output / f"{name}-prepared.jpg").write_bytes(image_bytes)
        record["at_utc"] = datetime.now(timezone.utc).isoformat()
        start = time.perf_counter()
        try:
            body = post_json(jev.ENDPOINT, payload, {"Authorization": "Bearer " + settings.jev_api_key,
                             "TypeSafe-Danger-Zone-Images": "true"}, timeout=(10, 90))
            record["response"] = body
            record["candidates"] = jev.interpret(body, {"id": payload["state"]["photo"]},
                                                 {"transform": transform, "upright_size": list(image.size)}, [])
            record["status"] = "succeeded"
        except (ExternalFailure, ValueError, KeyError, TypeError) as exc:
            record.update(status="failed", error=str(exc) if isinstance(exc, ExternalFailure) else "Jev response validation failed")
        record["elapsed_seconds"] = time.perf_counter() - start
        result_path.write_text(json.dumps(record, indent=2, ensure_ascii=False).replace(settings.jev_api_key, "[REDACTED]") + "\n")
        print(f"{name}: {record['status']} in {record['elapsed_seconds']:.2f}s", flush=True)
    render(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "render"))
    parser.add_argument("--output", type=Path, default=ROOT / "pitch/jev-results/2026-10-17/optimization/objects-v2")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    (run if args.action == "run" else render)(args.output)
