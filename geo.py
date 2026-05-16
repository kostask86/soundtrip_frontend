"""Geocoding and journey map point builders for playlist songs."""

from __future__ import annotations

import html
import ssl
import time
from dataclasses import dataclass
from typing import Any

import certifi
import requests
import streamlit as st
from geopy.exc import GeocoderServiceError, GeocoderTimedOut
from geopy.exc import GeocoderUnavailable
from geopy.geocoders import Nominatim

from soundtrip_client import SoundTripAPIError, get_song

NOMINATIM_USER_AGENT = "soundtrip-frontend/1.0 (journey-map)"
GEOCODE_MIN_INTERVAL_SEC = 1.1
OPEN_METEO_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"


@dataclass(frozen=True)
class SongLocation:
    city: str | None
    country: str | None

    @property
    def display(self) -> str:
        parts = [p for p in (self.city, self.country) if p]
        return ", ".join(parts) if parts else "Location unknown"

    @property
    def is_mappable(self) -> bool:
        return bool((self.city or "").strip() or (self.country or "").strip())


@dataclass
class JourneyPoint:
    order: int
    title: str
    artist: str
    city: str | None
    country: str | None
    lat: float
    lng: float
    tooltip_html: str
    location_label: str


@dataclass
class ClusterSong:
    order: int
    title: str
    artist: str
    tooltip_html: str


@dataclass
class MapCluster:
    lat: float
    lng: float
    city: str | None
    country: str | None
    location_label: str
    songs: list[ClusterSong]

    @property
    def song_count(self) -> int:
        return len(self.songs)


def _song_id(song: dict[str, Any]) -> int | str | None:
    raw = song.get("song_id")
    if raw is None:
        raw = song.get("id")
    if raw is None:
        raw = song.get("songId")
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


def _coerce_location_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    text = str(value).strip()
    return text or None


def extract_location(song: dict[str, Any]) -> SongLocation:
    city = _coerce_location_str(song.get("city") or song.get("location_city"))
    country = _coerce_location_str(song.get("country") or song.get("location_country"))

    nested = song.get("location")
    if isinstance(nested, dict):
        city = city or _coerce_location_str(nested.get("city"))
        country = country or _coerce_location_str(nested.get("country"))

    return SongLocation(city=city, country=country)


def _location_cache_key(playlist: dict[str, Any]) -> str:
    pid = playlist.get("id")
    songs = playlist.get("songs") or []
    n = len(songs) if isinstance(songs, list) else 0
    return f"pl:{pid}:n:{n}"


def songs_have_locations(songs: list[dict[str, Any]]) -> bool:
    for song in songs:
        if isinstance(song, dict) and extract_location(song).is_mappable:
            return True
    return False


def enrich_songs_with_locations(
    api_base: str,
    playlist: dict[str, Any],
    songs: list[dict[str, Any]],
    *,
    force_refresh: bool = False,
) -> list[dict[str, Any]]:
    """Merge city/country from song rows or fetch via GET /songs/{id} when missing."""
    if songs_have_locations(songs) and not force_refresh:
        return songs

    cache_key = _location_cache_key(playlist)
    cached = st.session_state.get("journey_song_locations")
    if not force_refresh and isinstance(cached, dict) and cached.get("key") == cache_key:
        cached_songs = cached.get("songs")
        if isinstance(cached_songs, list) and songs_have_locations(cached_songs):
            return cached_songs

    enriched: list[dict[str, Any]] = []
    for song in songs:
        if not isinstance(song, dict):
            continue
        merged = dict(song)
        loc = extract_location(merged)
        if not loc.is_mappable:
            sid = _song_id(merged)
            if sid is not None:
                try:
                    detail = get_song(api_base, sid)
                    for key in ("city", "country", "location_city", "location_country"):
                        if detail.get(key) is not None and not merged.get(key):
                            merged[key] = detail[key]
                except (SoundTripAPIError, Exception):
                    pass
        enriched.append(merged)

    st.session_state.journey_song_locations = {"key": cache_key, "songs": enriched}
    return enriched


def _geocode_query(city: str | None, country: str | None) -> str | None:
    city_s = (city or "").strip()
    country_s = (country or "").strip()
    if not city_s and not country_s:
        return None
    if city_s and country_s:
        return f"{city_s}, {country_s}"
    return city_s or country_s


