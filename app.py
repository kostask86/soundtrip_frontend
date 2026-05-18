from __future__ import annotations

import html
import math
import os
from collections import Counter
from base64 import b64encode
from pathlib import Path
from typing import Any

import pandas as pd
import pydeck as pdk
import requests
import streamlit as st
from pydeck.data_utils import compute_view

from geo import (
    JourneyPoint,
    build_cluster_path_segments,
    build_journey_points,
    build_map_clusters,
    cluster_tooltip_html,
    clusters_in_playlist_order,
    enrich_songs_with_locations,
    extract_location,
    songs_have_locations,
)
from soundtrip_client import SoundTripAPIError, apply_song_metadata, get_playlist, wait_for_playlist

_APP_DIR = Path(__file__).resolve().parent
LOGO_PATH = _APP_DIR / "logo.png"

st.set_page_config(
    page_title="Song Journey",
    page_icon=str(LOGO_PATH) if LOGO_PATH.exists() else "🎵",
    layout="wide",
)

DEFAULT_PROMPT = (
    "Create a 6-song playlist with psychedelic rock and blues rock from the late 1960s and early "
    "1970s. I want it to feel mysterious, dark, transcendent, and intense, with influences from "
    "American folk, blues tradition, and psychedelic counterculture."
)

EMPTY_SIGNAL = "—"

PANEL_META: dict[str, tuple[str, str]] = {
    "Style": ("◌", "style"),
    "Time": ("◷", "time"),
    "Emotion": ("♡", "emotion"),
    "Influence": ("◎", "influence"),
    "Geography": ("◍", "geography"),
    "Songs": ("♪", "songs"),
    "Songs Requested": ("♪", "songs"),
}

# Accent colors for numbered stops (map sidebar + journey path), cycling by song order.
JOURNEY_STOP_COLORS: list[dict[str, str]] = [
    {"border": "#9b7aff", "bg": "rgba(100, 72, 200, 0.5)", "text": "#e8dcff", "loc": "#c4a8ff"},
    {"border": "#ff6eb8", "bg": "rgba(170, 48, 115, 0.5)", "text": "#ffd0e8", "loc": "#ff92c8"},
    {"border": "#5eb8ff", "bg": "rgba(40, 95, 175, 0.5)", "text": "#d4ecff", "loc": "#7ec8ff"},
    {"border": "#ff9a5c", "bg": "rgba(175, 85, 35, 0.5)", "text": "#ffe4cc", "loc": "#ffb878"},
    {"border": "#5ee8c8", "bg": "rgba(35, 130, 105, 0.5)", "text": "#d0fff0", "loc": "#78e8c8"},
    {"border": "#c87aff", "bg": "rgba(130, 60, 180, 0.5)", "text": "#f0d8ff", "loc": "#dda8ff"},
]


def _logo_data_uri() -> str | None:
    if not LOGO_PATH.exists():
        return None
    try:
        encoded = b64encode(LOGO_PATH.read_bytes()).decode("ascii")
        return f"data:image/png;base64,{encoded}"
    except OSError:
        return None


def _api_base_url() -> str:
    env = os.environ.get("SOUNDTRIP_API_BASE", "").strip()
    if env:
        return env.rstrip("/")
    try:
        sec = st.secrets.get("SOUNDTRIP_API_BASE")
        if sec:
            return str(sec).strip().rstrip("/")
    except (FileNotFoundError, AttributeError, RuntimeError):
        pass
    return "http://127.0.0.1:8001"


def _init_session_state() -> None:
    if "generated_playlist" not in st.session_state:
        st.session_state.generated_playlist = None
    if "playlist_error" not in st.session_state:
        st.session_state.playlist_error = None
    if "playlist_prompt" not in st.session_state:
        st.session_state.playlist_prompt = DEFAULT_PROMPT
    if "load_playlist_id_text" not in st.session_state:
        st.session_state.load_playlist_id_text = ""
    if "active_playlist_id" not in st.session_state:
        st.session_state.active_playlist_id = None
    if "journey_selected_stop_order" not in st.session_state:
        st.session_state.journey_selected_stop_order = None
    if "journey_lens_song_order" not in st.session_state:
        st.session_state.journey_lens_song_order = None


def _resolve_playlist_id(playlist: dict[str, Any] | None = None) -> int | None:
    typed = str(st.session_state.get("load_playlist_id_text") or "").strip()
    if typed.isdigit():
        return int(typed)

    active = st.session_state.get("active_playlist_id")
    if active is not None:
        try:
            return int(active)
        except (TypeError, ValueError):
            pass

    pl = playlist if isinstance(playlist, dict) else st.session_state.get("generated_playlist")
    if isinstance(pl, dict) and pl.get("id") is not None:
        try:
            return int(pl["id"])
        except (TypeError, ValueError):
            pass

    return None


def _set_active_playlist(playlist: dict[str, Any]) -> None:
    st.session_state.generated_playlist = playlist
    pid = playlist.get("id")
    if pid is not None:
        try:
            st.session_state.active_playlist_id = int(pid)
        except (TypeError, ValueError):
            pass
    st.session_state.pop("journey_song_locations", None)


def _load_playlist_for_journey(api_base: str) -> dict[str, Any] | None:
    """Reload playlist via GET so songs include city/country from the API."""
    pid = _resolve_playlist_id()
    if pid is not None:
        try:
            playlist = get_playlist(api_base, pid)
            _set_active_playlist(playlist)
            return playlist
        except SoundTripAPIError:
            pass

    pl = st.session_state.get("generated_playlist")
    if isinstance(pl, dict) and pl.get("songs") is not None:
        return pl
    return None


def _ordered_unique_join(values: list[str]) -> str:
    seen: set[str] = set()
    out: list[str] = []
    for raw in values:
        s = (raw or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return ", ".join(out) if out else EMPTY_SIGNAL


def _top_labels(values: list[str], limit: int) -> list[str]:
    if not values:
        return []
    counts = Counter(values)
    first_seen: dict[str, int] = {}
    for idx, label in enumerate(values):
        first_seen.setdefault(label, idx)
    ranked = sorted(counts.items(), key=lambda item: (-item[1], first_seen[item[0]]))
    return [label for label, _ in ranked[:limit]]


def _aggregates_from_playlist(playlist: dict[str, Any]) -> dict[str, str]:
    songs = playlist.get("songs") or []
    styles: list[str] = []
    emotions: list[str] = []
    times: list[str] = []
    influences: list[str] = []
    geos: list[str] = []
    for song in songs:
        if not isinstance(song, dict):
            continue
        for st_obj in song.get("styles") or []:
            if (
                isinstance(st_obj, dict)
                and str(st_obj.get("role") or "").lower() == "primary"
                and st_obj.get("label")
            ):
                styles.append(str(st_obj["label"]))
                break
        for em in song.get("emotions") or []:
            if not isinstance(em, dict):
                continue
            conf = em.get("confidence")
            conf_val = float(conf) if isinstance(conf, (int, float)) else 0.0
            if em.get("label") and conf_val >= 0.9:
                emotions.append(str(em["label"]))
        tm = song.get("time")
        if isinstance(tm, dict) and tm.get("label"):
            times.append(str(tm["label"]))
        for inf in song.get("influences") or []:
            if not isinstance(inf, dict):
                continue
            conf = inf.get("confidence")
            conf_val = float(conf) if isinstance(conf, (int, float)) else 0.0
            if inf.get("label") and conf_val >= 0.9:
                influences.append(str(inf["label"]))
        g = song.get("geography")
        if isinstance(g, dict):
            p = g.get("primary")
            if isinstance(p, dict) and p.get("label"):
                geos.append(str(p["label"]))
    n = len(songs) if isinstance(songs, list) else 0
    return {
        "style": _ordered_unique_join(_top_labels(styles, 2)),
        "time": _ordered_unique_join(_top_labels(times, 2)),
        "emotion": _ordered_unique_join(_top_labels(emotions, 3)),
        "influence": _ordered_unique_join(_top_labels(influences, 3)),
        "geography": _ordered_unique_join(_top_labels(geos, 2)),
        "songs_requested": str(n) if n else EMPTY_SIGNAL,
    }


def _tags_for_song(song: dict[str, Any]) -> list[str]:
    style_tags: list[tuple[str, str]] = []
    time_tags: list[tuple[str, str]] = []
    emotion_tags: list[tuple[str, str]] = []
    influence_tags: list[tuple[str, str]] = []
    geography_tags: list[tuple[str, str]] = []
    for st_obj in song.get("styles") or []:
        if not isinstance(st_obj, dict):
            continue
        if str(st_obj.get("role") or "").lower() == "primary" and st_obj.get("label"):
            style_tags.append((str(st_obj["label"]), "style"))
            break
    tm = song.get("time")
    if isinstance(tm, dict) and tm.get("label"):
        time_tags.append((str(tm["label"]), "time"))
    for em in song.get("emotions") or []:
        if not isinstance(em, dict):
            continue
        conf = em.get("confidence")
        conf_val = float(conf) if isinstance(conf, (int, float)) else 0.0
        if em.get("label") and conf_val >= 0.9:
            emotion_tags.append((str(em["label"]), "emotion"))
    for inf in song.get("influences") or []:
        if not isinstance(inf, dict):
            continue
        conf = inf.get("confidence")
        conf_val = float(conf) if isinstance(conf, (int, float)) else 0.0
        if inf.get("label") and conf_val >= 0.9:
            influence_tags.append((str(inf["label"]), "influence"))
    g = song.get("geography")
    if isinstance(g, dict):
        p = g.get("primary")
        if isinstance(p, dict) and p.get("label"):
            geography_tags.append((str(p["label"]), "geography"))
    tags = style_tags + time_tags + emotion_tags + influence_tags + geography_tags
    seen: set[str] = set()
    ordered: list[tuple[str, str]] = []
    for label, kind in tags:
        if label not in seen:
            seen.add(label)
            ordered.append((label, kind))
    return ordered


def _primary_style_label(song: dict[str, Any]) -> str:
    for st_obj in song.get("styles") or []:
        if not isinstance(st_obj, dict):
            continue
        if str(st_obj.get("role") or "").lower() == "primary" and st_obj.get("label"):
            return str(st_obj["label"])
    return ""


def _journey_color_index(order: int) -> int:
    return (max(order, 1) - 1) % len(JOURNEY_STOP_COLORS)


def _journey_index_style(order: int, *, mapped: bool) -> str:
    if not mapped:
        return (
            "border-color: rgba(143, 160, 223, 0.35); "
            "background: rgba(27, 36, 64, 0.75); color: #9db1de;"
        )
    c = JOURNEY_STOP_COLORS[_journey_color_index(order)]
    return f"border-color: {c['border']}; background: {c['bg']}; color: {c['text']};"


def _journey_location_color(order: int, *, mapped: bool) -> str:
    if not mapped:
        return "#7f8fb4"
    return JOURNEY_STOP_COLORS[_journey_color_index(order)]["loc"]


def _city_for_journey_path(
    song: dict[str, Any],
    order: int,
    point_by_order: dict[int, JourneyPoint],
) -> str:
    jp = point_by_order.get(order)
    if jp is not None and jp.city and jp.city.strip():
        return jp.city.strip()
    loc = extract_location(song)
    if loc.city and loc.city.strip():
        return loc.city.strip()
    if loc.country and loc.country.strip():
        return loc.country.strip()
    return "Unknown"


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    earth_radius_km = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2.0) ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return earth_radius_km * c


