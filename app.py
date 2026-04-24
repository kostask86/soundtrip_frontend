import streamlit as st


st.set_page_config(page_title="Song Journey", page_icon="🎵", layout="wide")


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

            .topbar {
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 1.4rem;
            }

            .brand {
                display: flex;
                align-items: center;
                gap: 0.6rem;
                font-weight: 700;
                color: #f5f7ff;
                font-size: 1.1rem;
            }

            .brand .logo {
                width: 30px;
                height: 30px;
                border-radius: 999px;
                background: radial-gradient(circle at 25% 25%, #8f7dff, #5b39f4 55%, #382492 100%);
                display: inline-flex;
                align-items: center;
                justify-content: center;
                font-size: 0.95rem;
                box-shadow: 0 0 18px rgba(126, 92, 255, 0.6);
            }

            .nav {
                display: flex;
                gap: 0.6rem;
            }

            .pill {
                border: 1px solid rgba(161, 180, 255, 0.2);
                border-radius: 999px;
                padding: 0.36rem 0.95rem;
                color: #d9e3ff;
                background: rgba(14, 20, 38, 0.56);
                font-size: 0.82rem;
            }

            .pill.active {
                background: linear-gradient(90deg, rgba(72, 53, 196, 0.45), rgba(64, 112, 255, 0.42));
                border-color: rgba(129, 146, 255, 0.65);
                color: #ffffff;
                box-shadow: 0 0 14px rgba(91, 95, 255, 0.35);
            }

            .avatar {
                width: 36px;
                height: 36px;
                border-radius: 999px;
                background: radial-gradient(circle at 20% 20%, #77f6ff 0%, #8e63ff 55%, #4a2ea5 100%);
                box-shadow: 0 0 14px rgba(137, 119, 255, 0.6);
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


def render_topbar() -> None:
    st.markdown(
        """
        <div class="topbar">
            <div class="brand"><span class="logo">♪</span>Song Journey</div>
            <div class="nav">
                <span class="pill active">Discover</span>
                <span class="pill">Journey</span>
                <span class="pill">Library</span>
            </div>
            <div class="avatar"></div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_left_panel() -> None:
    st.markdown('<div class="hero-title">Describe your perfect playlist</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="hero-sub">Tell us the style, time period, emotions, influences, and number of songs you want.</div>',
        unsafe_allow_html=True,
    )

    left_input, right_button = st.columns([3.3, 1.4], gap="small")
    with left_input:
        st.text_area(
            "playlist_prompt",
            value=(
                "Create a 6-song playlist with psychedelic rock and blues rock from the late 1960s and early "
                "1970s. I want it to feel mysterious, dark, transcendent, and intense, with influences from "
                "American folk, blues tradition, and psychedelic counterculture."
            ),
            height=170,
            label_visibility="collapsed",
        )
    with right_button:
        st.write("")
        st.write("")
        generate = st.button("✨ Generate Playlist", use_container_width=True)
        if generate:
            st.toast("UI only for now — API calls next step.", icon="🎵")

    st.markdown(
        """
        <div class="chip-row">
            <div class="chip"><div class="k">Style</div><div class="v">Psychedelic Rock, Blues Rock</div></div>
            <div class="chip"><div class="k">Time</div><div class="v">Late 1960s to Early 1970s</div></div>
            <div class="chip"><div class="k">Emotion</div><div class="v">Dark, Mysterious, Transcendent, Intense</div></div>
            <div class="chip"><div class="k">Influence</div><div class="v">American Folk, Blues Tradition, Psychedelic Counterculture</div></div>
            <div class="chip"><div class="k">Songs Requested</div><div class="v">6</div></div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown('<div class="section-title">🎵 Generated Playlist</div>', unsafe_allow_html=True)

    songs = [
        ("The End", "The Doors", ["Psychedelic Rock", "1960s", "Mystery"]),
        ("Stairway to Heaven", "Led Zeppelin", ["Blues Rock", "1970s", "Epic"]),
        ("Like a Rolling Stone", "Bob Dylan", ["Folk Rock", "1960s", "Rebellious"]),
        ("Time", "Pink Floyd", ["Psychedelic Rock", "1970s", "Transcendent"]),
        ("All Along the Watchtower", "The Jimi Hendrix Experience", ["Blues Rock", "1960s", "Mystery"]),
        ("White Rabbit", "Jefferson Airplane", ["Psychedelic Rock", "1960s", "Dreamy"]),
    ]

    for index, (title, artist, tags) in enumerate(songs, start=1):
        tags_html = "".join([f'<span class="tag">{tag}</span>' for tag in tags])
        st.markdown(
            f"""
            <div class="song-row">
                <div class="song-num">{index}</div>
                <div>
                    <div class="song-title">{title}</div>
                    <div class="song-artist">{artist}</div>
                    <div class="song-tags">{tags_html}</div>
                </div>
                <div class="play-btn">▶</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def render_signals_panel() -> None:
    st.markdown(
        """
        <div class="signals-card">
            <div class="signals-title">Journey Signals</div>

            <div class="signal-row">
                <div class="signal-label">Style</div>
                <div class="signal-value">Psychedelic Rock, Blues Rock</div>
            </div>

            <div class="signal-row">
                <div class="signal-label">Time</div>
                <div class="signal-value">Late 1960s to Early 1970s</div>
            </div>

            <div class="signal-row">
                <div class="signal-label">Emotion</div>
                <div class="signal-value">Dark, Mysterious, Transcendent, Intense</div>
            </div>

            <div class="signal-row">
                <div class="signal-label">Influence</div>
                <div class="signal-value">American Folk, Blues Tradition, Psychedelic Counterculture</div>
            </div>

            <div class="signal-row">
                <div class="signal-label">Songs Requested</div>
                <div class="signal-value">6</div>
            </div>

            <div class="wave">~ waveform visualization placeholder ~</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def main() -> None:
    inject_styles()
    render_topbar()
    content_left, content_right = st.columns([2.1, 1], gap="large")
    with content_left:
        render_left_panel()
    with content_right:
        st.write("")
        st.write("")
        render_signals_panel()


if __name__ == "__main__":
    main()
