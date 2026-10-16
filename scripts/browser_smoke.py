#!/usr/bin/env python3
"""Optional isolated headless browser check. Requires playwright, no external model calls."""
import argparse
import os
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from safe_home.config import ROOT


def run(base_url, executable=None):
    output = ROOT / "data/browser-smoke"
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, **({"executable_path": executable} if executable else {}))
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(base_url)
        page.wait_for_load_state("networkidle")
        page.screenshot(path=str(output / "desktop-login.png"), full_page=True)
        page.get_by_label("Password", exact=True).fill(os.environ["REVIEWER_PASSWORD"])
        page.get_by_role("button", name="Accedi →").click()
        page.get_by_role("button", name="+ Nuova valutazione").wait_for()
        page.get_by_role("button", name="+ Nuova valutazione").click()
        page.get_by_label("Nome della valutazione").fill("Browser smoke · profilo fittizio")
        page.get_by_label("Descrizione del profilo fittizio").fill("Profilo fittizio: usa un deambulatore, nessuna storia clinica fornita.")
        page.get_by_label("Ho verificato i dati inseriti").check()
        page.get_by_label("Confermo l’uso di sole immagini").check()
        page.screenshot(path=str(output / "desktop-intake.png"), full_page=True)
        page.get_by_role("button", name="Crea la valutazione →").click()
        page.get_by_label("+ Scegli le foto dimostrative").set_input_files([str(ROOT / "data/demo/synthetic-1.png"), str(ROOT / "data/demo/synthetic-2.png")])
        page.get_by_role("button", name="Carica 2 foto").click()
        expect(page.get_by_role("button", name="Avvia elaborazione →")).to_be_enabled()
        page.get_by_role("button", name="Avvia elaborazione →").click()
        page.get_by_role("heading", name="Guardare prima di decidere.").wait_for(timeout=30000)
        page.wait_for_load_state("networkidle")
        page.screenshot(path=str(output / "desktop-review.png"), full_page=True)
        expect(page.locator("svg[aria-label='Regione di evidenza'] rect")).to_have_count(1)
        page.get_by_role("button", name="Scena 3D", exact=True).click()
        page.locator("canvas").wait_for(timeout=20000)
        page.screenshot(path=str(output / "desktop-scene.png"), full_page=True)
        page.get_by_role("button", name="Foto sorgente").click()
        cards = page.locator(".finding-card")
        for index, decision in enumerate(["approve", "edit", "reject", "defer"]):
            cards.nth(index).click()
            expect(page.locator(".review-controls")).to_have_attribute("data-hazard-id", cards.nth(index).get_attribute("data-hazard-id"))
            page.get_by_label("Decisione del revisore").select_option(decision)
            if decision == "edit":
                page.get_by_label("Azione", exact=True).fill("Verifica del passaggio con il revisore simulato.")
            if decision == "defer":
                page.get_by_label("Nota (obbligatoria)").fill("Richiedere la foto mancante; revisione simulata.")
            page.get_by_role("button", name="Salva decisione").focus()
            page.keyboard.press("Enter")
            expect(cards.nth(index)).to_contain_text(decision)
        page.get_by_role("button", name="Prepara la bozza →").click()
        page.get_by_role("link", name="Apri PDF ↗").wait_for(timeout=30000)
        page.evaluate("""() => { const original = HTMLCanvasElement.prototype.getContext; HTMLCanvasElement.prototype.getContext = function(kind, ...args) { return String(kind).includes('webgl') ? null : original.call(this, kind, ...args); }; }""")
        page.get_by_role("button", name="Scena 3D", exact=True).click()
        expect(page.get_by_text("Vista 3D non disponibile.", exact=False)).to_be_visible()
        page.get_by_role("button", name="Foto sorgente").click()
        pdf_url = page.get_by_role("link", name="Apri PDF ↗").get_attribute("href")
        response = context.request.get(base_url + pdf_url)
        assert response.status == 200 and response.body().startswith(b"%PDF")
        page.reload()
        page.wait_for_load_state("networkidle")
        expect(page.get_by_text("0 da rivedere", exact=False)).to_be_visible()
        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(path=str(output / "phone-review.png"), full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Phone layout overflows"
        page.get_by_role("button", name="+ Nuova valutazione").click()
        page.screenshot(path=str(output / "phone-intake.png"), full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert not errors, errors
        browser.close()
    print(f"PASS: desktop/phone intake, evidence boxes, synthetic scene, keyboard decisions, persisted review, PDF. Screenshots: {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--executable")
    args = parser.parse_args()
    run(args.base_url.rstrip("/"), args.executable)
