#!/usr/bin/env python3
"""Keep Cindy's CSP-compatible panel page in sync with the shared app."""
from pathlib import Path
import sys

root = Path(__file__).resolve().parent / "core"
source = (root / "app.html").read_text(encoding="utf-8")
start = source.index("<script>")
end = source.rindex("</script>")
files = {
    root / "panel-app.html": source[:start] + '<script src="panel-app.js"></script>' + source[end + len("</script>"):],
    root / "panel-app.js": source[start + len("<script>"):end],
}
if sys.argv[1:] == ["--check"]:
    stale = [str(path.name) for path, content in files.items() if not path.exists() or path.read_text(encoding="utf-8") != content]
    if stale:
        raise SystemExit("Outdated panel files: " + ", ".join(stale))
    print("Panel files match the shared app.")
elif not sys.argv[1:]:
    for path, content in files.items():
        path.write_text(content, encoding="utf-8")
    print("Panel files generated from the shared app.")
else:
    raise SystemExit("Usage: sync-panel-app.py [--check]")