def _nominatim_geocode(query: str) -> tuple[float, float] | None:
    ctx = ssl.create_default_context(cafile=certifi.where())
    geolocator = Nominatim(user_agent=NOMINATIM_USER_AGENT, timeout=15, ssl_context=ctx)
    try:
        location = geolocator.geocode(query, addressdetails=False)
    except (GeocoderTimedOut, GeocoderServiceError, GeocoderUnavailable, OSError):
        return None
    if location is None:
        return None
    return (float(location.latitude), float(location.longitude))


def _open_meteo_geocode(query: str) -> tuple[float, float] | None:
    try:
        resp = requests.get(
            OPEN_METEO_GEOCODE_URL,
            params={"name": query, "count": 1, "language": "en", "format": "json"},
            timeout=15,
            verify=certifi.where(),
        )
        if not resp.ok:
            return None
        results = resp.json().get("results")
        if not isinstance(results, list) or not results:
            return None
        top = results[0]
        lat = top.get("latitude")
        lng = top.get("longitude")
        if lat is None or lng is None:
            return None
        return (float(lat), float(lng))
    except (requests.RequestException, TypeError, ValueError):
        return None


@st.cache_data(show_spinner=False, ttl=60 * 60 * 24 * 7)
def geocode_city_country(
    city: str | None,
    country: str | None,
    *,
    _cache_version: int = 2,
) -> tuple[float, float] | None:
    query = _geocode_query(city, country)
    if not query:
        return None

    coords = _nominatim_geocode(query)
    if coords is not None:
        return coords
    return _open_meteo_geocode(query)


def _geocode_batch(locations: list[SongLocation]) -> dict[tuple[str | None, str | None], tuple[float, float] | None]:
    unique: list[tuple[str | None, str | None]] = []
    seen: set[tuple[str | None, str | None]] = set()
    for loc in locations:
        if not loc.is_mappable:
            continue
        key = (loc.city, loc.country)
        if key not in seen:
            seen.add(key)
            unique.append(key)

    results: dict[tuple[str | None, str | None], tuple[float, float] | None] = {}
    for idx, key in enumerate(unique):
        if idx > 0:
            time.sleep(GEOCODE_MIN_INTERVAL_SEC)
        lat_lng = geocode_city_country(key[0], key[1])
        results[key] = lat_lng
    return results


def _tooltip_html(song: dict[str, Any], loc: SongLocation) -> str:
    title = str(song.get("title") or "Untitled")
    artist = str(song.get("artist") or "")
    album_obj = song.get("album")
    album_name = ""
    if isinstance(album_obj, dict):
        album_name = str(album_obj.get("name") or album_obj.get("title") or "").strip()
    elif isinstance(album_obj, str):
        album_name = album_obj.strip()
    year = str(song.get("release_year") or "").strip()

    tag_bits: list[str] = []
    for st_obj in song.get("styles") or []:
        if (
            isinstance(st_obj, dict)
            and str(st_obj.get("role") or "").lower() == "primary"
            and st_obj.get("label")
        ):
            tag_bits.append(f"Style: {st_obj['label']}")
            break
    tm = song.get("time")
    if isinstance(tm, dict) and tm.get("label"):
        tag_bits.append(f"Time: {tm['label']}")
    for em in song.get("emotions") or []:
        if isinstance(em, dict) and em.get("label"):
            tag_bits.append(f"Emotion: {em['label']}")
            break

    lines = [
        f"<b>{title}</b>",
        artist,
        f"Location: {loc.display}",
    ]
    if album_name:
        lines.append(f"Album: {album_name}")
    if year:
        lines.append(f"Year: {year}")
    lines.extend(tag_bits[:3])
    return "<br/>".join(lines)


def build_journey_points(songs: list[dict[str, Any]]) -> tuple[list[JourneyPoint], int]:
    """
    Geocode songs and return mappable points plus count of songs that could not be placed.
    """
    locations = [extract_location(s) for s in songs if isinstance(s, dict)]
    coord_map = _geocode_batch(locations)

    points: list[JourneyPoint] = []

    for order, song in enumerate(songs, start=1):
        if not isinstance(song, dict):
            continue
        loc = extract_location(song)
        if not loc.is_mappable:
            continue
        coords = coord_map.get((loc.city, loc.country))
        if coords is None:
            coords = geocode_city_country(loc.city, loc.country)
        if coords is None:
            continue
        lat, lng = coords
        points.append(
            JourneyPoint(
                order=order,
                title=str(song.get("title") or "Untitled"),
                artist=str(song.get("artist") or ""),
                city=loc.city,
                country=loc.country,
                lat=lat,
                lng=lng,
                tooltip_html=_tooltip_html(song, loc),
                location_label=loc.display,
            )
        )

    total_songs = len([s for s in songs if isinstance(s, dict)])
    unmapped = max(0, total_songs - len(points))
    return points, unmapped


