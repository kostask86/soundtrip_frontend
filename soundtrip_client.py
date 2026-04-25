"""HTTP client for SoundTrip backend playlist generation (OpenAPI 3.1 paths)."""

from __future__ import annotations

import time
from typing import Any

import requests

GENERATE_PATH = "/api/v1/playlists/generate"
JOBS_PATH = "/api/v1/playlists/jobs/{job_id}"
PLAYLIST_PATH = "/api/v1/playlists/{playlist_id}"


class SoundTripAPIError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None, body: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


def _raise_for_status(resp: requests.Response) -> None:
    if resp.ok:
        return
    body = resp.text
    try:
        detail = resp.json()
        if isinstance(detail, dict) and "detail" in detail:
            msg = str(detail["detail"])
        else:
            msg = str(detail)
    except Exception:
        msg = body or resp.reason
    raise SoundTripAPIError(
        msg or f"HTTP {resp.status_code}",
        status_code=resp.status_code,
        body=body,
    )


def post_generate(base_url: str, prompt: str, *, timeout: float = 60.0) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}{GENERATE_PATH}"
    resp = requests.post(url, json={"prompt": prompt}, timeout=timeout)
    _raise_for_status(resp)
    if resp.status_code != 202:
        raise SoundTripAPIError(
            f"Expected 202 from generate, got {resp.status_code}",
            status_code=resp.status_code,
            body=resp.text,
        )
    data = resp.json()
    if "job_id" not in data:
        raise SoundTripAPIError("Generate response missing job_id", body=resp.text)
    return data


def get_job(base_url: str, job_id: str, *, timeout: float = 60.0) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}{JOBS_PATH.format(job_id=job_id)}"
    resp = requests.get(url, timeout=timeout)
    _raise_for_status(resp)
    return resp.json()


def get_playlist(base_url: str, playlist_id: int, *, timeout: float = 60.0) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}{PLAYLIST_PATH.format(playlist_id=playlist_id)}"
    resp = requests.get(url, timeout=timeout)
    _raise_for_status(resp)
    return resp.json()


def _job_is_terminal(status: str) -> bool:
    s = (status or "").lower()
    return s in (
        "completed",
        "complete",
        "succeeded",
        "success",
        "done",
        "finished",
        "ready",
        "failed",
        "error",
        "cancelled",
        "canceled",
    )


def _job_is_failed(status: str) -> bool:
    s = (status or "").lower()
    return s in ("failed", "error", "cancelled", "canceled")


def wait_for_playlist(
    base_url: str,
    prompt: str,
    *,
    poll_interval: float = 0.75,
    max_wait_seconds: float = 180.0,
) -> dict[str, Any]:
    gen = post_generate(base_url, prompt)
    job_id = gen["job_id"]

    deadline = time.monotonic() + max_wait_seconds
    last_status = ""

    while time.monotonic() < deadline:
        job = get_job(base_url, job_id)
        last_status = str(job.get("status") or "")

        err = job.get("error")
        if err:
            raise SoundTripAPIError(str(err))

        pl = job.get("playlist")
        if isinstance(pl, dict) and pl.get("songs") is not None:
            return pl

        if _job_is_failed(last_status):
            raise SoundTripAPIError(last_status or "Playlist job failed")

        pid = job.get("playlist_id")
        if pid is not None and _job_is_terminal(last_status):
            return get_playlist(base_url, int(pid))

        if _job_is_terminal(last_status):
            raise SoundTripAPIError("Job finished without playlist data")

        time.sleep(poll_interval)

    raise SoundTripAPIError(f"Timed out waiting for playlist (last status: {last_status!r})")