def _adjacency_rows(
    points: list[JourneyPoint],
    lens_order: int,
    *,
    limit: int = 6,
) -> list[dict[str, Any]]:
    """Rank other mapped stops by geographic proximity to the lens stop.

    Returns rows ordered by ascending distance with a heuristic match percent and
    a coarse region label ("same region" within ~250 km or same country, else
    "nearby region").
    """
    selected = next((p for p in points if p.order == lens_order), None)
    if selected is None:
        return []

    others = [p for p in points if p.order != lens_order]
    if not others:
        return []

    rows: list[dict[str, Any]] = []
    for other in others:
        distance = _haversine_km(selected.lat, selected.lng, other.lat, other.lng)
        same_country = bool(
            selected.country
            and other.country
            and selected.country.strip().lower() == other.country.strip().lower()
        )
        same_region = same_country or distance < 250.0
        label = "same region" if same_region else "nearby region"
        if distance <= 50.0:
            percent = 96
        elif distance <= 250.0:
            percent = max(85, 96 - int((distance - 50.0) / 25.0))
        elif distance <= 2000.0:
            percent = max(72, 85 - int((distance - 250.0) / 130.0))
        else:
            percent = max(60, 72 - int((distance - 2000.0) / 1500.0))
        rows.append(
            {
                "order": other.order,
                "distance_km": distance,
                "label": label,
                "percent": percent,
            }
        )

    rows.sort(key=lambda r: r["distance_km"])
    return rows[:limit]


def _match_blurb(song: dict[str, Any]) -> str:
    """Build a short 'Why this match?' sentence from the song's signals."""

    def _first_label(items: Any, *, conf_threshold: float = 0.0) -> str:
        if not isinstance(items, list):
            return ""
        for entry in items:
            if not isinstance(entry, dict):
                continue
            label = entry.get("label")
            if not label:
                continue
            if conf_threshold > 0.0:
                conf = entry.get("confidence")
                conf_val = float(conf) if isinstance(conf, (int, float)) else 0.0
                if conf_val < conf_threshold:
                    continue
            return str(label).strip()
        return ""

    style = _primary_style_label(song)
    time_label = ""
    tm = song.get("time")
    if isinstance(tm, dict) and tm.get("label"):
        time_label = str(tm["label"]).strip()
    emotion = _first_label(song.get("emotions"), conf_threshold=0.9)
    influence = _first_label(song.get("influences"), conf_threshold=0.9)

    parts: list[str] = []
    if style:
        parts.append(f"{style.lower()} style")
    if time_label:
        parts.append(f"{time_label.lower()} era")
    if emotion:
        parts.append(f"{emotion.lower()} emotion")
    if influence:
        parts.append(f"{influence.lower()} roots")

    if not parts:
        return "A distinctive entry in this journey."
    if len(parts) == 1:
        return f"Shares {parts[0]} with the rest of the journey."
    body = ", ".join(parts[:-1])
    return f"Shares {body}, and {parts[-1]} with the rest of the journey."


def _song_id(song: dict[str, Any]) -> int | str | None:
    # Prefer playlist item ids exactly as returned by backend.
    raw = song.get("song_id")
    if raw is None:
        raw = song.get("id")
    if raw is None:
        raw = song.get("songId")
    if raw is None:
        raw = song.get("track_id")
    if raw is None:
        raw = song.get("trackId")
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        trimmed = raw.strip()
        if not trimmed:
            return None
        return int(trimmed) if trimmed.isdigit() else trimmed
    return None


def _song_cover_url(song: dict[str, Any]) -> str:
    direct = str(song.get("album_cover_url") or "").strip()
    if direct:
        return direct
    album_cover = song.get("album_cover")
    if isinstance(album_cover, dict):
        nested = str(album_cover.get("url") or "").strip()
        if nested:
            return nested
    return ""


def _song_hover_tooltip(song: dict[str, Any]) -> str:
    album_obj = song.get("album")
    album_name = ""
    release_year = str(song.get("release_year") or "").strip()
    if isinstance(album_obj, dict):
        album_name = str(album_obj.get("name") or album_obj.get("title") or "").strip()
    elif isinstance(album_obj, str):
        album_name = album_obj.strip()
    album_display = album_name or EMPTY_SIGNAL
    year_display = release_year or EMPTY_SIGNAL
    return f"Album: {album_display}\nRecording Year: {year_display}"


