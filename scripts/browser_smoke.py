#!/usr/bin/env python3
"""Optional isolated headless browser check of the Next.js reviewer app. Requires playwright, no external model calls."""
import argparse
import os
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
        browser = playwright.chromium.launch(headless=True, **({"executable_path": executable} if executable else {}))
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)

        page.goto(base_url + "/login")
        page.screenshot(path=str(output / "desktop-login.png"), full_page=True)
        page.locator("input[name=password]").fill(os.environ["REVIEWER_PASSWORD"])
        page.get_by_role("button", name="Accedi").click()
        page.get_by_role("link", name="Nuovo paziente").click()

        page.locator("input[name=label]").fill("Browser smoke · profilo fittizio")
        page.locator("textarea[name=description]").fill("Profilo fittizio: usa un deambulatore, nessuna storia clinica fornita.")
        page.locator("input[name=mobility_aid]").fill("deambulatore")
        page.locator("input[name=profile_confirmed]").check()
        page.locator("input[name=staged_data]").check()
        page.screenshot(path=str(output / "desktop-intake.png"), full_page=True)
        page.get_by_role("button", name="Continua con le foto").click()

        page.get_by_role("heading", name="Foto della casa").wait_for()
        match = re.search(r"[?&]id=([^&]+)", page.url)
        assert match, page.url
        assessment_id = match.group(1)
        page.get_by_label("Stanza").fill("Soggiorno")
        page.locator("input[type=file]").set_input_files([str(ROOT / "data/demo/synthetic-1.png"), str(ROOT / "data/demo/synthetic-2.png")])
        expect(page.get_by_text("2 foto")).to_be_visible(timeout=30000)
        page.get_by_role("button", name="Avvia l’analisi").click()

        page.get_by_role("button", name="Avvia revisione").wait_for(timeout=60000)
        expect(page.get_by_text("Stiamo analizzando la casa")).to_be_hidden(timeout=60000)
        page.locator("canvas").wait_for(timeout=20000)
        page.screenshot(path=str(output / "desktop-scene.png"))

        page.get_by_role("button", name="Avvia revisione").click()
        card = page.locator("aside")
        for index, answer in enumerate(["Confermo", "Non è un problema", "Serve verificare", "Confermo"], start=1):
            expect(card).to_contain_text(f"{index} di 4")
            if answer == "Serve verificare":
                card.get_by_role("button", name=answer).click()
                card.locator("textarea").fill("Richiedere la foto mancante; revisione simulata.")
                card.get_by_role("button", name="Invia").click()
            else:
                card.get_by_role("button", name=answer).click()
        page.get_by_role("heading", name="Revisione completata").wait_for()
        page.screenshot(path=str(output / "desktop-summary.png"))
        page.get_by_role("button", name="Prepara il report").click()

        page.get_by_role("button", name="Rivedi e rilascia").click(timeout=60000)
        dialog = page.locator("div.glass", has=page.get_by_role("heading", name="Rilascia alla famiglia"))
        pdf_url = dialog.get_by_role("link", name="PDF").get_attribute("href")
        response = context.request.get(base_url + pdf_url)
        assert response.status == 200 and response.body().startswith(b"%PDF")
        dialog.get_by_role("checkbox").check()
        dialog.get_by_role("button", name="Rilascia", exact=True).click()
        family_link = page.locator("input[readonly]").input_value()
        page.screenshot(path=str(output / "desktop-released.png"))
        anonymous = browser.new_context()
        assert anonymous.request.get(family_link).status == 200
        assert anonymous.request.get(family_link + "?format=pdf").body().startswith(b"%PDF")
        page.get_by_role("button", name="Revoca il link").click()
        expect(page.get_by_text("è stato revocato")).to_be_visible()
        assert anonymous.request.get(family_link).status == 404
        anonymous.close()

        # Without WebGL the review stays usable from the photos.
        fallback = context.new_page()
        fallback.add_init_script(NO_WEBGL)
        fallback.goto(f"{base_url}/pazienti?id={assessment_id}")
        expect(fallback.get_by_text("Vista 3D non disponibile.", exact=False)).to_be_visible(timeout=20000)
        fallback.get_by_role("button", name="Foto della stanza").click()
        expect(fallback.get_by_role("img", name="Foto 1 della stanza")).to_be_visible()
        fallback.close()

        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(base_url + "/pazienti/nuovo")
        page.get_by_role("button", name="Continua con le foto").wait_for()
        page.screenshot(path=str(output / "phone-intake.png"), full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Phone intake overflows"
        assert not errors, errors
        browser.close()
    print(f"PASS: intake, room photos, fixture analysis, 3D scene, guided review, PDF, release, family link, revoke, WebGL fallback, phone intake. Screenshots: {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--executable")
    args = parser.parse_args()
    run(args.base_url.rstrip("/"), args.executable)
