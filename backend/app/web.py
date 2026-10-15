"""Serves the built React dashboard (frontend/dist) from the same server.

Any path that is not an API, webhook, WebSocket or docs route returns the
file if it exists (JS, CSS, fonts) or index.html, so links like /leads/<id>
work when opened directly. If the dashboard is not built, / explains how.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

RESERVED = ("api/", "webhooks/", "ws/", "docs", "redoc", "openapi.json")

NOT_BUILT = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Dashboard not built</title>
<meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="font-family:system-ui;max-width:40rem;margin:4rem auto;padding:0 1rem;
line-height:1.5;color:#1c2a36">
<h1>The dashboard is not built yet</h1>
<p>The server is running. To use the dashboard, build it once:</p>
<pre style="background:#eef1f3;padding:1rem;border-radius:6px">cd frontend
npm ci
npm run build</pre>
<p>Then reload this page. The API is available at <a href="/docs">/docs</a>.</p>
</body></html>"""


def mount_dashboard(app: FastAPI, dist: str) -> None:
    root = Path(dist).resolve()
    index = root / "index.html"

    @app.get("/{path:path}", include_in_schema=False)
    def dashboard(path: str):  # noqa: ANN202
        if path.startswith(RESERVED):
            raise HTTPException(status_code=404, detail="Not Found")
        if not index.is_file():
            return HTMLResponse(NOT_BUILT)
        if path:
            candidate = (root / path).resolve()
            if candidate.is_file() and candidate.is_relative_to(root):
                cache = "public, max-age=31536000, immutable" if path.startswith("assets/") else "no-cache"
                return FileResponse(candidate, headers={"Cache-Control": cache})
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
