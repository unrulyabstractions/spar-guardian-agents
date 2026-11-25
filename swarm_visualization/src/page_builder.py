"""Write the swarm picture as one HTML file that opens from disk: three.js, the viewer and the run's story inlined."""
from __future__ import annotations

import base64
import html
import json
import re
from pathlib import Path

WEB = Path(__file__).resolve().parent / "web"
PLACEHOLDER = re.compile(r"__(TITLE|CSS|THREE|STORY|JS)__")


def build_page(story: dict) -> str:
    three = base64.b64encode((WEB / "vendor" / "three.module.min.js").read_bytes()).decode("ascii")
    parts = {
        "TITLE": html.escape(story.get("run") or "swarm"),
        "CSS": (WEB / "viewer.css").read_text(),
        "THREE": three,
        # "</" inside a <script> block would end it early.
        "STORY": json.dumps(story, separators=(",", ":")).replace("</", "<\\/"),
        "JS": (WEB / "viewer.js").read_text(),
    }
    template = (WEB / "viewer.html").read_text()
    return PLACEHOLDER.sub(lambda m: parts[m.group(1)], template)


def write_page(story: dict, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_page(story))
    return out
