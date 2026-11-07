#!/usr/bin/env python3
"""Sync the GitHub Pages native UI from the plugin, without changing its markup.

Run: python3 scripts/sync-pages-demo.py
Check after plugin UI/version changes: python3 scripts/sync-pages-demo.py --check

HTTP uses the HTML copy. file:// uses the identical HTML encoded in JavaScript
and loaded into srcdoc, so the demo host can open native details without changing
plugin code or requiring a local server. Both are generated; do not edit them.
"""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins/task-progress"
ASSETS = ROOT / "docs/assets"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if either generated asset is stale")
    args = parser.parse_args()
    version = json.loads((PLUGIN / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))["version"]
    html = (PLUGIN / "app.html").read_text(encoding="utf-8").replace("__TASK_PROGRESS_VERSION__", version)
    javascript = (
        "// Generated from task-progress-app.html without UI changes; only used for file:// previews.\n"
        "window.taskProgressDemoSource = " + json.dumps(html, ensure_ascii=False) + ";\n"
    )
    outputs = {ASSETS / "task-progress-app.html": html, ASSETS / "demo-app-source.js": javascript}
    stale = []
    for path, content in outputs.items():
        expected = content.encode("utf-8")
        if args.check:
            if not path.exists() or path.read_bytes() != expected:
                stale.append(str(path.relative_to(ROOT)))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(expected)
    if stale:
        parser.exit(1, "Stale demo assets: " + ", ".join(stale) + "\nRun python3 scripts/sync-pages-demo.py\n")
    print(f"Native demo assets {'match' if args.check else 'synced from'} plugin {version}; HTML and JS payload are identical.")


if __name__ == "__main__":
    main()
