#!/usr/bin/env python3
"""Generate Cindy's self-contained core from the shared Task Progress sources."""
from pathlib import Path
import argparse
import filecmp
import json
import shutil
import subprocess
import tempfile


CINDY = Path(__file__).resolve().parent
SHARED = CINDY.parent
CORE_FILES = (
    "app.html", "server.py", "execution_lifecycle.py", "execution_worker.py",
    "legacy_history.py", "mcp_bridge.py", "service_manager.py", "shared_service.py", "widget_store.py",
)
PATCHED_FILES = ("app.html", "server.py", "mcp_bridge.py", "service_manager.py")


def validate_versions() -> None:
    manifests = (SHARED / ".codex-plugin/plugin.json", CINDY / "ghost.json",
                 SHARED / "dsh/package.json")
    versions = {str(path): json.loads(path.read_text(encoding="utf-8"))["version"]
                for path in manifests}
    if len(set(versions.values())) != 1:
        raise ValueError("Task Progress platform versions must match: " +
                         ", ".join(f"{path}={version}" for path, version in versions.items()))


def generate(destination: Path) -> None:
    validate_versions()
    destination.mkdir()
    for name in CORE_FILES:
        shutil.copy2(SHARED / name, destination / name)
    for name in PATCHED_FILES:
        with (CINDY / "patches" / (name + ".patch")).open("rb") as patch:
            subprocess.run(["patch", "--batch", "--forward", "-s", "-p1"],
                           cwd=destination, stdin=patch, check=True)
        # BSD patch may create a backup when context shifts. It is not a runtime file.
        (destination / (name + ".orig")).unlink(missing_ok=True)
    for directory in ("scripts", "skills", "assets", ".codex-plugin"):
        shutil.copytree(SHARED / directory, destination / directory,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(CINDY / "ghost.json", destination / "ghost.json")
    app = (destination / "app.html").read_text(encoding="utf-8")
    start, end = app.index("<script>"), app.rindex("</script>")
    (destination / "panel-app.html").write_text(
        app[:start] + '<script src="panel-app.js"></script>' + app[end + len("</script>"):],
        encoding="utf-8")
    (destination / "panel-app.js").write_text(app[start + len("<script>"):end], encoding="utf-8")


def files(root: Path) -> set[Path]:
    return {p.relative_to(root) for p in root.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="check the existing generated core")
    mode.add_argument("--stage", type=Path, help="create a new clean source directory for Cindy Forge")
    args = parser.parse_args()
    core = CINDY / "core"
    with tempfile.TemporaryDirectory(prefix="task-progress-cindy-") as tmp:
        generated = Path(tmp) / "core"
        generate(generated)
        if args.stage:
            stage = args.stage.resolve()
            stage.mkdir(parents=True, exist_ok=False)
            for name in ("ghost.json", "main.js", "panel.html", "panel.css", "panel.js"):
                shutil.copy2(CINDY / name, stage / name)
            shutil.copytree(CINDY / "assets", stage / "assets")
            shutil.copytree(CINDY / "node", stage / "node")
            shutil.copytree(generated, stage / "core")
            manifest = json.loads((stage / "ghost.json").read_text(encoding="utf-8"))
            for skill in manifest["skill"]["items"]:
                relative = Path(skill["dir"])
                skill_dir = stage / relative
                skill_dir.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(generated / relative, skill_dir)
            print(f"Prepared self-contained Cindy Forge source: {stage}")
        elif args.check:
            if not core.is_dir() or files(core) != files(generated):
                raise SystemExit("Generated Cindy core has missing or extra files; run prepare.py")
            changed = [str(p) for p in sorted(files(generated))
                       if not filecmp.cmp(core / p, generated / p, shallow=False)]
            if changed:
                raise SystemExit("Generated Cindy core differs: " + ", ".join(changed))
            print("Cindy core matches shared sources and adapter patches.")
        else:
            # Replace only the generated core, never the authoring files beside it.
            if core.exists():
                shutil.rmtree(core)
            shutil.copytree(generated, core)
            print("Generated Cindy core from shared sources and adapter patches.")


if __name__ == "__main__":
    main()
