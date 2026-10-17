#!/usr/bin/env python3
"""Build the silent presentation video (pitch/media/step-wise-video.mp4) for an external voice-over.

Pieces, in story order: animated pitch slides (index.html), still deck slides (deck.html) with a slow push-in,
title cards and the recorded app flow (pitch/media/prototipo.mp4, see record_demo.py), joined with crossfades.
A thin chapter bar under the picture tells the viewer which act they are in.
The timeline below is the single source of truth: pitch/media/voiceover.md is timed against it.
    uv run --with playwright python scripts/build_video.py
"""
import argparse
import base64
import json
import subprocess
import tempfile
import time
from pathlib import Path

from playwright.sync_api import Page, ViewportSize, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
PITCH = ROOT / "pitch"
SIZE: ViewportSize = {"width": 1920, "height": 1080}
FPS = 30
FADE = 0.6

# (kind, what, seconds). "pitch" entries in a row play as one continuous take, advanced like a live talk.
TIMELINE = [
    ("pitch", "cover", 12), ("anim", "what", 8),
    ("pitch", "legge", 12), ("deck", "gap", 10), ("deck", "vuoto", 12),
    ("pitch", "p-foto", 10), ("pitch", "p-jev", 11), ("pitch", "p-3d", 13), ("pitch", "p-gemma", 10),
    ("card", "prototype", 3),
    ("clip", "prototipo.mp4", 1.6),
    ("deck", "amd", 12), ("deck", "who", 8),
    ("pitch", "close", 8),
    ("card", "end", 4),
]
CHAPTERS = ["The problem", "How it works", "The real app", "Why AMD"]
CHAPTER_OF = {"legge": 0, "gap": 0, "vuoto": 0, "p-foto": 1, "p-jev": 1, "p-3d": 1, "p-gemma": 1, "prototype": 2, "prototipo": 2, "amd": 3, "who": 3}
BAND = 36

CARDS = {
    "prototype": ('<p class="k">The real prototype</p><h1>Maria\'s case, from the room<br>to her daughter\'s phone.</h1>', "#0e0d0c"),
    "end": ('<div class="brand"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"><path d="M3.5 10.5 12 4l8.5 6.5V20h-17z"/><circle cx="12" cy="14" r="2.2" fill="currentColor" stroke="none"/></svg>Step Wise</div>'
            '<h1 style="font-size:64px;margin-top:36px">Home fall-hazard assessment from phone photos,<br>reviewed by a professional before discharge.</h1>'
            '<p class="m">AMD Developer Hackathon · ACT III · Track 2 Health &amp; Wellbeing<br>github.com/jacopopper/amd-hack · @jacopopper · @davideblesse</p>', "#0e0d0c"),
}
# Animated: the four steps arrive one by one, each with a real image from the project.
WHAT_HTML = """<!doctype html><html><head><meta charset="utf-8"><style>
@font-face{font-family:"Instrument Serif";src:url(assets/fonts/InstrumentSerif-normal.woff2) format("woff2")}
@font-face{font-family:"Inter";src:url(assets/fonts/Inter-normal.woff2) format("woff2");font-weight:100 900}
html,body{margin:0;width:1920px;height:1080px;background:#0e0d0c;color:#efeae0;font-family:Inter,sans-serif;overflow:hidden}
#what{position:relative;height:1080px;padding:120px 120px 0;box-sizing:border-box}
.brand{display:flex;align-items:center;gap:16px;font:600 34px/1 Inter;opacity:0;animation:up .7s .1s forwards}
.brand svg{width:44px;height:44px}
h1{font:400 92px/1 "Instrument Serif",serif;margin:30px 0 0;letter-spacing:-.012em;opacity:0;animation:up .8s .35s forwards}
h1 em{font-style:normal;background:#ffe14a;color:#141210;padding:0 .08em}
.steps{position:relative;display:grid;grid-template-columns:repeat(4,1fr);gap:28px;margin-top:70px}
.line{position:absolute;left:0;top:206px;height:3px;width:0;background:linear-gradient(90deg,#ffe14a,#d2321f);animation:grow 3.6s 1.2s linear forwards;z-index:0}
.st{position:relative;z-index:1;opacity:0;transform:translateY(40px) scale(.96);animation:in .7s cubic-bezier(.2,.8,.2,1) forwards}
.st:nth-child(2){animation-delay:1.2s}.st:nth-child(3){animation-delay:2.1s}.st:nth-child(4){animation-delay:3s}.st:nth-child(5){animation-delay:3.9s}
.im{height:412px;border-radius:14px;background:#000 50%/cover;box-shadow:0 0 0 1px #2e2b27,0 40px 70px -30px #000}
.n{display:flex;align-items:center;gap:14px;margin-top:26px;font:500 18px/1 ui-monospace,Menlo,monospace;color:#ffe14a;letter-spacing:.08em}
.n i{flex:1;height:1px;background:#2e2b27}
.st b{display:block;margin-top:14px;font:600 32px/1.15 Inter;color:#fff}
.st span{display:block;margin-top:10px;font-size:21px;line-height:1.4;color:#a19b90}
.st.hu .im{box-shadow:0 0 0 2px #d2321f,0 40px 70px -30px #000}
@keyframes up{from{opacity:0;transform:translateY(24px)}to{opacity:1;transform:none}}
@keyframes in{to{opacity:1;transform:none}}
@keyframes grow{to{width:100%}}
</style></head><body><section id="what">
<div class="brand"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"><path d="M3.5 10.5 12 4l8.5 6.5V20h-17z"/><circle cx="12" cy="14" r="2.2" fill="currentColor" stroke="none"/></svg>Step Wise</div>
<h1>A home safety check, <em>before Maria leaves hospital.</em></h1>
<div class="steps"><div class="line"></div>
<div class="st"><div class="im" style="background-image:url(assets/photos/casa/collega.jpg);background-position:58% 45%"></div><div class="n">01<i></i></div><b>Phone photos</b><span>A relative photographs the home, room by room.</span></div>
<div class="st"><div class="im" style="background-image:url(assets/deck/app-room.png);background-size:auto 180%;background-position:58% 64%"></div><div class="n">02<i></i></div><b>AI finds the hazards</b><span>Each one with its photo, in a 3D room.</span></div>
<div class="st hu"><div class="im" style="background-image:url(assets/deck/app-review.png);background-position:100% 0"></div><div class="n">03<i></i></div><b>A nurse approves</b><span>Confirm, edit, exclude or check in person.</span></div>
<div class="st hu"><div class="im" style="background-image:url(assets/deck/app-release.png);background-position:16% 10%"></div><div class="n">04<i></i></div><b>The family gets a plan</b><span>Clear actions, before she comes home.</span></div>
</div></section></body></html>"""

