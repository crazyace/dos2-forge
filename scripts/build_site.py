"""Build a static, searchable reference site from Forge's JSON exports.

Usage:
    dos2forge templates -o templates.json
    dos2forge instances -o instances.json
    python scripts/build_site.py templates.json instances.json -o site

The output directory holds a self-contained page (no external assets)
plus a compact records file the page searches client-side.  Commit the
``site/`` directory and the repository's Pages workflow publishes it.

All root templates are included.  Level instances are filtered to the
interesting ones — those with a display name or a scripted ``S_`` name —
because the full set (~226k rows, mostly unnamed scenery placements) is
too heavy for a browser and adds nothing searchable.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

PAGE = """<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DOS2 Forge reference</title>
<style>
  :root { color-scheme: light dark; font-family: system-ui, sans-serif; }
  body { margin: 2rem auto; max-width: 72rem; padding: 0 1rem; }
  h1 { font-size: 1.4rem; } .muted { opacity: .65; font-size: .9rem; }
  input { width: 100%; font-size: 1.1rem; padding: .5rem .75rem; margin: 1rem 0;
          border: 1px solid #8886; border-radius: .5rem; background: transparent; color: inherit; }
  table { border-collapse: collapse; width: 100%; font-size: .9rem; }
  th, td { text-align: left; padding: .35rem .5rem; border-bottom: 1px solid #8883;
           overflow-wrap: anywhere; }
  td.uuid { font-family: ui-monospace, monospace; font-size: .8rem; white-space: nowrap; }
  tr:hover { background: #8881; cursor: copy; }
</style>
<h1>DOS2 Forge reference</h1>
<p class="muted">Root template and named level-instance UUIDs extracted from a
Divinity: Original Sin&nbsp;2 Definitive Edition install with
<a href="https://github.com/crazyace/dos2-forge">dos2-forge</a>.
Click a row to copy its UUID. Data derived from game files owned by Larian Studios.</p>
<input id="q" placeholder="Search names, display names, stats ids, UUIDs… (e.g. Migo)" autofocus>
<p class="muted" id="count"></p>
<table><thead><tr>
  <th>Name</th><th>Display name</th><th>Kind</th><th>Level</th><th>Stats</th><th>UUID</th>
</tr></thead><tbody id="rows"></tbody></table>
<script>
const fold = s => (s || "").toLowerCase().replace(/[\\u2018\\u2019\\u02bc]/g, "'");
let records = [];
fetch("data/records.json").then(r => r.json()).then(data => {
  records = data.map(r => ({ r, key: fold(r.join(" ")) }));
  render(document.getElementById("q").value);
});
function render(query) {
  const needle = fold(query.trim());
  const hits = needle
    ? records.filter(x => x.key.includes(needle)).slice(0, 500)
    : records.slice(0, 500);
  document.getElementById("count").textContent =
    needle ? `${hits.length}${hits.length === 500 ? "+" : ""} match(es)` :
    `${records.length} records — type to search`;
  document.getElementById("rows").innerHTML = hits.map(x => {
    const [uuid, name, display, kind, level, stats] = x.r;
    const esc = s => (s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;");
    return `<tr data-uuid="${uuid}"><td>${esc(name)}</td><td>${esc(display)}</td>` +
           `<td>${esc(kind)}</td><td>${esc(level)}</td><td>${esc(stats)}</td>` +
           `<td class="uuid">${uuid}</td></tr>`;
  }).join("");
}
document.getElementById("q").addEventListener("input", e => render(e.target.value));
document.getElementById("rows").addEventListener("click", e => {
  const row = e.target.closest("tr");
  if (row) navigator.clipboard?.writeText(row.dataset.uuid);
});
</script>
</html>
"""


def compact(record: dict, kind: str) -> list[str]:
    return [
        record.get("map_key", ""),
        record.get("name", ""),
        record.get("display_name", ""),
        record.get("type") or kind,
        record.get("level", ""),
        record.get("stats", ""),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("templates", type=Path)
    parser.add_argument("instances", type=Path)
    parser.add_argument("-o", "--output", type=Path, default=Path("site"))
    args = parser.parse_args()

    templates = json.loads(args.templates.read_text("utf-8"))
    instances = json.loads(args.instances.read_text("utf-8"))

    rows = [compact(t, "template") for t in templates]
    kept = 0
    for instance in instances:
        if instance.get("display_name") or instance.get("name", "").startswith("S_"):
            rows.append(compact(instance, "instance"))
            kept += 1
    rows.sort(key=lambda r: (r[1].lower(), r[0]))

    data_dir = args.output / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "records.json").write_text(
        json.dumps(rows, separators=(",", ":"), ensure_ascii=False), "utf-8"
    )
    (args.output / "index.html").write_text(PAGE, "utf-8")
    print(
        f"site written to {args.output}: {len(templates)} templates + "
        f"{kept} named instances ({len(instances) - kept} unnamed instances skipped)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
