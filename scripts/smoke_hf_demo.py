"""Browser smoke for the static artifact, locally or on the public Space."""
import argparse
from playwright.sync_api import sync_playwright, expect

parser = argparse.ArgumentParser()
parser.add_argument("--base-url", default="http://127.0.0.1:3024")
parser.add_argument("--browser", default="/usr/bin/google-chrome")
args = parser.parse_args()
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, executable_path=args.browser,
                               args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    errors, failures, requests = [], [], []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("response", lambda r: failures.append(f"{r.status} {r.url}") if r.status >= 400 else None)
    page.on("request", lambda r: requests.append((r.method, r.url)))
    page.goto(args.base_url, wait_until="networkidle")
    expect(page.get_by_text("Maria R. · 84", exact=True)).to_be_visible()
    expect(page.get_by_text("New patient", exact=True)).to_have_count(0)
    page.locator('a[href*="id="]').click()
    expect(page.get_by_role("button", name="Home in 3D", exact=True)).to_be_visible()
    room = page.locator('iframe[title="The home in 3D"]').element_handle().content_frame()
    room.wait_for_function("window.splatsReady === true && window.captureFrameMs !== undefined", timeout=120000)
    assert not room.evaluate("window.captureError || null")
    print("Splat stream complete; first frame (ms):", room.evaluate("window.captureFrameMs"))
    # Software-only test runners may trigger the remote viewer's photo fallback.
    page.get_by_role("button", name="Home in 3D", exact=True).click()
    expect(room.locator("canvas")).to_be_visible()
    assert any(url.endswith("/capture-data/room.ksplat") for _, url in requests), "Actual reconstruction was not requested"
    page.wait_for_load_state("networkidle")
    page.screenshot(path="/tmp/stepwise-demo-scene.png")
    before = room.locator("canvas").screenshot()
    bounds = page.locator('iframe[title="The home in 3D"]').bounding_box()
    x, y = bounds["x"] + bounds["width"] * .5, bounds["y"] + bounds["height"] * .4
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + 80, y + 20)
    page.mouse.up()
    after = room.locator("canvas").screenshot()
    assert before != after, "Dragging did not change the rendered reconstruction"
    page.locator("button").filter(has_text="Rug").click()
    expect(page.get_by_role("button", name="Confirm", exact=True)).to_be_disabled()
    expect(page.get_by_role("button", name="Next", exact=True)).to_be_visible()
    page.get_by_role("button", name="Next", exact=True).click()
    page.get_by_role("button", name="Close", exact=True).click()
    page.get_by_role("button", name="Room photos", exact=True).click()
    expect(page.locator('img[alt="Room photo 1"]')).to_be_visible()
    page.reload(wait_until="networkidle")
    expect(page.get_by_role("button", name="Home in 3D", exact=True)).to_be_visible()
    page.get_by_title("All patients", exact=True).click()
    expect(page.get_by_text("Maria R. · 84", exact=True)).to_be_visible()
    page.goto(args.base_url.rstrip("/") + "/pazienti/nuovo/index.html", wait_until="networkidle")
    expect(page.get_by_text("Back to demo", exact=True)).to_be_visible()
    expect(page.locator("form")).to_have_count(0)
    page.get_by_text("Back to demo", exact=True).click()
    expect(page.get_by_text("Maria R. · 84", exact=True)).to_be_visible()
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(args.base_url, wait_until="networkidle")
    expect(page.get_by_text("Maria R. · 84", exact=True)).to_be_visible()
    page.screenshot(path="/tmp/stepwise-demo-mobile.png", full_page=True)
    assert all(method in {"GET", "HEAD"} for method, _ in requests), requests
    assert not any("/api/" in url for _, url in requests), requests
    assert not errors, errors
    assert not failures, failures
    print(f"PASS: remote snapshot, full Gaussian-splat stream, rendered frame and orbit interaction, evidence navigation, read-only controls, refresh, intake guard, mobile landing; {len(requests)} read requests, no API calls or browser errors")
    browser.close()