CARD_HTML = """<!doctype html><html><head><meta charset="utf-8"><style>
@font-face{{font-family:"Instrument Serif";src:url(assets/fonts/InstrumentSerif-normal.woff2) format("woff2")}}
@font-face{{font-family:"Inter";src:url(assets/fonts/Inter-normal.woff2) format("woff2");font-weight:100 900}}
html,body{{margin:0;width:1920px;height:1080px;background:{bg};color:#efeae0;font-family:Inter,sans-serif}}
body{{display:flex;flex-direction:column;justify-content:center;padding:0 160px;box-sizing:border-box}}
.k{{font:600 20px/1 Inter;letter-spacing:.14em;text-transform:uppercase;color:#ffe14a;display:flex;gap:14px;align-items:center;margin:0 0 34px}}
.k:before{{content:"";width:40px;height:2px;background:currentColor}}
h1{{font:400 104px/1.02 "Instrument Serif",serif;margin:0;letter-spacing:-.012em}}
.brand{{display:flex;align-items:center;gap:18px;font:600 44px/1 Inter}} .brand svg{{width:56px;height:56px}}
.m{{margin:56px 0 0;font-size:24px;line-height:1.6;color:#a19b90}}
.flow{{display:flex;align-items:center;gap:22px;margin-top:64px;font:500 28px/1.2 Inter;color:#d4cec2}}
.flow span{{border:1px solid #2e2b27;background:#1a1917;border-radius:10px;padding:18px 22px}} .flow i{{font-style:normal;color:#ffe14a}}
.band{{position:fixed;left:0;right:0;bottom:0;height:{band}px;background:#0e0d0c;display:flex;align-items:center;justify-content:center;gap:56px;font:600 14px/1 Inter;letter-spacing:.14em;text-transform:uppercase;color:#5f5a52}}
.band b{{font-weight:600}} .band b.on{{color:#ffe14a}} .band .sw{{position:absolute;left:32px;letter-spacing:0;text-transform:none;font-size:15px;color:#8f887c}}
</style></head><body>{body}</body></html>"""