@st.cache_data(show_spinner=False, ttl=3600)
def _cover_data_uri(url: str) -> str | None:
    if not url:
        return None
    try:
        resp = requests.get(
            url,
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        if not resp.ok:
            return None
        content_type = str(resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if not content_type.startswith("image/"):
            return None
        encoded = b64encode(resp.content).decode("ascii")
        return f"data:{content_type};base64,{encoded}"
    except requests.RequestException:
        return None


def inject_styles() -> None:
    st.markdown(
        """
        <style>
            .stApp {
                background:
                    radial-gradient(1200px 550px at 85% 0%, rgba(114, 61, 255, 0.28), transparent 60%),
                    radial-gradient(950px 500px at 15% 15%, rgba(2, 84, 208, 0.24), transparent 58%),
                    linear-gradient(180deg, #060816 0%, #090d1f 45%, #070914 100%);
                color: #eaf2ff;
                font-family: "Inter", "Segoe UI", sans-serif;
            }

            .block-container {
                max-width: 1220px;
                padding-top: 1.2rem;
                padding-bottom: 1.3rem;
            }

            header[data-testid="stHeader"] {
                display: none;
            }

            .top-nav {
                margin: 0 0 1.15rem 0;
                border: 1px solid rgba(116, 132, 196, 0.2);
                border-radius: 14px;
                background: rgba(8, 13, 27, 0.92);
                min-height: 66px;
                display: flex;
                align-items: center;
                justify-content: space-between;
                padding: 0.6rem 1rem;
                box-shadow: 0 8px 20px rgba(0, 0, 0, 0.35);
            }

            .top-nav-left {
                display: flex;
                align-items: center;
                gap: 0.62rem;
            }

            .top-nav-logo {
                width: 95px;
                height: 95px;
                border-radius: 999px;
                object-fit: cover;
                border: 1px solid rgba(169, 138, 255, 0.42);
                box-shadow: 0 0 18px rgba(170, 101, 255, 0.58);
            }

            .top-nav-name {
                color: #f7f9ff;
                font-weight: 600;
                font-size: 1.65rem;
                letter-spacing: -0.01em;
            }

            div[data-testid="stTabs"] {
                margin-top: -0.35rem;
                margin-bottom: 0.85rem;
            }

            div[data-testid="stTabs"] [role="tablist"] {
                gap: 0.6rem;
            }

            div[data-testid="stTabs"] [role="tab"] {
                border: 1px solid rgba(133, 149, 215, 0.18);
                border-radius: 10px;
                background: rgba(17, 22, 40, 0.7);
                color: #b7c4e2;
                min-height: 44px;
                padding: 0 0.95rem;
            }

            div[data-testid="stTabs"] [role="tab"][aria-selected="true"] {
                color: #efe5ff;
                border-color: rgba(164, 126, 255, 0.68);
                background: linear-gradient(90deg, rgba(89, 72, 189, 0.7), rgba(128, 63, 201, 0.72));
                box-shadow: 0 0 14px rgba(144, 82, 255, 0.33);
            }

            div[data-testid="stTabs"] [role="tab"]::before,
            div[data-testid="stTabs"] [role="tab"]::after {
                display: none !important;
                border-bottom: none !important;
                box-shadow: none !important;
            }

            .hero-title {
                font-size: 3rem;
                font-weight: 650;
                line-height: 1.1;
                margin: 0.15rem 0 0.4rem 0;
                letter-spacing: -0.01em;
                color: #f9fbff;
            }

            .hero-sub {
                color: #b3c0de;
                font-size: 1.2rem;
                margin-bottom: 0.95rem;
            }

            .load-playlist-label {
                color: #7f8fb4;
                font-size: 0.83rem;
                margin-top: 0.3rem;
                white-space: nowrap;
            }

            div[data-testid="stTextInput"] input {
                min-height: 2rem;
                height: 2rem;
                font-size: 0.9rem;
                padding-top: 0.2rem;
                padding-bottom: 0.2rem;
            }

            .generated-playlist-spacer {
                height: 1rem;
            }

            .main-card, .signals-card, .playlist-card {
                border-radius: 18px;
                border: 1px solid rgba(130, 149, 210, 0.18);
                background: linear-gradient(180deg, rgba(15, 21, 42, 0.86) 0%, rgba(10, 14, 30, 0.9) 100%);
                box-shadow: 0 8px 26px rgba(0, 0, 0, 0.45);
            }

            .main-card {
                padding: 1rem;
                margin-bottom: 0.9rem;
            }

            .chip-row {
                display: flex;
                gap: 0.55rem;
                flex-wrap: wrap;
                margin: 0.8rem 0 0.35rem 0;
            }

            .chip {
                border: 1px solid rgba(134, 149, 226, 0.22);
                border-radius: 13px;
                padding: 0.45rem 0.62rem;
                min-width: 140px;
                background: rgba(13, 18, 35, 0.92);
            }

            .chip.style { border-color: rgba(158, 126, 255, 0.42); background: rgba(56, 36, 90, 0.32); }
            .chip.time { border-color: rgba(112, 189, 255, 0.4); background: rgba(30, 58, 101, 0.33); }
            .chip.emotion { border-color: rgba(255, 120, 170, 0.42); background: rgba(96, 37, 71, 0.34); }
            .chip.influence { border-color: rgba(95, 225, 196, 0.44); background: rgba(28, 82, 72, 0.32); }
            .chip.geography { border-color: rgba(255, 200, 102, 0.4); background: rgba(96, 69, 22, 0.33); }
            .chip.songs { border-color: rgba(255, 170, 110, 0.36); background: rgba(92, 46, 20, 0.32); }

            .chip .k {
                color: #9eb2e7;
                font-size: 0.72rem;
                margin-bottom: 0.13rem;
                display: flex;
                align-items: center;
                gap: 0.35rem;
            }

            .chip .v {
                color: #e8efff;
                font-size: 0.83rem;
                line-height: 1.2;
            }

            .section-title {
                color: #eef4ff;
                margin: 1rem 0 0.62rem;
                font-size: 1.02rem;
                font-weight: 600;
            }

            .song-row {
                border-radius: 12px;
                border: 1px solid rgba(130, 149, 210, 0.17);
                background: rgba(13, 19, 36, 0.95);
                display: grid;
                grid-template-columns: 32px 42px 1fr;
                align-items: center;
                padding: 0.55rem 0.8rem;
                margin-bottom: 0.42rem;
                gap: 0.55rem;
            }

            .song-num {
                color: #8ea1ce;
                font-size: 0.86rem;
                text-align: center;
            }

            .song-title {
                color: #f1f5ff;
                font-weight: 600;
                font-size: 0.95rem;
                margin-bottom: 0.1rem;
            }

            .song-cover {
                width: 38px;
                height: 38px;
                border-radius: 7px;
                object-fit: cover;
                border: 1px solid rgba(130, 149, 210, 0.28);
                background: rgba(20, 27, 48, 0.9);
            }

            .song-cover-empty {
                width: 38px;
                height: 38px;
                border-radius: 7px;
                border: 1px solid rgba(130, 149, 210, 0.18);
                background: rgba(20, 27, 48, 0.45);
            }

            .song-artist {
                color: #9db1dd;
                font-size: 0.8rem;
            }

            .song-tags {
                margin-top: 0.25rem;
                display: flex;
                gap: 0.35rem;
                flex-wrap: wrap;
            }

            .tag {
                border: 1px solid rgba(131, 146, 214, 0.2);
                background: rgba(27, 36, 64, 0.75);
                border-radius: 999px;
                color: #c8d6f5;
                font-size: 0.66rem;
                padding: 0.16rem 0.42rem;
            }

            .tag-style {
                border-color: rgba(163, 126, 255, 0.45);
                background: rgba(56, 38, 95, 0.55);
                color: #d8c7ff;
            }
            .tag-time {
                border-color: rgba(109, 190, 255, 0.45);
                background: rgba(31, 59, 101, 0.55);
                color: #b8e1ff;
            }
            .tag-emotion {
                border-color: rgba(255, 126, 174, 0.45);
                background: rgba(95, 36, 71, 0.55);
                color: #ffc6dc;
            }
            .tag-influence {
                border-color: rgba(97, 227, 201, 0.45);
                background: rgba(29, 82, 72, 0.52);
                color: #b8f5e7;
            }
            .tag-geography {
                border-color: rgba(255, 205, 116, 0.45);
                background: rgba(96, 69, 20, 0.52);
                color: #ffe1b2;
            }

            .play-btn {
                width: 32px;
                height: 32px;
                border-radius: 999px;
                border: 1px solid rgba(150, 162, 230, 0.35);
                display: flex;
                align-items: center;
                justify-content: center;
                color: #dde6ff;
                background: rgba(28, 34, 60, 0.9);
                font-size: 0.8rem;
            }

            .signals-card {
                padding: 1rem 1.05rem;
                height: 100%;
            }

            .signals-title {
                color: #f4f8ff;
                font-weight: 700;
                margin-bottom: 0.5rem;
                font-size: 1.03rem;
            }

            .signal-row {
                border-top: 1px solid rgba(143, 160, 223, 0.15);
                padding-top: 0.52rem;
                margin-top: 0.52rem;
            }

            .signal-label {
                color: #9db1de;
                font-size: 0.78rem;
            }

            .signal-value {
                color: #edf4ff;
                font-size: 0.86rem;
                margin-top: 0.14rem;
            }

            .wave {
                margin-top: 0.9rem;
                height: 42px;
                border-radius: 10px;
                background:
                    radial-gradient(70% 120% at 30% 30%, rgba(163, 92, 255, 0.22), transparent),
                    radial-gradient(80% 140% at 65% 70%, rgba(101, 157, 255, 0.2), transparent),
                    rgba(9, 14, 29, 0.88);
                border: 1px solid rgba(142, 155, 222, 0.16);
                display: flex;
                align-items: center;
                justify-content: center;
                color: #a9bcf0;
                font-size: 0.75rem;
            }

            .stTextArea textarea {
                border-radius: 13px !important;
                border: 1px solid rgba(133, 149, 220, 0.36) !important;
                color: #ecf2ff !important;
                background: rgba(11, 17, 34, 0.95) !important;
                min-height: 170px !important;
                font-size: 1rem !important;
            }

            .stTextArea label {
                display: none;
            }

            div[data-testid="stButton"] button[kind="primary"] {
                width: 100%;
                border: none;
                border-radius: 12px;
                min-height: 3.15rem;
                font-weight: 600;
                color: #f8f7ff;
                background: linear-gradient(90deg, #6750ff 0%, #b146ff 100%);
                box-shadow: 0 0 18px rgba(125, 88, 255, 0.46);
            }

            div[data-testid="stButton"] button[kind="primary"]:hover {
                background: linear-gradient(90deg, #7664ff 0%, #c85aff 100%);
            }

            div[data-testid="stButton"] button[kind="secondary"] {
                border-radius: 8px;
                min-height: 1.65rem;
                height: 1.65rem;
                padding: 0 0.35rem;
                font-size: 0.74rem;
                line-height: 1;
                background: rgba(21, 29, 53, 0.92);
                border: 1px solid rgba(123, 140, 196, 0.38);
                box-shadow: none;
            }

            .journey-empty {
                border-radius: 16px;
                border: 1px dashed rgba(143, 160, 223, 0.28);
                background: rgba(12, 18, 36, 0.75);
                padding: 2.5rem 1.5rem;
                text-align: center;
                color: #b3c0de;
                font-size: 1.05rem;
            }

            .journey-empty strong {
                color: #efe5ff;
            }

            .journey-hero-title {
                font-family: Georgia, "Times New Roman", serif;
                font-size: 2.65rem;
                font-weight: 600;
                line-height: 1.12;
                margin: 0.1rem 0 0.35rem 0;
                letter-spacing: -0.02em;
                color: #f9fbff;
            }

            .journey-hero-title .journey-gradient {
                background: linear-gradient(90deg, #b89cff 0%, #ff8ec8 55%, #7ec8ff 100%);
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
                background-clip: text;
            }

            .journey-hero-sub {
                color: #9db1de;
                font-size: 1.05rem;
                margin: 0 0 1rem 0;
                line-height: 1.45;
            }

            .journey-chips {
                margin-bottom: 1.1rem;
            }

            .journey-panel {
                border-radius: 18px;
                border: 1px solid rgba(130, 149, 210, 0.18);
                background: linear-gradient(180deg, rgba(15, 21, 42, 0.86) 0%, rgba(10, 14, 30, 0.9) 100%);
                box-shadow: 0 8px 26px rgba(0, 0, 0, 0.45);
                padding: 1rem 1.05rem;
                max-height: 620px;
                overflow-y: auto;
            }

            .journey-panel-title,
            .route-stops-header {
                color: #f4f8ff;
                font-weight: 700;
                font-size: 1.08rem;
                margin-bottom: 0.65rem;
                display: flex;
                align-items: center;
                gap: 0.4rem;
            }

            .route-stops-icon {
                color: #ff9a6a;
                font-size: 1rem;
            }

            .journey-panel-footer {
                margin-top: 0.85rem;
                padding-top: 0.65rem;
                border-top: 1px solid rgba(143, 160, 223, 0.15);
                color: #9db1de;
                font-size: 0.78rem;
            }

            .journey-stop {
                display: flex;
                gap: 0.55rem;
                align-items: center;
                padding: 0.45rem 0;
            }

            .journey-stop-genre {
                flex-shrink: 0;
                align-self: center;
                max-width: 5.5rem;
                padding: 0.2rem 0.45rem;
                border-radius: 999px;
                border: 1px solid rgba(158, 126, 255, 0.45);
                background: rgba(56, 36, 90, 0.4);
                color: #d8c8ff;
                font-size: 0.62rem;
                line-height: 1.2;
                text-align: center;
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
            }

            .journey-index {
                flex-shrink: 0;
                width: 26px;
                height: 26px;
                border-radius: 999px;
                border: 1px solid rgba(255, 200, 102, 0.45);
                background: rgba(96, 69, 22, 0.45);
                color: #ffe1b2;
                font-size: 0.78rem;
                font-weight: 700;
                display: flex;
                align-items: center;
                justify-content: center;
            }

            .journey-index.unmapped {
                border-color: rgba(143, 160, 223, 0.35);
                background: rgba(27, 36, 64, 0.75);
                color: #9db1de;
            }

            .journey-stop-body {
                flex: 1;
                min-width: 0;
            }

            .journey-stop-title {
                color: #f1f5ff;
                font-weight: 600;
                font-size: 0.9rem;
                line-height: 1.25;
            }

            .journey-stop-artist {
                color: #9db1dd;
                font-size: 0.78rem;
                margin-top: 0.08rem;
            }

            .journey-stop-location {
                color: #ffc86a;
                font-size: 0.74rem;
                margin-top: 0.2rem;
            }

            .journey-stop-location.muted {
                color: #7f8fb4;
            }

            .journey-connector {
                margin: 0 0 0 12px;
                width: 2px;
                height: 18px;
                background: linear-gradient(180deg, rgba(163, 126, 255, 0.7), rgba(255, 200, 102, 0.5));
                border-radius: 2px;
            }

            .journey-map-note {
                color: #9db1de;
                font-size: 0.82rem;
                margin-bottom: 0.45rem;
            }

            .journey-map-wrap {
                border-radius: 14px;
                border: 1px solid rgba(130, 149, 210, 0.22);
                overflow: hidden;
                box-shadow: 0 8px 24px rgba(0, 0, 0, 0.35);
            }

            .journey-stop-cover-wrap {
                flex-shrink: 0;
            }

            .journey-stop-cover {
                width: 40px;
                height: 40px;
                border-radius: 6px;
                object-fit: cover;
                border: 1px solid rgba(130, 149, 210, 0.28);
                display: block;
            }

            .journey-stop-cover-empty {
                width: 40px;
                height: 40px;
                border-radius: 6px;
                border: 1px solid rgba(130, 149, 210, 0.18);
                background: rgba(20, 27, 48, 0.45);
            }

            .journey-path-bar {
                margin-top: 1rem;
                border-radius: 16px;
                border: 1px solid rgba(130, 149, 210, 0.2);
                background: linear-gradient(180deg, rgba(15, 21, 42, 0.9) 0%, rgba(10, 14, 30, 0.94) 100%);
                box-shadow: 0 8px 24px rgba(0, 0, 0, 0.35);
                padding: 0.85rem 1.1rem;
            }

            .journey-path-title {
                color: #f4f8ff;
                font-weight: 700;
                font-size: 1rem;
                margin-bottom: 0.55rem;
                display: flex;
                align-items: center;
                gap: 0.35rem;
            }

            .journey-path-flow {
                display: flex;
                flex-wrap: wrap;
                align-items: center;
                gap: 0.35rem 0.5rem;
            }

            .journey-path-step {
                display: inline-flex;
                align-items: center;
                gap: 0.35rem;
                font-size: 0.88rem;
                color: #edf4ff;
            }

            .journey-path-index {
                width: 22px;
                height: 22px;
                border-radius: 999px;
                border: 1px solid rgba(255, 200, 102, 0.45);
                font-size: 0.72rem;
                font-weight: 700;
                display: inline-flex;
                align-items: center;
                justify-content: center;
                flex-shrink: 0;
            }

            .journey-path-city {
                font-weight: 500;
            }

            .journey-path-arrow {
                color: #7f8fb4;
                font-size: 0.85rem;
                margin: 0 0.1rem;
            }

            .route-stops-note {
                margin-top: 0.65rem;
                padding-top: 0.55rem;
                border-top: 1px solid rgba(143, 160, 223, 0.12);
                color: #7f8fb4;
                font-size: 0.74rem;
            }

            .journey-stop.selected {
                border-radius: 12px;
                background: linear-gradient(180deg, rgba(78, 52, 168, 0.32) 0%, rgba(48, 30, 110, 0.28) 100%);
                box-shadow: 0 0 0 1px rgba(184, 156, 255, 0.55), 0 0 18px rgba(155, 122, 255, 0.32);
                padding: 0.55rem 0.6rem;
            }

            div[data-testid="stButton"] button[aria-label="Select"],
            div[data-testid="stButton"] button[aria-label="Selected"] {
                width: 100%;
                min-height: 1.65rem;
                height: 1.65rem;
                padding: 0 0.4rem;
                font-size: 0.7rem;
                line-height: 1;
                color: #cdd6ef;
                background: rgba(21, 29, 53, 0.85);
                border: 1px solid rgba(123, 140, 196, 0.32);
                border-radius: 8px;
                box-shadow: none;
            }

            div[data-testid="stButton"] button[aria-label="Selected"] {
                color: #efe5ff;
                background: linear-gradient(90deg, rgba(89, 72, 189, 0.55), rgba(128, 63, 201, 0.55));
                border-color: rgba(184, 156, 255, 0.55);
            }

            div[data-testid="stButton"]:has(button[aria-label="✦"]) {
                display: flex;
                justify-content: center;
                align-items: center;
            }

            div[data-testid="stButton"] button[aria-label="✦"] {
                display: inline-flex;
                align-items: center;
                justify-content: center;
                width: 2.4rem;
                min-width: 2.4rem;
                max-width: 2.4rem;
                height: 2.4rem;
                min-height: 2.4rem;
                padding: 0;
                margin: 0;
                font-size: 1.05rem;
                line-height: 1;
                color: #efe5ff;
                background: linear-gradient(135deg, rgba(103, 80, 255, 0.9) 0%, rgba(177, 70, 255, 0.9) 100%);
                border: 1px solid rgba(184, 156, 255, 0.55);
                border-radius: 10px;
                box-shadow: 0 0 10px rgba(155, 122, 255, 0.35);
                transition: transform 0.12s ease, box-shadow 0.12s ease, filter 0.12s ease;
            }

            div[data-testid="stButton"] button[aria-label="✦"] p,
            div[data-testid="stButton"] button[aria-label="✦"] div {
                margin: 0 !important;
                padding: 0 !important;
                line-height: 1 !important;
            }

            div[data-testid="stButton"] button[aria-label="✦"]:hover {
                transform: translateY(-1px);
                box-shadow: 0 0 14px rgba(184, 156, 255, 0.65);
                filter: brightness(1.08);
            }

            div[data-testid="stButton"] button[aria-label="✦"]:active {
                transform: translateY(0);
                filter: brightness(0.95);
            }

            div[data-testid="stButton"] button[aria-label="← Back to Journey"] {
                min-height: 1.95rem;
                height: 1.95rem;
                padding: 0 0.8rem;
                font-size: 0.8rem;
                color: #cdd6ef;
                background: rgba(15, 21, 42, 0.85);
                border: 1px solid rgba(123, 140, 196, 0.35);
                border-radius: 8px;
                box-shadow: none;
            }

            .lens-filter-row {
                display: flex;
                gap: 0.5rem;
                margin: 0.1rem 0 0.85rem 0;
                flex-wrap: wrap;
            }

            .lens-filter-chip {
                display: inline-flex;
                align-items: center;
                gap: 0.35rem;
                border: 1px solid rgba(133, 149, 215, 0.22);
                border-radius: 999px;
                background: rgba(17, 22, 40, 0.75);
                color: #b7c4e2;
                padding: 0.42rem 0.95rem;
                font-size: 0.85rem;
                line-height: 1;
            }

            .lens-filter-chip.active {
                color: #efe5ff;
                border-color: rgba(184, 156, 255, 0.7);
                background: linear-gradient(90deg, rgba(89, 72, 189, 0.55), rgba(128, 63, 201, 0.55));
                box-shadow: 0 0 14px rgba(144, 82, 255, 0.3);
            }

            .lens-filter-chip.disabled {
                opacity: 0.6;
            }

            .lens-selected-card {
                border-radius: 18px;
                border: 1px solid rgba(184, 156, 255, 0.32);
                background: linear-gradient(180deg, rgba(33, 22, 72, 0.78) 0%, rgba(15, 12, 38, 0.86) 100%);
                box-shadow: 0 10px 28px rgba(0, 0, 0, 0.45);
                padding: 1rem 1.05rem;
                margin-bottom: 0.9rem;
            }

            .lens-selected-header {
                color: #efe5ff;
                font-weight: 700;
                font-size: 0.95rem;
                margin-bottom: 0.75rem;
                display: flex;
                align-items: center;
                gap: 0.4rem;
            }

            .lens-selected-top {
                display: grid;
                grid-template-columns: 96px 1fr;
                gap: 0.85rem;
                align-items: flex-start;
                margin-bottom: 0.85rem;
            }

            .lens-selected-cover,
            .lens-selected-cover-empty {
                width: 96px;
                height: 96px;
                border-radius: 10px;
                object-fit: cover;
                border: 1px solid rgba(184, 156, 255, 0.4);
                box-shadow: 0 0 14px rgba(155, 122, 255, 0.35);
                background: rgba(20, 27, 48, 0.85);
            }

            .lens-selected-title {
                color: #f6f2ff;
                font-weight: 700;
                font-size: 1.08rem;
                line-height: 1.2;
            }

            .lens-selected-artist {
                color: #b9c4e6;
                font-size: 0.84rem;
                margin-top: 0.18rem;
            }

            .lens-selected-pills {
                display: flex;
                gap: 0.35rem;
                margin: 0.55rem 0 0.35rem 0;
                flex-wrap: wrap;
            }

            .lens-selected-pill {
                border: 1px solid rgba(131, 146, 214, 0.3);
                background: rgba(27, 36, 64, 0.75);
                border-radius: 999px;
                color: #c8d6f5;
                font-size: 0.7rem;
                padding: 0.16rem 0.5rem;
            }

            .lens-selected-pill.year {
                border-color: rgba(109, 190, 255, 0.45);
                background: rgba(31, 59, 101, 0.55);
                color: #b8e1ff;
            }

            .lens-selected-pill.style {
                border-color: rgba(163, 126, 255, 0.5);
                background: rgba(56, 38, 95, 0.55);
                color: #d8c7ff;
            }

            .lens-selected-location {
                color: #ffc86a;
                font-size: 0.82rem;
                margin-top: 0.25rem;
            }

            .lens-meta-grid {
                display: flex;
                flex-direction: column;
                gap: 0.5rem;
                border-top: 1px solid rgba(143, 160, 223, 0.15);
                padding-top: 0.75rem;
            }

            .lens-meta-row {
                display: grid;
                grid-template-columns: 26px 90px 1fr;
                align-items: center;
                gap: 0.5rem;
            }

            .lens-meta-icon {
                color: #b89cff;
                font-size: 1rem;
                text-align: center;
            }

            .lens-meta-label {
                color: #9db1de;
                font-size: 0.78rem;
            }

            .lens-meta-value {
                color: #edf4ff;
                font-size: 0.84rem;
                line-height: 1.3;
            }

            .lens-match-blurb {
                margin-top: 0.85rem;
                padding-top: 0.7rem;
                border-top: 1px solid rgba(143, 160, 223, 0.15);
                color: #cdd6ef;
                font-size: 0.83rem;
                line-height: 1.45;
            }

            .lens-match-blurb-title {
                color: #efe5ff;
                font-weight: 600;
                margin-bottom: 0.3rem;
                display: flex;
                align-items: center;
                gap: 0.35rem;
            }

            .lens-adjacent-panel {
                border-radius: 18px;
                border: 1px solid rgba(130, 149, 210, 0.18);
                background: linear-gradient(180deg, rgba(15, 21, 42, 0.86) 0%, rgba(10, 14, 30, 0.9) 100%);
                box-shadow: 0 8px 26px rgba(0, 0, 0, 0.45);
                padding: 1rem 1.05rem;
            }

            .lens-adjacent-header {
                color: #f4f8ff;
                font-weight: 700;
                font-size: 1rem;
                margin-bottom: 0.7rem;
                display: flex;
                align-items: center;
                gap: 0.4rem;
            }

            .lens-adjacent-row {
                display: grid;
                grid-template-columns: 22px 38px 1fr auto auto;
                gap: 0.55rem;
                align-items: center;
                padding: 0.42rem 0;
                border-bottom: 1px solid rgba(143, 160, 223, 0.1);
            }

            .lens-adjacent-row:last-of-type {
                border-bottom: none;
            }

            .lens-adjacent-rank {
                color: #9db1de;
                font-size: 0.78rem;
                text-align: center;
            }

            .lens-adjacent-cover,
            .lens-adjacent-cover-empty {
                width: 38px;
                height: 38px;
                border-radius: 6px;
                object-fit: cover;
                border: 1px solid rgba(130, 149, 210, 0.28);
                background: rgba(20, 27, 48, 0.85);
            }

            .lens-adjacent-title {
                color: #f1f5ff;
                font-weight: 600;
                font-size: 0.85rem;
                line-height: 1.15;
            }

            .lens-adjacent-artist {
                color: #9db1dd;
                font-size: 0.74rem;
            }

            .lens-adjacent-location {
                color: #9db1dd;
                font-size: 0.7rem;
                margin-top: 0.05rem;
            }

            .lens-region-pill {
                border-radius: 999px;
                font-size: 0.65rem;
                padding: 0.18rem 0.45rem;
                white-space: nowrap;
            }

            .lens-region-pill.same {
                border: 1px solid rgba(97, 227, 201, 0.45);
                background: rgba(29, 82, 72, 0.55);
                color: #b8f5e7;
            }

            .lens-region-pill.nearby {
                border: 1px solid rgba(255, 205, 116, 0.45);
                background: rgba(96, 69, 20, 0.55);
                color: #ffe1b2;
            }

            .lens-adjacent-percent {
                color: #cdd6ef;
                font-size: 0.78rem;
                font-weight: 600;
                min-width: 36px;
                text-align: right;
            }

            .lens-adjacent-footer {
                margin-top: 0.65rem;
                padding-top: 0.55rem;
                border-top: 1px solid rgba(143, 160, 223, 0.12);
                color: #b89cff;
                font-size: 0.78rem;
                text-align: center;
                cursor: pointer;
            }

            .journey-path-step.selected {
                border: 1px solid rgba(184, 156, 255, 0.7);
                border-radius: 999px;
                padding: 0.15rem 0.55rem 0.15rem 0.2rem;
                background: linear-gradient(90deg, rgba(89, 72, 189, 0.45), rgba(128, 63, 201, 0.45));
                box-shadow: 0 0 12px rgba(155, 122, 255, 0.45);
            }

            .journey-path-step.selected .journey-path-city {
                color: #efe5ff !important;
            }

            div[data-testid="stButton"] button[aria-label="✦ Explore Similar Stops"] {
                min-height: 2.2rem;
                height: 2.2rem;
                padding: 0 0.95rem;
                font-size: 0.85rem;
                color: #efe5ff;
                background: linear-gradient(90deg, rgba(103, 80, 255, 0.85) 0%, rgba(177, 70, 255, 0.85) 100%);
                border: 1px solid rgba(184, 156, 255, 0.5);
                border-radius: 10px;
                box-shadow: 0 0 14px rgba(155, 122, 255, 0.35);
            }

            div[data-testid="stVerticalBlockBorderWrapper"]:has(.route-stops-header) {
                border-color: rgba(130, 149, 210, 0.18) !important;
                border-radius: 18px !important;
                background: linear-gradient(180deg, rgba(15, 21, 42, 0.86) 0%, rgba(10, 14, 30, 0.9) 100%) !important;
                box-shadow: 0 8px 26px rgba(0, 0, 0, 0.45) !important;
                max-height: 640px;
                overflow-y: auto;
            }

            div[data-testid="stVerticalBlockBorderWrapper"]:has(.lens-selected-marker),
            div[data-testid="stVerticalBlockBorderWrapper"]:has(.lens-adjacent-marker) {
                border-color: rgba(184, 156, 255, 0.32) !important;
                border-radius: 18px !important;
                background: linear-gradient(180deg, rgba(33, 22, 72, 0.7) 0%, rgba(15, 12, 38, 0.86) 100%) !important;
                box-shadow: 0 10px 28px rgba(0, 0, 0, 0.45) !important;
            }

            div[data-testid="stVerticalBlockBorderWrapper"]:has(.lens-adjacent-marker) {
                background: linear-gradient(180deg, rgba(15, 21, 42, 0.86) 0%, rgba(10, 14, 30, 0.9) 100%) !important;
                border-color: rgba(130, 149, 210, 0.18) !important;
            }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _chip(k: str, v: str) -> str:
    icon, kind = PANEL_META.get(k, ("•", "default"))
    return (
        f'<div class="chip {kind}"><div class="k"><span>{icon}</span><span>{html.escape(k)}</span></div>'
        f'<div class="v">{html.escape(v)}</div></div>'
    )


def _signal_row(label: str, value: str) -> str:
    return (
        f'<div class="signal-row">'
        f'<div class="signal-label">{html.escape(label)}</div>'
        f'<div class="signal-value">{html.escape(value)}</div>'
        f"</div>"
    )


def render_top_banner() -> None:
    logo_uri = _logo_data_uri()
    logo_html = (
        f'<img class="top-nav-logo" src="{logo_uri}" alt="Song Journey logo" />'
        if logo_uri
        else '<div class="top-nav-logo"></div>'
    )
    st.markdown(
        f"""
        <div class="top-nav">
            <div class="top-nav-left">
                {logo_html}
                <div class="top-nav-name">Song Journey</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_chips(agg: dict[str, str]) -> None:
    inner = "".join(
        [
            _chip("Style", agg["style"]),
            _chip("Time", agg["time"]),
            _chip("Emotion", agg["emotion"]),
            _chip("Influence", agg["influence"]),
            _chip("Geography", agg["geography"]),
            _chip("Songs Requested", agg["songs_requested"]),
        ]
    )
    st.markdown(f'<div class="chip-row">{inner}</div>', unsafe_allow_html=True)


def render_journey_header() -> None:
    st.markdown(
        """
        <div class="journey-hero-title">
            Follow your playlist <span class="journey-gradient">journey</span>
        </div>
        <div class="journey-hero-sub">
            Trace how your playlist moves across cities, scenes, and continents.
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_journey_chips(agg: dict[str, str]) -> None:
    inner = "".join(
        [
            _chip("Style", agg["style"]),
            _chip("Time", agg["time"]),
            _chip("Emotion", agg["emotion"]),
            _chip("Influence", agg["influence"]),
            _chip("Songs", agg["songs_requested"]),
        ]
    )
    st.markdown(f'<div class="chip-row journey-chips">{inner}</div>', unsafe_allow_html=True)


def render_playlist_songs(playlist: dict[str, Any]) -> None:
    songs = playlist.get("songs") or []
    for index, song in enumerate(songs, start=1):
        if not isinstance(song, dict):
            continue
        sid = _song_id(song)
        title = str(song.get("title") or "Untitled")
        artist = str(song.get("artist") or "")
        tooltip = _song_hover_tooltip(song)
        cover_url = _song_cover_url(song)
        cover_src = _cover_data_uri(cover_url) or cover_url
        tag_list = _tags_for_song(song)
        tags_html = "".join(
            f'<span class="tag tag-{html.escape(kind)}">{html.escape(label)}</span>' for label, kind in tag_list
        )
        content_col, actions_col = st.columns([9.65, 0.85], vertical_alignment="center")
        with content_col:
            num_col, cover_col, info_col = st.columns([0.55, 0.85, 8.25], vertical_alignment="center")
            with num_col:
                st.markdown(f'<div class="song-num">{index}</div>', unsafe_allow_html=True)
            with cover_col:
                if cover_src:
                    st.image(cover_src, width=38)
                else:
                    st.markdown('<div class="song-cover-empty"></div>', unsafe_allow_html=True)
            with info_col:
                st.markdown(
                    f"""
                    <div title="{html.escape(tooltip)}">
                        <div class="song-title">{html.escape(title)}</div>
                        <div class="song-artist">{html.escape(artist)}</div>
                        <div class="song-tags">{tags_html}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
        with actions_col:
            meta_col, play_col = st.columns([1, 1])
            with meta_col:
                metadata_clicked = st.button(
                    "ⓘ",
                    key=f"song_metadata_{sid}_{index}",
                    help="Fetch metadata for this song",
                    use_container_width=True,
                    type="secondary",
                )
            with play_col:
                st.button(
                    "▷",
                    key=f"song_play_{sid}_{index}",
                    disabled=True,
                    use_container_width=True,
                    type="secondary",
                )

        if metadata_clicked:
            sid = _song_id(song)
            if sid is None:
                st.session_state.playlist_error = "Song id not found for this row."
            else:
                try:
                    result = apply_song_metadata(
                        _api_base_url(),
                        sid,
                        auto=True,
                        overwrite=True,
                        min_score=60,
                    )
                    updated_song = result.get("song") if isinstance(result.get("song"), dict) else result
                    if isinstance(updated_song, dict):
                        merged = dict(song)
                        merged.update(updated_song)
                        songs[index - 1] = merged
                        if isinstance(st.session_state.generated_playlist, dict):
                            st.session_state.generated_playlist["songs"] = songs
                    st.toast("Song metadata updated")
                except SoundTripAPIError as exc:
                    st.session_state.playlist_error = str(exc)
                except requests.RequestException as exc:
                    st.session_state.playlist_error = f"Network error: {exc}"


MAP_STYLE_CARTO_DARK = "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json"
MAP_STYLE_CARTO_VOYAGER = "https://basemaps.cartocdn.com/gl/voyager-gl-style/style.json"
MAX_VISIBLE_MAP_LABELS = 5
LABEL_LINE_HEIGHT_PX = 16
LABEL_BASE_OFFSET_PX = 28


def render_world_map(points: list[JourneyPoint]) -> None:
    if not points:
        st.info(
            "No songs could be placed on the map. Load a playlist by id on Discover "
            "(songs need city and country from the API), then reopen Journey."
        )
        return

    clusters = build_map_clusters(points)
    clusters_ordered = clusters_in_playlist_order(points, clusters)

    scatter_rows = [
        {
            "lon": c.lng,
            "lat": c.lat,
            "tooltip": cluster_tooltip_html(c),
            # pydeck get_radius is in meters; pixel size is clamped separately.
            "radius_m": 55_000 + min(c.song_count - 1, 6) * 6_000,
            "radius_min_px": min(20, 10 + 2 * (c.song_count - 1)),
            "radius_max_px": min(26, 14 + 2 * (c.song_count - 1)),
        }
        for c in clusters
    ]
    scatter_df = pd.DataFrame(scatter_rows)

    text_rows: list[dict[str, Any]] = []
    for cluster in clusters:
        visible = cluster.songs[:MAX_VISIBLE_MAP_LABELS]
        for i, song in enumerate(visible):
            title = song.title if len(song.title) <= 32 else f"{song.title[:31]}…"
            text_rows.append(
                {
                    "lon": cluster.lng,
                    "lat": cluster.lat,
                    "text": f"{song.order}. {title}",
                    "offset_y": -(LABEL_BASE_OFFSET_PX + i * LABEL_LINE_HEIGHT_PX),
                }
            )
        remaining = len(cluster.songs) - len(visible)
        if remaining > 0:
            i = len(visible)
            text_rows.append(
                {
                    "lon": cluster.lng,
                    "lat": cluster.lat,
                    "text": f"+{remaining} more",
                    "offset_y": -(LABEL_BASE_OFFSET_PX + i * LABEL_LINE_HEIGHT_PX),
                }
            )

    text_df = pd.DataFrame(text_rows) if text_rows else pd.DataFrame(columns=["lon", "lat", "text", "offset_y"])

    path_data = [{"path": seg} for seg in build_cluster_path_segments(clusters_ordered)]
    layers: list[pdk.Layer] = []

    if path_data:
        layers.append(
            pdk.Layer(
                "PathLayer",
                data=path_data,
                get_path="path",
                get_color=[163, 126, 255, 175],
                get_width=3,
                width_min_pixels=2,
                pickable=False,
            )
        )

    layers.append(
        pdk.Layer(
            "ScatterplotLayer",
            data=scatter_df,
            get_position="[lon, lat]",
            get_fill_color=[255, 200, 102, 220],
            get_line_color=[255, 255, 255, 180],
            get_radius="radius_m",
            radius_min_pixels="radius_min_px",
            radius_max_pixels="radius_max_px",
            line_width_min_pixels=1,
            pickable=True,
        )
    )

    if not text_df.empty:
        font_size = 12 if any(c.song_count > 3 for c in clusters) else 13
        layers.append(
            pdk.Layer(
                "TextLayer",
                data=text_df,
                get_position="[lon, lat]",
                get_text="text",
                get_size=font_size,
                get_color=[255, 255, 255, 245],
                get_alignment_baseline="'bottom'",
                get_pixel_offset="[0, offset_y]",
            )
        )

    positions = [[c.lng, c.lat] for c in clusters]
    view = compute_view(positions, view_proportion=0.82)
    view_state = pdk.ViewState(
        latitude=view.latitude,
        longitude=view.longitude,
        zoom=max(view.zoom - 0.35, 1.5),
        pitch=20,
        bearing=0,
    )

    deck = pdk.Deck(
        layers=layers,
        initial_view_state=view_state,
        map_style=MAP_STYLE_CARTO_DARK,
        tooltip={"html": "{tooltip}", "style": {"backgroundColor": "#0f1528", "color": "#eaf2ff"}},
    )
    st.markdown('<div class="journey-map-wrap">', unsafe_allow_html=True)
    try:
        st.pydeck_chart(deck, width="stretch", height=560)
    except TypeError:
        st.pydeck_chart(deck, use_container_width=True, height=560)
    except Exception as exc:
        st.error(f"Map could not be rendered: {exc}")
    st.markdown("</div>", unsafe_allow_html=True)


def render_lens_map(points: list[JourneyPoint], lens_order: int) -> None:
    """Map variant for the lens view: selected cluster glows; others stay visible but dim."""
    if not points:
        st.info("No mappable songs in this playlist.")
        return

    selected = next((p for p in points if p.order == lens_order), None)
    if selected is None:
        render_world_map(points)
        return

    clusters = build_map_clusters(points)
    clusters_ordered = clusters_in_playlist_order(points, clusters)

    def _cluster_has_selected(cluster: Any) -> bool:
        return any(s.order == lens_order for s in cluster.songs)

    base_rows: list[dict[str, Any]] = []
    highlight_rows: list[dict[str, Any]] = []
    glow_rows: list[dict[str, Any]] = []
    for c in clusters:
        is_selected_cluster = _cluster_has_selected(c)
        row = {
            "lon": c.lng,
            "lat": c.lat,
            "tooltip": cluster_tooltip_html(c),
            "radius_m": 55_000 + min(c.song_count - 1, 6) * 6_000,
            "radius_min_px": min(20, 10 + 2 * (c.song_count - 1)),
            "radius_max_px": min(26, 14 + 2 * (c.song_count - 1)),
        }
        if is_selected_cluster:
            glow_rows.append(
                {
                    "lon": c.lng,
                    "lat": c.lat,
                    "radius_m": 120_000,
                    "radius_min_px": 38,
                    "radius_max_px": 48,
                }
            )
            highlight_rows.append(row)
        else:
            base_rows.append(row)

    text_rows: list[dict[str, Any]] = []
    for cluster in clusters:
        is_selected_cluster = _cluster_has_selected(cluster)
        visible = cluster.songs[:MAX_VISIBLE_MAP_LABELS]
        alpha = 255 if is_selected_cluster else 170
        for i, song in enumerate(visible):
            title = song.title if len(song.title) <= 32 else f"{song.title[:31]}…"
            text_rows.append(
                {
                    "lon": cluster.lng,
                    "lat": cluster.lat,
                    "text": f"{song.order}. {title}",
                    "offset_y": -(LABEL_BASE_OFFSET_PX + i * LABEL_LINE_HEIGHT_PX),
                    "alpha": alpha,
                }
            )
        remaining = len(cluster.songs) - len(visible)
        if remaining > 0:
            i = len(visible)
            text_rows.append(
                {
                    "lon": cluster.lng,
                    "lat": cluster.lat,
                    "text": f"+{remaining} more",
                    "offset_y": -(LABEL_BASE_OFFSET_PX + i * LABEL_LINE_HEIGHT_PX),
                    "alpha": alpha,
                }
            )

    text_df = (
        pd.DataFrame(text_rows)
        if text_rows
        else pd.DataFrame(columns=["lon", "lat", "text", "offset_y", "alpha"])
    )

    path_data = [{"path": seg} for seg in build_cluster_path_segments(clusters_ordered)]
    layers: list[pdk.Layer] = []

    if path_data:
        layers.append(
            pdk.Layer(
                "PathLayer",
                data=path_data,
                get_path="path",
                get_color=[163, 126, 255, 140],
                get_width=3,
                width_min_pixels=2,
                pickable=False,
            )
        )

    if base_rows:
        layers.append(
            pdk.Layer(
                "ScatterplotLayer",
                data=pd.DataFrame(base_rows),
                get_position="[lon, lat]",
                get_fill_color=[255, 200, 102, 130],
                get_line_color=[255, 255, 255, 110],
                get_radius="radius_m",
                radius_min_pixels="radius_min_px",
                radius_max_pixels="radius_max_px",
                line_width_min_pixels=1,
                pickable=True,
            )
        )

    if glow_rows:
        layers.append(
            pdk.Layer(
                "ScatterplotLayer",
                data=pd.DataFrame(glow_rows),
                get_position="[lon, lat]",
                get_fill_color=[155, 122, 255, 70],
                get_radius="radius_m",
                radius_min_pixels="radius_min_px",
                radius_max_pixels="radius_max_px",
                line_width_min_pixels=0,
                stroked=False,
                pickable=False,
            )
        )

    if highlight_rows:
        layers.append(
            pdk.Layer(
                "ScatterplotLayer",
                data=pd.DataFrame(highlight_rows),
                get_position="[lon, lat]",
                get_fill_color=[184, 156, 255, 240],
                get_line_color=[239, 229, 255, 240],
                get_radius="radius_m",
                radius_min_pixels="radius_min_px",
                radius_max_pixels="radius_max_px",
                line_width_min_pixels=2,
                pickable=True,
            )
        )

    if not text_df.empty:
        font_size = 12 if any(c.song_count > 3 for c in clusters) else 13
        layers.append(
            pdk.Layer(
                "TextLayer",
                data=text_df,
                get_position="[lon, lat]",
                get_text="text",
                get_size=font_size,
                get_color="[255, 255, 255, alpha]",
                get_alignment_baseline="'bottom'",
                get_pixel_offset="[0, offset_y]",
            )
        )

    positions = [[c.lng, c.lat] for c in clusters]
    view = compute_view(positions, view_proportion=0.7)
    selected_zoom = max(view.zoom + 0.5, 3.5)
    view_state = pdk.ViewState(
        latitude=selected.lat,
        longitude=selected.lng,
        zoom=min(selected_zoom, 5.5),
        pitch=20,
        bearing=0,
    )

    deck = pdk.Deck(
        layers=layers,
        initial_view_state=view_state,
        map_style=MAP_STYLE_CARTO_DARK,
        tooltip={"html": "{tooltip}", "style": {"backgroundColor": "#0f1528", "color": "#eaf2ff"}},
    )
    st.markdown('<div class="journey-map-wrap">', unsafe_allow_html=True)
    try:
        st.pydeck_chart(deck, width="stretch", height=560)
    except TypeError:
        st.pydeck_chart(deck, use_container_width=True, height=560)
    except Exception as exc:
        st.error(f"Map could not be rendered: {exc}")
    st.markdown("</div>", unsafe_allow_html=True)


def _route_stop_row_html(
    song: dict[str, Any],
    order: int,
    point_by_order: dict[int, JourneyPoint],
    *,
    selected: bool,
) -> str:
    loc = extract_location(song)
    jp = point_by_order.get(order)
    mapped = jp is not None
    loc_label = jp.location_label if mapped else loc.display
    index_style = _journey_index_style(order, mapped=mapped)
    loc_color = _journey_location_color(order, mapped=mapped)
    loc_style = f"color: {loc_color};" if mapped else "color: #7f8fb4;"

    title = str(song.get("title") or "Untitled")
    artist = str(song.get("artist") or "")
    genre = _primary_style_label(song)
    cover_url = _song_cover_url(song)
    cover_src = _cover_data_uri(cover_url) or cover_url
    if cover_src:
        cover_html = (
            f'<div class="journey-stop-cover-wrap">'
            f'<img class="journey-stop-cover" src="{html.escape(cover_src, quote=True)}" alt="" />'
            f"</div>"
        )
    else:
        cover_html = (
            '<div class="journey-stop-cover-wrap">'
            '<div class="journey-stop-cover-empty"></div>'
            "</div>"
        )

    genre_html = ""
    if genre:
        genre_html = f'<div class="journey-stop-genre">{html.escape(genre)}</div>'

    stop_class = "journey-stop selected" if selected else "journey-stop"
    return (
        f'<div class="{stop_class}">'
        f'<div class="journey-index" style="{index_style}">{order}</div>'
        f"{cover_html}"
        f'<div class="journey-stop-body">'
        f'<div class="journey-stop-title">{html.escape(title)}</div>'
        f'<div class="journey-stop-artist">{html.escape(artist)}</div>'
        f'<div class="journey-stop-location" style="{loc_style}">{html.escape(loc_label)}</div>'
        f"</div>"
        f"{genre_html}"
        f"</div>"
    )


def render_route_stops_panel(
    songs: list[dict[str, Any]],
    points: list[JourneyPoint],
    *,
    unmapped: int,
) -> None:
    point_by_order = {p.order: p for p in points}
    mapped_count = len(points)
    total = len([s for s in songs if isinstance(s, dict)])
    valid_songs = [(order, s) for order, s in enumerate(songs, start=1) if isinstance(s, dict)]

    panel = st.container(border=True)
    with panel:
        st.markdown(
            '<div class="route-stops-header">'
            '<span class="route-stops-icon">◎</span>'
            '<span>Route Stops</span>'
            "</div>",
            unsafe_allow_html=True,
        )

        for idx, (order, song) in enumerate(valid_songs):
            row_html = _route_stop_row_html(song, order, point_by_order, selected=False)

            card_col, btn_col = st.columns([6.4, 0.7], gap="small")
            with card_col:
                st.markdown(row_html, unsafe_allow_html=True)
            with btn_col:
                if st.button(
                    "✦",
                    key=f"explore_stop_{order}",
                    help="Explore song stop",
                    use_container_width=True,
                ):
                    st.session_state.journey_lens_song_order = order
                    st.rerun()

            if idx < len(valid_songs) - 1:
                st.markdown('<div class="journey-connector"></div>', unsafe_allow_html=True)

        if unmapped or mapped_count < total:
            note = f'<div class="route-stops-note">{mapped_count} of {total} on map'
            if unmapped:
                note += f" · {unmapped} unmapped"
            note += "</div>"
            st.markdown(note, unsafe_allow_html=True)


def _song_high_conf_labels(items: Any, *, threshold: float = 0.9, limit: int = 4) -> list[str]:
    if not isinstance(items, list):
        return []
    out: list[str] = []
    for entry in items:
        if not isinstance(entry, dict):
            continue
        label = entry.get("label")
        if not label:
            continue
        conf = entry.get("confidence")
        conf_val = float(conf) if isinstance(conf, (int, float)) else 0.0
        if conf_val < threshold:
            continue
        out.append(str(label).strip())
        if len(out) >= limit:
            break
    return out


def _song_geography_label(song: dict[str, Any], point: JourneyPoint | None) -> str:
    g = song.get("geography")
    if isinstance(g, dict):
        primary = g.get("primary")
        if isinstance(primary, dict) and primary.get("label"):
            return str(primary["label"]).strip()
    if point is not None:
        return point.location_label
    return extract_location(song).display


def render_selected_stop_card(song: dict[str, Any], point: JourneyPoint | None) -> None:
    title = str(song.get("title") or "Untitled")
    artist = str(song.get("artist") or "")
    release_year = str(song.get("release_year") or "").strip()
    style_label = _primary_style_label(song)

    location = ""
    if point is not None:
        location = point.location_label
    else:
        loc = extract_location(song)
        if loc.is_mappable:
            location = loc.display

    cover_url = _song_cover_url(song)
    cover_src = _cover_data_uri(cover_url) or cover_url
    if cover_src:
        cover_html = (
            f'<img class="lens-selected-cover" '
            f'src="{html.escape(cover_src, quote=True)}" alt="" />'
        )
    else:
        cover_html = '<div class="lens-selected-cover-empty"></div>'

    pills_parts: list[str] = []
    if release_year:
        pills_parts.append(
            f'<span class="lens-selected-pill year">{html.escape(release_year)}</span>'
        )
    if style_label:
        pills_parts.append(
            f'<span class="lens-selected-pill style">{html.escape(style_label)}</span>'
        )
    pills_html = "".join(pills_parts)

    location_html = ""
    if location:
        location_html = (
            f'<div class="lens-selected-location">{html.escape(location)}</div>'
        )

    geography_value = _song_geography_label(song, point)
    time_obj = song.get("time")
    era_value = ""
    if isinstance(time_obj, dict) and time_obj.get("label"):
        era_value = str(time_obj["label"]).strip()
    emotion_labels = _song_high_conf_labels(song.get("emotions"))
    influence_labels = _song_high_conf_labels(song.get("influences"))

    meta_rows = [
        ("Geography", "◍", geography_value or EMPTY_SIGNAL),
        ("Era", "◷", era_value or EMPTY_SIGNAL),
        ("Emotion", "♡", ", ".join(emotion_labels) if emotion_labels else EMPTY_SIGNAL),
        (
            "Influence",
            "◎",
            ", ".join(influence_labels) if influence_labels else EMPTY_SIGNAL,
        ),
    ]
    meta_html_parts: list[str] = []
    for label, icon, value in meta_rows:
        meta_html_parts.append(
            f'<div class="lens-meta-row">'
            f'<div class="lens-meta-icon">{icon}</div>'
            f'<div class="lens-meta-label">{html.escape(label)}</div>'
            f'<div class="lens-meta-value">{html.escape(value)}</div>'
            f"</div>"
        )
    meta_html = "".join(meta_html_parts)

    blurb = _match_blurb(song)

    container = st.container(border=True)
    with container:
        st.markdown(
            f"""
            <div class="lens-selected-marker"></div>
            <div class="lens-selected-header">
                <span>✦</span>
                <span>Selected Stop</span>
            </div>
            <div class="lens-selected-top">
                {cover_html}
                <div>
                    <div class="lens-selected-title">{html.escape(title)}</div>
                    <div class="lens-selected-artist">{html.escape(artist)}</div>
                    <div class="lens-selected-pills">{pills_html}</div>
                    {location_html}
                </div>
            </div>
            <div class="lens-meta-grid">{meta_html}</div>
            <div class="lens-match-blurb">
                <div class="lens-match-blurb-title"><span>✦</span><span>Why this match?</span></div>
                <div>{html.escape(blurb)}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


_PLACEHOLDER_ADJACENT_SONGS: list[dict[str, str]] = [
    {
        "title": "Piece of My Heart",
        "artist": "Big Brother & the Holding Company",
        "location": "San Francisco, USA",
        "label": "same region",
        "percent": "91%",
    },
    {
        "title": "White Rabbit",
        "artist": "Jefferson Airplane",
        "location": "San Francisco, USA",
        "label": "same region",
        "percent": "90%",
    },
    {
        "title": "Somebody to Love",
        "artist": "Jefferson Airplane",
        "location": "San Francisco, USA",
        "label": "same region",
        "percent": "88%",
    },
    {
        "title": "Light My Fire",
        "artist": "The Doors",
        "location": "Los Angeles, USA",
        "label": "nearby region",
        "percent": "86%",
    },
    {
        "title": "The End",
        "artist": "The Doors",
        "location": "Los Angeles, USA",
        "label": "nearby region",
        "percent": "84%",
    },
    {
        "title": "The Weight",
        "artist": "The Band",
        "location": "Vancouver, Canada",
        "label": "nearby region",
        "percent": "82%",
    },
]


def render_adjacent_songs(
    songs: list[dict[str, Any]],
    points: list[JourneyPoint],
    lens_order: int,
) -> None:
    row_html_parts: list[str] = []
    for rank, entry in enumerate(_PLACEHOLDER_ADJACENT_SONGS, start=1):
        pill_kind = "same" if entry["label"] == "same region" else "nearby"
        row_html_parts.append(
            f'<div class="lens-adjacent-row">'
            f'<div class="lens-adjacent-rank">{rank}</div>'
            f'<div class="lens-adjacent-cover-empty"></div>'
            f"<div>"
            f'<div class="lens-adjacent-title">{html.escape(entry["title"])}</div>'
            f'<div class="lens-adjacent-artist">{html.escape(entry["artist"])}</div>'
            f'<div class="lens-adjacent-location">{html.escape(entry["location"])}</div>'
            f"</div>"
            f'<span class="lens-region-pill {pill_kind}">{html.escape(entry["label"])}</span>'
            f'<div class="lens-adjacent-percent">{html.escape(entry["percent"])}</div>'
            f"</div>"
        )

    body = "".join(row_html_parts)
    st.markdown(
        f"""
        <div class="lens-adjacent-panel">
            <div class="lens-adjacent-marker"></div>
            <div class="lens-adjacent-header">
                <span>◍</span><span>Adjacent Songs by Geography</span>
            </div>
            {body}
            <div class="lens-adjacent-footer">View all adjacent songs ›</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_journey_path(
    songs: list[dict[str, Any]],
    points: list[JourneyPoint],
    *,
    selected_order: int | None = None,
) -> None:
    point_by_order = {p.order: p for p in points}
    valid_songs = [(order, s) for order, s in enumerate(songs, start=1) if isinstance(s, dict)]
    if not valid_songs:
        return

    steps: list[str] = []
    for idx, (order, song) in enumerate(valid_songs):
        city = _city_for_journey_path(song, order, point_by_order)
        mapped = point_by_order.get(order) is not None
        index_style = _journey_index_style(order, mapped=mapped)
        city_color = _journey_location_color(order, mapped=mapped)
        step_classes = "journey-path-step"
        if selected_order is not None and order == selected_order:
            step_classes += " selected"
        steps.append(
            f'<span class="{step_classes}">'
            f'<span class="journey-path-index" style="{index_style}">{order}</span>'
            f'<span class="journey-path-city" style="color: {city_color};">{html.escape(city)}</span>'
            f"</span>"
        )
        if idx < len(valid_songs) - 1:
            steps.append('<span class="journey-path-arrow">→</span>')

    flow = "".join(steps)
    st.markdown(
        f"""
        <div class="journey-path-bar">
            <div class="journey-path-title">
                <span>↝</span>
                <span>Journey Path</span>
            </div>
            <div class="journey-path-flow">{flow}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )



def _render_lens_filter_row() -> None:
    chips = [
        ("◍", "Map", True, False),
        ("◷", "Era", False, True),
        ("♡", "Mood", False, True),
        ("◌", "Style", False, True),
        ("◎", "Influence", False, True),
    ]
    parts: list[str] = []
    for icon, label, active, disabled in chips:
        classes = "lens-filter-chip"
        if active:
            classes += " active"
        if disabled:
            classes += " disabled"
        parts.append(
            f'<span class="{classes}"><span>{icon}</span><span>{html.escape(label)}</span></span>'
        )
    st.markdown(
        f'<div class="lens-filter-row">{"".join(parts)}</div>',
        unsafe_allow_html=True,
    )


def render_journey_lens(
    songs: list[dict[str, Any]],
    points: list[JourneyPoint],
    lens_order: int,
) -> None:
    valid_songs = {order: s for order, s in enumerate(songs, start=1) if isinstance(s, dict)}
    selected_song = valid_songs.get(lens_order)
    selected_point = next((p for p in points if p.order == lens_order), None)
    if selected_song is None or selected_point is None:
        st.session_state.journey_lens_song_order = None
        st.rerun()
        return

    back_col, _ = st.columns([1.5, 6.5])
    with back_col:
        if st.button("← Back to Journey", key="lens_back_btn", use_container_width=True):
            st.session_state.journey_lens_song_order = None
            st.rerun()

    st.markdown(
        """
        <div class="journey-hero-title">
            Explore <span class="journey-gradient">around</span> your stop
        </div>
        <div class="journey-hero-sub">
            Discover similar songs connected by style, time, emotion, and influence.
        </div>
        """,
        unsafe_allow_html=True,
    )

    map_col, panel_col = st.columns([2.1, 1.0], gap="medium")
    with map_col:
        _render_lens_filter_row()
        render_world_map(points)
    with panel_col:
        render_selected_stop_card(selected_song, selected_point)
        render_adjacent_songs(songs, points, lens_order)

    path_col, cta_col = st.columns([5.0, 2.0], gap="medium")
    with path_col:
        render_journey_path(songs, points, selected_order=lens_order)
    with cta_col:
        st.markdown("<div style='height: 0.7rem;'></div>", unsafe_allow_html=True)
        if st.button(
            "✦ Explore Similar Stops",
            key="lens_explore_similar_btn",
            use_container_width=True,
        ):
            st.toast(
                "External recommendations coming soon — will use the backend.",
            )


def render_journey_tab(api_base: str) -> None:
    pid = _resolve_playlist_id()
    if pid is None and not isinstance(st.session_state.get("generated_playlist"), dict):
        st.markdown(
            """
            <div class="journey-empty">
                <strong>No playlist yet</strong><br/>
                Generate or load a playlist on the <strong>Discover</strong> tab (e.g. enter id <strong>1</strong> and generate),
                then return here to see your world map and song journey.
            </div>
            """,
            unsafe_allow_html=True,
        )
        return

    try:
        with st.spinner("Loading playlist…"):
            pl = _load_playlist_for_journey(api_base)
    except requests.RequestException as exc:
        st.error(f"Network error loading playlist: {exc}")
        return

    if not isinstance(pl, dict) or pl.get("songs") is None:
        st.markdown(
            """
            <div class="journey-empty">
                <strong>Could not load playlist</strong><br/>
                Enter a playlist id on Discover and generate, or check that the backend is running.
            </div>
            """,
            unsafe_allow_html=True,
        )
        return

    songs_raw = pl.get("songs") or []
    songs = [s for s in songs_raw if isinstance(s, dict)]
    if not songs:
        st.markdown(
            '<div class="journey-empty"><strong>Playlist has no songs</strong></div>',
            unsafe_allow_html=True,
        )
        return

    try:
        with st.spinner("Placing songs on the map…"):
            songs = enrich_songs_with_locations(api_base, pl, songs)
            points, unmapped = build_journey_points(songs)
    except Exception as exc:
        st.warning(f"Could not geocode some locations: {exc}")
        points, unmapped = [], len(songs)

    lens_order = st.session_state.get("journey_lens_song_order")
    if isinstance(lens_order, int) and any(p.order == lens_order for p in points):
        render_journey_lens(songs, points, lens_order)
        return
    if lens_order is not None:
        st.session_state.journey_lens_song_order = None

    render_journey_header()
    render_journey_chips(_aggregates_from_playlist(pl))

    map_col, panel_col = st.columns([2.1, 1.0], gap="medium")
    with map_col:
        if unmapped and not points and songs_have_locations(songs):
            st.warning(
                "Songs have city/country but could not be geocoded. "
                "Check your internet connection, restart the app, and try Journey again."
            )
        elif unmapped:
            st.markdown(
                f'<div class="journey-map-note">{html.escape(str(unmapped))} song(s) could not be placed on the map.</div>',
                unsafe_allow_html=True,
            )
        render_world_map(points)
    with panel_col:
        render_route_stops_panel(songs, points, unmapped=unmapped)

    render_journey_path(songs, points)





def render_left_panel(api_base: str) -> None:
    st.markdown(
        """
        <div class="journey-hero-title">
            Describe your <span class="journey-gradient">perfect</span> playlist
        </div>
        <div class="journey-hero-sub">
            Tell us the style, time period, emotions, influences, and number of songs you want.
        </div>
        """,
        unsafe_allow_html=True,
    )

    pending_prompt = st.session_state.pop("pending_playlist_prompt", None)
    if isinstance(pending_prompt, str):
        st.session_state.playlist_prompt = pending_prompt

    with st.form("playlist_form", clear_on_submit=False):
        prompt_col, generate_col, spacer_col = st.columns([5.0, 1.8, 2.8])
        with prompt_col:
            st.text_area(
                "playlist_prompt",
                height=170,
                label_visibility="collapsed",
                key="playlist_prompt",
            )
            load_text_col, load_gap_col, load_input_col, _ = st.columns([1.5, 0.25, 0.9, 7.35])
            with load_text_col:
                st.markdown(
                    '<div class="load-playlist-label">Load Playlist by id</div>',
                    unsafe_allow_html=True,
                )
            with load_gap_col:
                st.empty()
            with load_input_col:
                st.text_input(
                    "Load Playlist by id",
                    placeholder="id",
                    key="load_playlist_id_text",
                    label_visibility="collapsed",
                )
        with generate_col:
            generate = st.form_submit_button("🪄 Generate Playlist", use_container_width=True)
        with spacer_col:
            st.empty()

    if generate:
        st.session_state.playlist_error = None
        typed_id = str(st.session_state.get("load_playlist_id_text") or "").strip()
        if typed_id:
            if not typed_id.isdigit():
                st.session_state.playlist_error = "Playlist id must be a number."
            else:
                playlist_id = int(typed_id)
                st.session_state.active_playlist_id = playlist_id
                try:
                    loaded = get_playlist(api_base, playlist_id)
                    _set_active_playlist(loaded)
                    loaded_prompt = str(loaded.get("user_prompt") or loaded.get("prompt") or "").strip()
                    if loaded_prompt:
                        st.session_state.pending_playlist_prompt = loaded_prompt
                    st.toast(f"Playlist {playlist_id} loaded")
                    st.rerun()
                except SoundTripAPIError as exc:
                    st.session_state.generated_playlist = None
                    if exc.status_code == 404:
                        st.session_state.playlist_error = f"Playlist {playlist_id} not found."
                    else:
                        st.session_state.playlist_error = str(exc)
                except requests.RequestException as exc:
                    st.session_state.playlist_error = f"Network error: {exc}"
        else:
            prompt = (st.session_state.get("playlist_prompt") or "").strip()
            if len(prompt) < 5:
                st.session_state.playlist_error = "Prompt must be at least 5 characters (API requirement)."
            else:
                try:
                    with st.spinner("Generating playlist…"):
                        playlist = wait_for_playlist(api_base, prompt)
                        _set_active_playlist(playlist)
                    st.toast("Playlist ready! Open the Journey tab to see your map.")
                except SoundTripAPIError as exc:
                    st.session_state.playlist_error = str(exc)
                except requests.RequestException as exc:
                    st.session_state.playlist_error = f"Network error: {exc}"

    if st.session_state.playlist_error:
        st.error(st.session_state.playlist_error)

    pl = st.session_state.generated_playlist
    if isinstance(pl, dict) and pl.get("songs") is not None:
        agg = _aggregates_from_playlist(pl)
        render_chips(agg)
        st.markdown('<div class="generated-playlist-spacer"></div>', unsafe_allow_html=True)
        st.markdown('<div class="section-title">♪ Generated Playlist</div>', unsafe_allow_html=True)
        render_playlist_songs(pl)


def main() -> None:
    _init_session_state()
    inject_styles()
    api_base = _api_base_url()
    render_top_banner()

    discover_tab, journey_tab, library_tab = st.tabs(["⌖ Discover", "↝ Journey", "📚 Library"])

    with discover_tab:
        render_left_panel(api_base)

    with journey_tab:
        render_journey_tab(api_base)

    with library_tab:
        pass


if __name__ == "__main__":
    main()
