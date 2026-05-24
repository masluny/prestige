#!/usr/bin/env python3
"""
app.py - launch Prestige (the OWL ontology editor) web UI.

Starts a local FastAPI server (uvicorn) and opens Prestige in the default
browser. The whole UI is a single-page web app served from `webapp/static/`.
"""

import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn

HERE = Path(__file__).resolve().parent
os.chdir(HERE)
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8765"))
NO_BROWSER = bool(int(os.environ.get("NO_BROWSER", "0")))


def _open_browser_when_ready():
    """Open the default browser once the server should be listening."""
    time.sleep(1.0)
    try:
        webbrowser.open("http://%s:%d/" % (HOST, PORT))
    except Exception:                                  # pragma: no cover
        pass


def main():
    print("Prestige  ->  http://%s:%d/" % (HOST, PORT))
    print("(Ctrl+C to stop)")
    if not NO_BROWSER:
        threading.Thread(target=_open_browser_when_ready, daemon=True).start()
    uvicorn.run("webapp.server:app", host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
