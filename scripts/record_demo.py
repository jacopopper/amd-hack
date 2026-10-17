#!/usr/bin/env python3
"""Record the judge-demo review flow as pitch/media/prototipo.mp4 (1920x1080, no audio).

Needs a running demo server (STEP_WISE_DEMO=true) and playwright:
    uv run --with playwright python scripts/record_demo.py --base-url http://127.0.0.1:8765
Every run opens a fresh browser context, so the server hands out a new private copy of the case.
"""
import argparse
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SIZE = {"width": 1920, "height": 1080}


def drag(page, x, y, dx, steps=90):
    """Slow orbit of the 3D room: a horizontal drag inside the viewer."""
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + dx, y, steps=steps)
    page.mouse.up()


def record(base_url, output, browser_channel):
    with tempfile.TemporaryDirectory() as tmp, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, channel=browser_channel, args=["--window-size=1920,1080", "--hide-scrollbars"])
        context = browser.new_context(viewport=SIZE, device_scale_factor=1, record_video_dir=tmp, record_video_size=SIZE)
        page = context.new_page()

        page.goto(base_url + "/")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(2500)
        page.get_by_role("link", name=re.compile("Maria")).first.click()

        # The splat room streams in; give it time, then a slow look around.
        page.frame_locator("iframe").first.get_by_role("button", name=re.compile("Carpet|Rug|Tappeto")).first.wait_for(timeout=60000)
        page.wait_for_timeout(4000)
        drag(page, 760, 620, 260)
        page.wait_for_timeout(1200)
        drag(page, 1020, 620, -260)
        page.wait_for_timeout(1500)

        page.get_by_role("button", name=re.compile("Start review|Avvia revisione")).click()
        page.wait_for_timeout(5500)
        page.get_by_role("button", name=re.compile(r"^(Confirm|Confermo)$")).click()
        page.wait_for_timeout(5500)
        page.get_by_role("button", name=re.compile(r"^(Confirm|Confermo)$")).click()
        page.wait_for_timeout(4500)

        page.get_by_role("button", name=re.compile(r"^(Prepare report|Prepara il report)$")).last.click()
        release = page.get_by_role("button", name=re.compile("Review and release|Rivedi e rilascia"))
        release.wait_for(timeout=60000)
        page.wait_for_timeout(1500)
        release.click()

        report = page.frame_locator("iframe").last
        report.get_by_role("heading", name=re.compile("Home safety plan|Piano", re.I)).wait_for(timeout=30000)
        page.wait_for_timeout(2500)
        page.mouse.move(500, 600)
        for _ in range(8):
            page.mouse.wheel(0, 160)
            page.wait_for_timeout(450)
        page.wait_for_timeout(1500)

        page.get_by_role("checkbox").check()
        page.wait_for_timeout(1200)
        page.get_by_role("button", name=re.compile(r"^(Release|Rilascia)$")).click()
        family = page.get_by_role("link", name=re.compile("family link|link per la famiglia", re.I))
        family.wait_for(timeout=30000)
        page.wait_for_timeout(3500)
        url = family.get_attribute("href")

        # What Maria's daughter opens on her phone.
        page.goto(url if url.startswith("http") else base_url + url)
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(2500)
        for _ in range(10):
            page.mouse.wheel(0, 180)
            page.wait_for_timeout(450)
        page.wait_for_timeout(2000)

        video = page.video.path()
        context.close()
        browser.close()
        output.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", video, "-an", "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-pix_fmt", "yuv420p", "-r", "30", "-movflags", "+faststart", str(output)], check=True)
    print(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path, default=ROOT / "pitch/media/prototipo.mp4")
    parser.add_argument("--channel", default="chrome" if shutil.which("google-chrome") else None, help="Playwright browser channel (default: system Chrome)")
    args = parser.parse_args()
    record(args.base_url.rstrip("/"), args.output, args.channel)
