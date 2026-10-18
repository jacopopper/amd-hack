"""Stage the static review plus the remote-committed reconstruction, never private working captures."""
import json
import shutil
import subprocess
from pathlib import Path

from step_wise.config import ROOT

out = ROOT / "frontend/.next-demo"
stage = ROOT / "dist/hf-space"
assert (out / "demo/responses.json").is_file(), "Export the snapshot and build with NEXT_PUBLIC_STATIC_DEMO=true first"
assert "Explore the reconstructed home" in (out / "index.html").read_text(), "Refusing to stage a normal application build"
if stage.exists():
    shutil.rmtree(stage)
stage.mkdir(parents=True)
# Only these built paths and the explicitly tracked reconstruction assets are public.
for name in ("_next", "demo", "pazienti", "404"):
    if (out / name).is_dir():
        shutil.copytree(out / name, stage / name)
for source in out.iterdir():
    if source.is_file() and (source.name in {"index.html", "index.txt", "404.html", "favicon.ico", "local-room-viewer.html", "room-cover-polished.png"} or source.name.startswith("__next")):
        shutil.copyfile(source, stage / source.name)
# Only publish reconstruction assets committed by the remote; ignore stray local captures.
tracked = subprocess.check_output(["git", "ls-files", "-z", "frontend/public/capture-data"], cwd=ROOT).decode().split("\0")
for name in filter(None, tracked):
    source = ROOT / name
    relative = source.relative_to(ROOT / "frontend/public")
    built = out / relative
    assert not source.is_symlink() and built.read_bytes() == source.read_bytes(), f"Changed build asset: {relative}"
    expected = subprocess.check_output(["git", "rev-parse", f"HEAD:{name}"], cwd=ROOT).strip()
    actual = subprocess.check_output(["git", "hash-object", str(source)], cwd=ROOT).strip()
    assert actual == expected, f"Reconstruction differs from merged remote: {relative}"
    target = stage / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(built, target)
assert (stage / "capture-data/room.ksplat").stat().st_size > 20_000_000
shutil.copyfile(ROOT / "deploy/huggingface/README.md", stage / "README.md")
shutil.copyfile(ROOT / "LICENSE", stage / "LICENSE")
shutil.copyfile(ROOT / "deploy/huggingface/THIRD_PARTY_NOTICES.txt", stage / "THIRD_PARTY_NOTICES.txt")
files = [p for p in stage.rglob("*") if p.is_file()]
for path in files:
    assert not path.is_symlink()
    assert path.suffix not in {".map", ".sqlite3", ".py", ".env"}
    if path.suffix in {".json", ".html", ".txt", ".js"}:
        content = path.read_text()
        assert str(ROOT) not in content, f"Workstation path in {path}"
        assert "hf_" not in content or path.suffix == ".js", f"Possible credential in {path}"
print(json.dumps({"directory": str(stage), "files": len(files), "bytes": sum(p.stat().st_size for p in files)}, indent=2))
