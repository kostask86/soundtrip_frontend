import html
import os
from typing import Any

import requests
import streamlit as st

from soundtrip_client import SoundTripAPIError, wait_for_playlist

st.set_page_config(page_title="Song Journey", page_icon="🎵", layout="wide")

DEFAULT_PROMPT = (
    "Create a 6-song playlist with psychedelic rock and blues rock from the late 1960s and early "
    "1970s. I want it to feel mysterious, dark, transcendent, and intense, with influences from "
    "American folk, blues tradition, and psychedelic counterculture."
)

EMPTY_SIGNAL = "—"


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
    return "http://127.0.0.1:8000"


def _init_session_state() -> None:
    if "generated_playlist" not in st.session_state:
        st.session_state.generated_playlist = None
    if "playlist_error" not in st.session_state:
        st.session_state.playlist_error = None
    if "playlist_prompt" not in st.session_state:
        st.session_state.playlist_prompt = DEFAULT_PROMPT


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
            if isinstance(st_obj, dict) and st_obj.get("label"):
                styles.append(str(st_obj["label"]))
        for em in song.get("emotions") or []:
            if isinstance(em, dict) and em.get("label"):
                emotions.append(str(em["label"]))
        tm = song.get("time")
        if isinstance(tm, dict) and tm.get("label"):
            times.append(str(tm["label"]))
        for inf in song.get("influences") or []:
            if isinstance(inf, dict) and inf.get("label"):
                influences.append(str(inf["label"]))
        g = song.get("geography")
        if isinstance(g, dict):
            p = g.get("primary")
            if isinstance(p, dict) and p.get("label"):
                geos.append(str(p["label"]))
            for sec in g.get("secondary") or []:
                if isinstance(sec, dict) and sec.get("label"):
                    geos.append(str(sec["label"]))
    n = len(songs) if isinstance(songs, list) else 0
    return {
        "style": _ordered_unique_join(styles),
        "time": _ordered_unique_join(times),
        "emotion": _ordered_unique_join(emotions),
        "influence": _ordered_unique_join(influences),
        "geography": _ordered_unique_join(geos),
        "songs_requested": str(n) if n else EMPTY_SIGNAL,
    }


