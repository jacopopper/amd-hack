"""Build the Step Wise website: one Italian page at the root, one English page under en/.

The page is site/index.html.j2 filled with the strings in site/i18n/<lang>.toml. Images, fonts and the
prototype clip are copied from pitch/, so the site and the pitch never drift apart. The output in
site/dist/ is plain static files: upload its contents to any static host (e.g. a Hugging Face static Space).

    .venv/bin/python site/build.py
"""

import shutil
import tomllib
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

SITE = Path(__file__).resolve().parent
PITCH = SITE.parent / "pitch"
DIST = SITE / "dist"

# (code, output dir relative to dist). The first language is the default, served at the root.
LANGS = [("it", ""), ("en", "en/")]

# Shared across languages. Set the LinkedIn URLs to show the link under each person.
TEAM = [
    {"name": "Davide Benedetti Valentini", "photo": "davide.jpg", "linkedin": ""},
    {"name": "Jacopo Dardini", "photo": "jacopo.jpg", "linkedin": ""},
]

ASSETS = ["fonts", "photos", "app", "logos", "sources", "team", "capture3d.js"]


def build() -> None:
    if DIST.exists():
        shutil.rmtree(DIST)
    (DIST / "assets").mkdir(parents=True)
    for name in ASSETS:
        src = PITCH / "assets" / name
        if src.is_dir():
            shutil.copytree(src, DIST / "assets" / name)
        else:
            shutil.copy2(src, DIST / "assets" / name)
    shutil.copy2(PITCH / "cover.png", DIST / "assets" / "cover.png")
    (DIST / "media").mkdir()
    shutil.copy2(PITCH / "media" / "prototipo.mp4", DIST / "media" / "prototipo.mp4")

    env = Environment(loader=FileSystemLoader(SITE), undefined=StrictUndefined, autoescape=False, keep_trailing_newline=True)
    template = env.get_template("index.html.j2")
    for code, out in LANGS:
        strings = tomllib.loads((SITE / "i18n" / f"{code}.toml").read_text(encoding="utf-8"))
        base = "../" * out.count("/")
        langs = [{"code": c, "href": base + o or "./"} for c, o in LANGS]
        html = template.render(t=strings, lang=code, base=base, langs=langs, team=TEAM)
        target = DIST / out / "index.html"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(html, encoding="utf-8")
        print(f"{code}: {target.relative_to(SITE.parent)}")


if __name__ == "__main__":
    build()
