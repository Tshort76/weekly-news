"""Turn the edition's HTML into a PDF using whatever browser is already here.

This used to shell out to `html2pdf`, which is a wrapper script in the owner's
`~/.local/bin` and exists on exactly one machine. It is a good script; it is not
something an installed app can assume.

So the app carries its own browser finder. Every desktop has Chrome, Chromium or
Edge, all three are the same engine, and all three take `--headless
--print-to-pdf`. When none is found we say so in one sentence rather than
failing with a missing-command traceback — a PDF is an optional output and never
worth losing the edition over.

The finder prefers a headless shell where one exists. Full Chrome spawns its
updater, which outlives the PDF write and looks exactly like a hang.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("digest.pdf")

TIMEOUT_SECONDS = 180

# Ordered: the headless shell first, then Chromium, Chrome, Edge.
COMMANDS = (
    "chrome-headless-shell", "chromium", "chromium-browser",
    "google-chrome", "google-chrome-stable", "chrome", "microsoft-edge",
)

MAC_APPS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
)

WINDOWS_APPS = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)

MISSING = (
    "No Chrome, Chromium or Edge was found, so the PDF was skipped. The web page "
    "and the text file were still written. Install any Chromium-based browser and "
    "the PDF will work next time."
)


def find_browser() -> Path | None:
    override = os.environ.get("DIGEST_BROWSER")
    if override and Path(override).exists():
        return Path(override)
    for command in COMMANDS:
        found = shutil.which(command)
        if found:
            return Path(found)
    candidates = WINDOWS_APPS if sys.platform.startswith("win") else MAC_APPS
    for path in candidates:
        if Path(path).exists():
            return Path(path)
    # Playwright installs a headless shell and a lot of machines have one.
    cache = Path.home() / (
        "AppData/Local/ms-playwright" if sys.platform.startswith("win")
        else "Library/Caches/ms-playwright" if sys.platform == "darwin"
        else ".cache/ms-playwright"
    )
    if cache.is_dir():
        for found in sorted(cache.glob("**/chrome-headless-shell")):
            return found
    return None


def html_to_pdf(html_path: Path, pdf_path: Path, browser: Path | None = None) -> bool:
    browser = browser or find_browser()
    if browser is None:
        log.warning("%s", MISSING)
        return False
    # The browser resolves --print-to-pdf against its own working directory, not
    # ours, so both paths go in absolute.
    html_path, pdf_path = Path(html_path).resolve(), Path(pdf_path).resolve()
    try:
        subprocess.run(
            [
                str(browser), "--headless", "--disable-gpu", "--no-sandbox",
                f"--print-to-pdf={pdf_path}", "--no-pdf-header-footer",
                html_path.as_uri(),
            ],
            check=True, capture_output=True, timeout=TIMEOUT_SECONDS,
        )
    except Exception as exc:
        log.error("PDF generation failed with %s: %s", browser.name, exc)
        return False
    return pdf_path.exists()