def _tags_for_song(song: dict[str, Any]) -> list[str]:
    tags: list[str] = []
    for st_obj in song.get("styles") or []:
        if isinstance(st_obj, dict) and st_obj.get("label"):
            tags.append(str(st_obj["label"]))
    for em in song.get("emotions") or []:
        if isinstance(em, dict) and em.get("label"):
            tags.append(str(em["label"]))
    tm = song.get("time")
    if isinstance(tm, dict) and tm.get("label"):
        tags.append(str(tm["label"]))
    for inf in song.get("influences") or []:
        if isinstance(inf, dict) and inf.get("label"):
            tags.append(str(inf["label"]))
    g = song.get("geography")
    if isinstance(g, dict):
        p = g.get("primary")
        if isinstance(p, dict) and p.get("label"):
            tags.append(str(p["label"]))
    seen: set[str] = set()
    ordered: list[str] = []
    for t in tags:
        if t not in seen:
            seen.add(t)
            ordered.append(t)
    return ordered


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
                font-size: 1.05rem;
                margin-bottom: 0.95rem;
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

            .chip .k {
                color: #9eb2e7;
                font-size: 0.72rem;
                margin-bottom: 0.13rem;
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
                grid-template-columns: 32px 1fr auto;
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

            div[data-testid="stButton"] button {
                width: 100%;
                border: none;
                border-radius: 12px;
                min-height: 3.15rem;
                font-weight: 600;
                color: #f8f7ff;
                background: linear-gradient(90deg, #6750ff 0%, #b146ff 100%);
                box-shadow: 0 0 18px rgba(125, 88, 255, 0.46);
            }

            div[data-testid="stButton"] button:hover {
                background: linear-gradient(90deg, #7664ff 0%, #c85aff 100%);
            }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _chip(k: str, v: str) -> str:
    return (
        f'<div class="chip"><div class="k">{html.escape(k)}</div>'
        f'<div class="v">{html.escape(v)}</div></div>'
    )


def _signal_row(label: str, value: str) -> str:
    return (
        f'<div class="signal-row">'
        f'<div class="signal-label">{html.escape(label)}</div>'
        f'<div class="signal-value">{html.escape(value)}</div>'
        f"</div>"
    )


def render_chips(agg: dict[str, str]) -> None:
    inner = "".join(
        [
            _chip("Style", agg["style"]),
            _chip("Time", agg["time"]),
            _chip("Emotion", agg["emotion"]),
            _chip("Influence", agg["influence"]),
            _chip("Songs Requested", agg["songs_requested"]),
        ]
    )
    st.markdown(f'<div class="chip-row">{inner}</div>', unsafe_allow_html=True)


def render_playlist_songs(playlist: dict[str, Any]) -> None:
    songs = playlist.get("songs") or []
    for index, song in enumerate(songs, start=1):
        if not isinstance(song, dict):
            continue
        title = str(song.get("title") or "Untitled")
        artist = str(song.get("artist") or "")
        tag_list = _tags_for_song(song)
        tags_html = "".join(f'<span class="tag">{html.escape(t)}</span>' for t in tag_list)
        st.markdown(
            f"""
            <div class="song-row">
                <div class="song-num">{index}</div>
                <div>
                    <div class="song-title">{html.escape(title)}</div>
                    <div class="song-artist">{html.escape(artist)}</div>
                    <div class="song-tags">{tags_html}</div>
                </div>
                <div class="play-btn">▶</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def render_signals_panel(agg: dict[str, str], *, has_playlist: bool) -> None:
    geo_row = ""
    if has_playlist and agg.get("geography") and agg["geography"] != EMPTY_SIGNAL:
        geo_row = _signal_row("Geography", agg["geography"])
    st.markdown(
        f"""
        <div class="signals-card">
            <div class="signals-title">Journey Signals</div>
            {_signal_row("Style", agg["style"] if has_playlist else EMPTY_SIGNAL)}
            {_signal_row("Time", agg["time"] if has_playlist else EMPTY_SIGNAL)}
            {_signal_row("Emotion", agg["emotion"] if has_playlist else EMPTY_SIGNAL)}
            {_signal_row("Influence", agg["influence"] if has_playlist else EMPTY_SIGNAL)}
            {geo_row}
            {_signal_row("Songs Requested", agg["songs_requested"] if has_playlist else EMPTY_SIGNAL)}
            <div class="wave">~ waveform visualization placeholder ~</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_left_panel(api_base: str) -> None:
    st.markdown('<div class="hero-title">Describe your perfect playlist</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="hero-sub">Tell us the style, time period, emotions, influences, and number of songs you want.</div>',
        unsafe_allow_html=True,
    )

    left_input, right_button = st.columns([3.3, 1.4], gap="small")
    with left_input:
        st.text_area(
            "playlist_prompt",
            height=170,
            label_visibility="collapsed",
            key="playlist_prompt",
        )
    with right_button:
        st.write("")
        st.write("")
        generate = st.button("✨ Generate Playlist", use_container_width=True)
        if generate:
            st.session_state.playlist_error = None
            prompt = (st.session_state.get("playlist_prompt") or "").strip()
            if len(prompt) < 5:
                st.session_state.playlist_error = "Prompt must be at least 5 characters (API requirement)."
            else:
                try:
                    with st.spinner("Generating playlist…"):
                        st.session_state.generated_playlist = wait_for_playlist(api_base, prompt)
                    st.toast("Playlist ready!", icon="🎵")
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
        st.markdown('<div class="section-title">🎵 Generated Playlist</div>', unsafe_allow_html=True)
        render_playlist_songs(pl)


def main() -> None:
    _init_session_state()
    inject_styles()
    api_base = _api_base_url()

    content_left, content_right = st.columns([2.1, 1], gap="large")
    with content_left:
        render_left_panel(api_base)

    pl = st.session_state.generated_playlist
    has_playlist = isinstance(pl, dict) and pl.get("songs") is not None
    agg = (
        _aggregates_from_playlist(pl)
        if has_playlist
        else {
            "style": EMPTY_SIGNAL,
            "time": EMPTY_SIGNAL,
            "emotion": EMPTY_SIGNAL,
            "influence": EMPTY_SIGNAL,
            "geography": EMPTY_SIGNAL,
            "songs_requested": EMPTY_SIGNAL,
        }
    )
    with content_right:
        st.write("")
        st.write("")
        render_signals_panel(agg, has_playlist=has_playlist)


if __name__ == "__main__":
    main()
