"""Google Drive upload. Local files are already written by the time this runs, so
a failure here is logged and retried on the next run — it never loses an edition.

Two methods: the Drive API with a cached OAuth token (default), or `rclone copy`
to a configured remote. Both overwrite the week's files rather than duplicating.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from .config import Config
from .state import State

log = logging.getLogger("digest.deliver")

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
MIME = {
    ".txt": "text/plain", ".md": "text/markdown", ".html": "text/html",
    ".pdf": "application/pdf", ".mp3": "audio/mpeg",
}


class DeliveryError(RuntimeError):
    pass


def _drive_service(cfg: Config):
    from google.auth.transport.requests import Request  # noqa: PLC0415
    from google.oauth2.credentials import Credentials  # noqa: PLC0415
    from google_auth_oauthlib.flow import InstalledAppFlow  # noqa: PLC0415
    from googleapiclient.discovery import build  # noqa: PLC0415

    token_file = cfg.drive.token_file
    creds = None
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not cfg.drive.credentials_file.exists():
                raise DeliveryError(
                    f"no OAuth client secrets at {cfg.drive.credentials_file} — "
                    "download one from the Google Cloud console first"
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                str(cfg.drive.credentials_file), SCOPES
            )
            creds = flow.run_local_server(port=0)
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(creds.to_json(), encoding="utf-8")
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _upload_oauth(paths: list[Path], cfg: Config, state: State, week: str) -> None:
    from googleapiclient.http import MediaFileUpload  # noqa: PLC0415

    service = _drive_service(cfg)
    for path in paths:
        media = MediaFileUpload(
            str(path), mimetype=MIME.get(path.suffix, "application/octet-stream"), resumable=False
        )
        existing = state.delivery(week, path.name)
        if not existing:
            # A file may exist from a run whose bookkeeping did not land.
            query = (
                f"name = '{path.name}' and '{cfg.drive.folder_id}' in parents and trashed = false"
            )
            found = service.files().list(q=query, fields="files(id)", pageSize=1).execute()
            existing = (found.get("files") or [{}])[0].get("id")

        if existing:
            file = service.files().update(fileId=existing, media_body=media).execute()
        else:
            file = (
                service.files()
                .create(
                    body={"name": path.name, "parents": [cfg.drive.folder_id]},
                    media_body=media,
                    fields="id",
                )
                .execute()
            )
        state.record_delivery(week, path.name, file["id"])
        log.info("uploaded %s", path.name)


def _upload_rclone(paths: list[Path], cfg: Config, state: State, week: str) -> None:
    if not cfg.drive.rclone_remote:
        raise DeliveryError("drive.rclone_remote is not set in digest.toml")
    for path in paths:
        subprocess.run(
            ["rclone", "copyto", str(path), f"{cfg.drive.rclone_remote}/{path.name}"],
            check=True, capture_output=True, timeout=300,
        )
        state.record_delivery(week, path.name, f"rclone:{cfg.drive.rclone_remote}")
        log.info("uploaded %s via rclone", path.name)


def deliver(paths: list[Path], cfg: Config, state: State, week: str) -> bool:
    """Return True when everything uploaded. Never raises."""
    if not cfg.drive.enabled:
        log.info("drive delivery disabled in config")
        return True
    if cfg.drive.method == "oauth" and not cfg.drive.folder_id:
        log.error("drive.enabled is true but drive.folder_id is empty; skipping upload")
        return False

    try:
        if cfg.drive.method == "rclone":
            _upload_rclone(paths, cfg, state, week)
        else:
            _upload_oauth(paths, cfg, state, week)
        return True
    except Exception as exc:
        log.error("drive upload failed (local files are written; will retry next run): %s", exc)
        return False