def run(cmd):
    subprocess.run(cmd, check=True)


def duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)], check=True, capture_output=True, text=True).stdout
    return float(json.loads(out)["format"]["duration"])


def encode(args, output):
    run(["ffmpeg", "-y", "-loglevel", "error", *args, "-an", "-c:v", "libx264", "-preset", "slow", "-crf", "17", "-pix_fmt", "yuv420p", "-r", str(FPS), str(output)])


def still(png, seconds, output):
    """A still with a slow, steady push-in (upscaled first so zoompan does not jitter)."""
    frames = round(seconds * FPS)
    zoom = f"zoompan=z='1+0.035*on/{frames}':d={frames}:s=1920x1080:fps={FPS}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
    encode(["-loop", "1", "-i", str(png), "-vf", f"scale=3840:2160:flags=lanczos,{zoom}", "-frames:v", str(frames)], output)


def capture(page: Page, ids, seconds, frames_dir: Path, output):
    """Screencast one continuous take of consecutive pitch slides, advanced with the arrow key like a live talk."""
    page.bring_to_front()
    session = page.context.new_cdp_session(page)
    shots = []

    def on_frame(event):
        path = frames_dir / f"{len(shots):05d}.jpg"
        path.write_bytes(base64.b64decode(event["data"]))
        shots.append((path, event["metadata"]["timestamp"]))
        session.send("Page.screencastFrameAck", {"sessionId": event["sessionId"]})

    session.on("Page.screencastFrame", on_frame)
    page.evaluate("id => document.getElementById(id).scrollIntoView({behavior: 'instant'})", ids[0])
    session.send("Page.startScreencast", {"format": "jpeg", "quality": 95, "maxWidth": 1920, "maxHeight": 1080, "everyNthFrame": 1})
    start = time.time()
    for i, wait in enumerate(seconds):
        page.wait_for_timeout(wait * 1000)
        if i < len(seconds) - 1:
            page.keyboard.press("ArrowDown")
    end = time.time()
    session.send("Page.stopScreencast")
    session.detach()

    # Frames arrive only when the screen changes: lay them on a constant 30 fps grid, holding each until the next.
    sequence = frames_dir / "cfr"
    sequence.mkdir()
    times = [t for _, t in shots]
    current = 0
    for n in range(round((end - start) * FPS)):
        at = start + n / FPS
        while current + 1 < len(shots) and times[current + 1] <= at:
            current += 1
        (sequence / f"{n:05d}.jpg").symlink_to(shots[current][0])
    encode(["-framerate", str(FPS), "-i", str(sequence / "%05d.jpg")], output)


def xfade(pieces, bands, output):
    """Crossfade the pieces, shrink the picture a little and put the chapter bar in the strip left below it."""
    inputs, graph, last, offset = [], [], "[0:v]", 0.0
    for i, piece in enumerate(pieces):
        inputs += ["-i", str(piece)]
        if i == 0:
            offset = duration(piece) - FADE
            continue
        label = f"[v{i}]"
        graph.append(f"{last}[{i}:v]xfade=transition=fade:duration={FADE}:offset={offset:.3f}{label}")
        last, offset = label, offset + duration(piece) - FADE
    width = round(1920 * (1080 - BAND) / 1080 / 2) * 2
    graph.append(f"{last}scale={width}:{1080 - BAND},pad=1920:1080:{(1920 - width) // 2}:0:color=0x0e0d0c[b]")
    last = "[b]"
    for k, (start, end, png) in enumerate(bands):
        inputs += ["-i", str(png)]
        graph.append(f"{last}[{len(pieces) + k}:v]overlay=0:0:enable='between(t,{start:.3f},{end:.3f})'[o{k}]")
        last = f"[o{k}]"
    encode([*inputs, "-filter_complex", ";".join(graph), "-map", last], output)


