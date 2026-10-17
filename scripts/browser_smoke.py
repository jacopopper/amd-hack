#!/usr/bin/env python3
"""Optional isolated headless browser check of the Next.js reviewer app. Requires playwright, no external model calls."""
import argparse
import re

from playwright.sync_api import expect, sync_playwright

from step_wise.config import ROOT

NO_WEBGL = """HTMLCanvasElement.prototype.getContext = (original => function(kind, ...args) {
  return String(kind).includes('webgl') ? null : original.call(this, kind, ...args);
})(HTMLCanvasElement.prototype.getContext);"""


def run(base_url, executable=None):
    output = ROOT / "data/browser-smoke"
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"], **({"executable_path": executable} if executable else {}))
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        health = context.request.get(base_url + "/api/health").json()
        assert all(health["modes"][key] == value for key, value in {"reconstruction": "fixture", "gemma": "fixture", "jev": "disabled"}.items()), "Browser smoke requires fixture API and worker"
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)

        page.goto(base_url + "/")
        page.wait_for_load_state("networkidle")
        page.screenshot(path=str(output / "desktop-patients.png"), full_page=True)
        page.get_by_role("link", name="New patient").click()

        page.locator("input[name=label]").fill("Browser smoke · fictional profile")
        page.locator("textarea[name=description]").fill("Fictional profile: uses a walker, no clinical history provided.")
        page.locator("input[name=mobility_aid]").fill("walker")
        page.locator("input[name=profile_confirmed]").check()
        page.locator("input[name=staged_data]").check()
        page.screenshot(path=str(output / "desktop-intake.png"), full_page=True)
        page.get_by_role("button", name="Continue to photos").click()

        page.get_by_role("heading", name="Photos of the home").wait_for()
        match = re.search(r"[?&]id=([^&]+)", page.url)
        assert match, page.url
        assessment_id = match.group(1)
        page.get_by_label("Room").fill("Living room")
        photos = [str(ROOT / "data/demo/synthetic-1.png"), str(ROOT / "data/demo/synthetic-2.png")]
        page.locator("input[type=file]").set_input_files(photos)
        expect(page.get_by_text("2 overview · 0 detail", exact=False)).to_be_visible(timeout=30000)
        page.get_by_role("button", name="Start analysis").click()

        page.get_by_role("button", name="Start review").wait_for(timeout=60000)
        expect(page.get_by_text("Analysing the home")).to_be_hidden(timeout=60000)
        page.locator("canvas").wait_for(timeout=20000)
        page.screenshot(path=str(output / "desktop-scene.png"))

        # Validate the whole selection before any upload, even when an earlier file is valid.
        page.get_by_role("button", name="Photos", exact=True).click()
        page.get_by_role("heading", name="Photos of the home").wait_for()
        uploads = []
        upload_url = f"{base_url}/api/assessments/{assessment_id}/photos"
        page.on("request", lambda request: uploads.append(request.url) if request.method == "POST" and request.url == upload_url else None)
        png = (ROOT / "data/demo/synthetic-1.png").read_bytes()
        page.locator("input[type=file]").set_input_files([
            {"name": "valid.png", "mimeType": "image/png", "buffer": png},
            {"name": "unsupported.heic", "mimeType": "image/heic", "buffer": b"unsupported test file"},
        ])
        expect(page.get_by_text("Use JPG or PNG files", exact=False)).to_be_visible()
        assert not uploads, "An invalid mixed batch uploaded a file before validation"
        before = context.request.get(f"{base_url}/api/assessments/{assessment_id}").json()
        assert len(before["photos"]) == 2
        original_scene = before["photos"][0]["scene_id"]

        # The first upload changes ready -> draft. The modal must survive to send the second.
        def inspect_batch(route):
            if route.request.method == "POST" and len(uploads) == 2:
                expect(page.get_by_role("heading", name="Photos of the home")).to_be_visible()
                expect(page.get_by_text("3 overview · 0 detail", exact=False)).to_be_visible()
                expect(page.get_by_role("button", name="Close", exact=True)).to_be_disabled()
                expect(page.get_by_label("Room", exact=True)).to_be_disabled()
                expect(page.get_by_label("Photo type", exact=True)).to_be_disabled()
                expect(page.get_by_role("button", name="Start analysis")).to_be_disabled()
                page.screenshot(path=str(output / "desktop-batch-upload.png"))
            route.continue_()

        page.route(upload_url, inspect_batch)
        page.locator("input[type=file]").set_input_files(photos)
        expect(page.get_by_text("4 overview · 0 detail", exact=False)).to_be_visible(timeout=30000)
        expect(page.get_by_role("button", name="Close", exact=True)).to_be_enabled()
        page.unroute(upload_url, inspect_batch)
        after = context.request.get(f"{base_url}/api/assessments/{assessment_id}").json()
        assert len(uploads) == 2 and len(after["photos"]) == 4
        assert {photo["scene_id"] for photo in after["photos"]} == {original_scene}, "Batch created another room"
        assert after["input_revision"] == before["input_revision"] + 2 and after["status"] == "draft"
        page.get_by_role("button", name="Close", exact=True).click()
        expect(page.get_by_role("heading", name="Photos of the home")).to_be_visible()
        expect(page.get_by_role("button", name="Close", exact=True)).to_have_count(0)
        page.get_by_role("button", name="Start analysis").click()
        page.get_by_role("button", name="Start review").wait_for(timeout=60000)

        # Exercise VGGT's client-side frame cap without configuring or calling a live model.
        capped = context.new_page()
        capped.route("**/api/health", lambda route: route.fulfill(json={**health, "modes": {**health["modes"], "reconstruction": "vggt"}}))
        mutations = []
        capped.on("request", lambda request: mutations.append(request.url) if request.method == "POST" else None)
        capped.goto(f"{base_url}/pazienti?id={assessment_id}")
        capped.wait_for_load_state("networkidle")
        capped.get_by_role("button", name="Photos", exact=True).click()
        capacity = health["photo_limits"]["frame_cap"] - 4
        capped.locator("input[type=file]").set_input_files([
            {"name": f"over-cap-{i}.png", "mimeType": "image/png", "buffer": png} for i in range(capacity + 1)
        ])
        expect(capped.get_by_text(f"you can add {capacity} more", exact=False)).to_be_visible()
        assert not mutations, "Over-cap selection sent a mutation"
        capped.screenshot(path=str(output / "desktop-upload-cap.png"))
        capped.close()

        page.get_by_role("button", name="Start review").click()
        card = page.locator("aside")
        for index, answer in enumerate(["Confirm", "Exclude", "Check in person", "Confirm"], start=1):
            expect(card).to_contain_text(f"{index} of 4")
            if answer == "Check in person":
                card.get_by_role("button", name=answer).click()
                card.locator("textarea").fill("Request the missing photo; simulated review.")
                card.get_by_role("button", name="Mark to check in person").click()
            else:
                card.get_by_role("button", name=answer).click()
        page.get_by_role("heading", name="Review complete").wait_for()
        page.screenshot(path=str(output / "desktop-summary.png"))
        page.get_by_role("button", name="Prepare report").click()

        page.get_by_role("button", name="Review and release").click(timeout=60000)
        dialog = page.locator("div.glass", has=page.get_by_role("heading", name="Release to the family"))
        pdf_url = dialog.get_by_role("link", name="PDF").get_attribute("href")
        response = context.request.get(base_url + pdf_url)
        assert response.status == 200 and response.body().startswith(b"%PDF")
        dialog.get_by_role("checkbox").check()
        dialog.get_by_role("button", name="Release", exact=True).click()
        family_link = page.locator("input[readonly]").input_value()
        page.screenshot(path=str(output / "desktop-released.png"))
        anonymous = browser.new_context()
        assert anonymous.request.get(family_link).status == 200
        assert anonymous.request.get(family_link + "?format=pdf").body().startswith(b"%PDF")
        page.get_by_role("button", name="Revoke link").click()
        expect(page.get_by_text("has been revoked")).to_be_visible()
        assert anonymous.request.get(family_link).status == 404
        anonymous.close()

        # Without WebGL the review stays usable from the photos.
        fallback = context.new_page()
        fallback.add_init_script(NO_WEBGL)
        fallback.goto(f"{base_url}/pazienti?id={assessment_id}")
        expect(fallback.get_by_text("3D view unavailable.", exact=False)).to_be_visible(timeout=20000)
        fallback.get_by_role("button", name="Room photos").click()
        expect(fallback.get_by_role("img", name="Room photo 1")).to_be_visible()
        fallback.close()

        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(base_url + "/pazienti/nuovo")
        page.get_by_role("button", name="Continue to photos").wait_for()
        page.screenshot(path=str(output / "phone-intake.png"), full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Phone intake overflows"
        assert not errors, errors
        browser.close()
    print(f"PASS: intake, mixed-file rejection, same-room batch upload, frame-cap preflight, fixture rerun, 3D scene, guided review, PDF, release, family link, revoke, WebGL fallback, phone intake. Screenshots: {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--executable")
    args = parser.parse_args()
    run(args.base_url.rstrip("/"), args.executable)