def _cluster_key(point: JourneyPoint) -> tuple:
    city = (point.city or "").strip().lower()
    country = (point.country or "").strip().lower()
    if city or country:
        return ("place", city, country)
    return ("coord", round(point.lat, 4), round(point.lng, 4))


def build_map_clusters(points: list[JourneyPoint]) -> list[MapCluster]:
    """Group journey points by city/country (one map marker per location)."""
    groups: dict[tuple, list[JourneyPoint]] = {}
    key_order: list[tuple] = []

    for point in points:
        key = _cluster_key(point)
        if key not in groups:
            groups[key] = []
            key_order.append(key)
        groups[key].append(point)

    clusters: list[MapCluster] = []
    for key in key_order:
        members = sorted(groups[key], key=lambda p: p.order)
        first = members[0]
        clusters.append(
            MapCluster(
                lat=first.lat,
                lng=first.lng,
                city=first.city,
                country=first.country,
                location_label=first.location_label,
                songs=[
                    ClusterSong(
                        order=p.order,
                        title=p.title,
                        artist=p.artist,
                        tooltip_html=p.tooltip_html,
                    )
                    for p in members
                ],
            )
        )
    return clusters


def clusters_in_playlist_order(points: list[JourneyPoint], clusters: list[MapCluster]) -> list[MapCluster]:
    """Order clusters by first appearance in the playlist."""
    key_to_cluster = {_cluster_key_from_cluster(c): c for c in clusters}
    ordered: list[MapCluster] = []
    seen: set[tuple] = set()

    for point in sorted(points, key=lambda p: p.order):
        key = _cluster_key(point)
        if key in seen:
            continue
        seen.add(key)
        cluster = key_to_cluster.get(key)
        if cluster is not None:
            ordered.append(cluster)
    return ordered


def _cluster_key_from_cluster(cluster: MapCluster) -> tuple:
    city = (cluster.city or "").strip().lower()
    country = (cluster.country or "").strip().lower()
    if city or country:
        return ("place", city, country)
    return ("coord", round(cluster.lat, 4), round(cluster.lng, 4))


def cluster_tooltip_html(cluster: MapCluster) -> str:
    header = f"<b>{html.escape(cluster.location_label)}</b>"
    items = "".join(
        f"<li>{s.order}. {html.escape(s.title)} — {html.escape(s.artist)}</li>"
        for s in cluster.songs
    )
    return f"{header}<ul style='margin:0.35em 0 0 1em;padding:0;'>{items}</ul>"


def build_cluster_path_segments(clusters_ordered: list[MapCluster]) -> list[list[list[float]]]:
    """Path segments between unique locations in playlist order."""
    if len(clusters_ordered) < 2:
        return []
    segments: list[list[list[float]]] = []
    for i in range(len(clusters_ordered) - 1):
        a, b = clusters_ordered[i], clusters_ordered[i + 1]
        if round(a.lat, 4) == round(b.lat, 4) and round(a.lng, 4) == round(b.lng, 4):
            continue
        segments.append(_arc_path(a.lng, a.lat, b.lng, b.lat))
    return segments


def build_path_segments(points: list[JourneyPoint]) -> list[list[list[float]]]:
    """PathLayer paths as [[lon, lat], ...] for each consecutive pair."""
    if len(points) < 2:
        return []
    segments: list[list[list[float]]] = []
    for i in range(len(points) - 1):
        a, b = points[i], points[i + 1]
        segments.append(_arc_path(a.lng, a.lat, b.lng, b.lat))
    return segments


def _arc_path(lon1: float, lat1: float, lon2: float, lat2: float, steps: int = 24) -> list[list[float]]:
    """Simple great-circle-ish arc via midpoint offset for readability."""
    mid_lon = (lon1 + lon2) / 2.0
    mid_lat = (lat1 + lat2) / 2.0
    dist = ((lon2 - lon1) ** 2 + (lat2 - lat1) ** 2) ** 0.5
    offset = min(12.0, max(1.5, dist * 0.15))
    mid_lat += offset if (lon2 - lon1) >= 0 else -offset

    path: list[list[float]] = []
    for t in range(steps + 1):
        u = t / steps
        # Quadratic bezier through midpoint
        lon = (1 - u) ** 2 * lon1 + 2 * (1 - u) * u * mid_lon + u**2 * lon2
        lat = (1 - u) ** 2 * lat1 + 2 * (1 - u) * u * mid_lat + u**2 * lat2
        path.append([lon, lat])
    return path