def build(output):
    with tempfile.TemporaryDirectory() as tmp_name, sync_playwright() as playwright:
        tmp = Path(tmp_name)
        browser = playwright.chromium.launch(headless=True, channel="chrome", args=["--hide-scrollbars", "--force-device-scale-factor=1"])
        context = browser.new_context(viewport=SIZE, device_scale_factor=1)
        pitch, deck, card = context.new_page(), context.new_page(), context.new_page()
        pitch.goto((PITCH / "index.html").as_uri())
        pitch.add_style_tag(content="#bar,#dots,.help{display:none!important}")
        deck.goto((PITCH / "deck.html").as_uri())
        deck.wait_for_load_state("load")
        pitch.wait_for_load_state("load")
        pitch.bring_to_front()

        pieces, timeline, i = [], [], 0
        while i < len(TIMELINE):
            kind, what, seconds = TIMELINE[i]
            piece = tmp / f"{len(pieces):02d}-{kind}-{Path(what).stem}.mp4"
            if kind == "anim":
                (PITCH / ".card.html").write_text(WHAT_HTML)
                try:
                    card.goto((PITCH / ".card.html").as_uri())
                    card.wait_for_load_state("load")
                    card.evaluate("document.getAnimations().forEach(a => { a.cancel(); a.play(); })")
                    frames = tmp / piece.stem
                    frames.mkdir()
                    capture(card, [what], [seconds], frames, piece)
                finally:
                    (PITCH / ".card.html").unlink()
                    pitch.bring_to_front()
                timeline.append((len(pieces), what, 0.0, seconds))
                i += 1
            elif kind == "pitch":
                run_ids, run_secs = [], []
                while i < len(TIMELINE) and TIMELINE[i][0] == "pitch":
                    run_ids.append(TIMELINE[i][1])
                    run_secs.append(TIMELINE[i][2])
                    i += 1
                frames = tmp / piece.stem
                frames.mkdir()
                capture(pitch, run_ids, run_secs, frames, piece)
                local = 0.0
                for name, secs in zip(run_ids, run_secs):
                    timeline.append((len(pieces), name, local, secs))
                    local += secs
            else:
                if kind == "deck":
                    png = tmp / f"{what}.png"
                    deck.locator(f"section#{what}").screenshot(path=str(png))
                    still(png, seconds, piece)
                elif kind == "card":
                    body, bg = CARDS[what]
                    (PITCH / ".card.html").write_text(CARD_HTML.format(body=body, bg=bg, band=BAND))
                    try:
                        card.goto((PITCH / ".card.html").as_uri())
                        card.wait_for_timeout(300)
                        png = tmp / f"card-{what}.png"
                        card.screenshot(path=str(png))
                    finally:
                        (PITCH / ".card.html").unlink()
                    still(png, seconds, piece)
                else:
                    encode(["-i", str(PITCH / "media" / what), "-vf", f"setpts=PTS/{seconds}"], piece)
                    seconds = duration(piece)
                timeline.append((len(pieces), Path(what).stem, 0.0, seconds))
                i += 1
            pieces.append(piece)
            print(f"built {piece.name}", flush=True)
        starts = [0.0]
        for piece in pieces[:-1]:
            starts.append(starts[-1] + duration(piece) - FADE)

        # One bar image per run of slides in the same chapter (outside the chapters: all labels dimmed).
        bands, total = [], starts[-1] + duration(pieces[-1])
        for index, name, local, _ in timeline:
            chapter, at = CHAPTER_OF.get(name), starts[index] + local
            if bands and bands[-1][2] == chapter:
                continue
            if bands:
                bands[-1][1] = at
            bands.append([at, total, chapter])
        for band in bands:
            labels = "".join(f'<b class="{"on" if i == band[2] else ""}">{i + 1:02d} · {label}</b>' for i, label in enumerate(CHAPTERS))
            (PITCH / ".card.html").write_text(CARD_HTML.format(body=f'<div class="band"><span class="sw">Step Wise</span>{labels}</div>', bg="transparent", band=BAND))
            try:
                card.goto((PITCH / ".card.html").as_uri())
                card.wait_for_timeout(200)
                band[2] = tmp / f"band-{band[0]:.0f}.png"
                card.screenshot(path=str(band[2]), omit_background=True)
            finally:
                (PITCH / ".card.html").unlink()
        browser.close()
        output.parent.mkdir(parents=True, exist_ok=True)
        xfade(pieces, [tuple(band) for band in bands], output)

    for index, name, local, seconds in timeline:
        minutes, secs = divmod(round(starts[index] + local, 1), 60)
        print(f"{int(minutes)}:{secs:04.1f}  {name}  ({seconds:.1f} s)")
    print(f"total {duration(output):.1f} s -> {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=PITCH / "media/step-wise-video.mp4")
    build(parser.parse_args().output)
