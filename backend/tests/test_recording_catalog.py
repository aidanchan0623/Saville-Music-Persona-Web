from __future__ import annotations

from datetime import date
from datetime import datetime, timedelta, timezone
import sqlite3

from app.analysis.normalizer import normalise_collection
from app.analysis.taste_model import source_genres_for_artist
from app.data.artist_genres import clusters_for_genres
from app.data.genre_taxonomy import INTERNAL_GENRES, normalise_external_genres
from app.database.recording_catalog import RecordingCatalog
from app.services.recording_genre_service import _release_metadata, exact_recording_candidates


def history_item(video_id: str, title: str, played: str, *, duration: int = 240, album: str | None = "Album") -> dict:
    return {
        "videoId": video_id,
        "title": title,
        "artists": [{"name": "Example Artist"}],
        "album": album,
        "duration_seconds": duration,
        "played": played,
    }


def test_event_deduplication_is_separate_from_recording_deduplication(tmp_path) -> None:
    normalised = normalise_collection(
        {
            "history": [
                history_item("same-video", "November Rain", "2026-07-01T12:32:00Z"),
                history_item("same-video", "November Rain", "2026-07-02T07:10:00Z"),
                history_item("same-video", "November Rain", "2026-07-02T13:45:00Z"),
                # Overlapping Takeout copy of the third occurrence.
                history_item("same-video", "November Rain", "2026-07-02T13:45:00Z"),
            ]
        },
        today=date(2026, 7, 3),
    )
    catalog = RecordingCatalog(tmp_path / "catalog.db")
    stats = catalog.sync_normalised(normalised)

    assert normalised["metadata"]["play_count"] == 3
    assert stats["eventsIndexed"] == 3
    assert catalog.summary()["recordings"] == 1
    assert catalog.summary()["listening_events"] == 3


def test_event_index_is_partitioned_by_profile_source(tmp_path) -> None:
    catalog = RecordingCatalog(tmp_path / "catalog.db")
    youtube = normalise_collection(
        {"source": "google_takeout", "history": [history_item("yt", "YouTube Song", "2026-07-01T12:00:00Z")]},
        today=date(2026, 7, 2),
    )
    spotify = normalise_collection(
        {
            "source": "spotify",
            "history": [{
                "source": "spotify",
                "source_track_id": "spotify:track:one",
                "title": "Spotify Song",
                "artists": [{"name": "Example Artist"}],
                "played": "2026-07-01T13:00:00Z",
            }],
        },
        today=date(2026, 7, 2),
    )

    catalog.sync_normalised(youtube, profile_source="youtube")
    catalog.sync_normalised(spotify, profile_source="spotify")

    assert catalog.summary()["listening_events"] == 2


def test_identical_events_can_exist_in_separate_anonymous_sessions(tmp_path) -> None:
    catalog = RecordingCatalog(tmp_path / "catalog.db")
    normalised = normalise_collection(
        {"source": "google_takeout", "history": [history_item("shared", "Shared Song", "2026-07-01T12:00:00Z")]},
        today=date(2026, 7, 2),
    )

    catalog.sync_normalised(normalised, profile_source=f"session:{'a' * 64}:youtube")
    catalog.sync_normalised(normalised, profile_source=f"session:{'b' * 64}:youtube")

    assert catalog.summary()["listening_events"] == 2


def test_versions_are_not_merged_but_presentation_variants_can_be(tmp_path) -> None:
    catalog = RecordingCatalog(tmp_path / "catalog.db")
    original = {"video_id": "original", "title": "Signal", "primary_artist": "Band", "album": "One", "duration_seconds": 240}
    live = {"video_id": "live", "title": "Signal (Live)", "primary_artist": "Band", "album": "One", "duration_seconds": 240}
    lyric = {"video_id": "lyric", "title": "Signal (Official Lyric Video)", "primary_artist": "Band", "album": "One", "duration_seconds": 240}

    original_resolution = catalog.resolve_track(original)
    live_resolution = catalog.resolve_track(live)
    lyric_resolution = catalog.resolve_track(lyric)

    assert original_resolution and live_resolution and lyric_resolution
    assert original_resolution.recording_id != live_resolution.recording_id
    assert original_resolution.recording_id == lyric_resolution.recording_id


def test_title_and_artist_alone_do_not_create_permanent_recording(tmp_path) -> None:
    catalog = RecordingCatalog(tmp_path / "catalog.db")
    weak = {"title": "Shared Name", "primary_artist": "Artist"}

    assert catalog.resolve_track(weak) is None
    assert catalog.summary()["recordings"] == 0


def test_release_metadata_prefers_the_earliest_valid_release_group() -> None:
    result = _release_metadata(
        [
            {"title": "Later Album", "date": "2011-04-02", "release-group": {"id": "later", "primary-type": "Album"}},
            {"title": "Original Single", "date": "2007-09-01", "release-group": {"id": "original", "primary-type": "Single"}},
        ]
    )

    assert result == {"releaseYear": 2007, "album": "Original Single", "releaseGroupId": "original"}


def test_confidence_components_remain_inspectable_and_gate_application(tmp_path) -> None:
    catalog = RecordingCatalog(tmp_path / "catalog.db")
    track = {"video_id": "genre-video", "title": "Song", "primary_artist": "Artist", "duration_seconds": 200}
    resolution = catalog.resolve_track(track)
    assert resolution
    catalog.save_evidence(
        resolution.recording_id,
        provider="test",
        provider_recording_id="provider-id",
        raw_genres=["melodic house & techno"],
        identity_confidence=0.99,
        evidence_confidence=0.9,
    )
    assignment = catalog.save_assignment(
        resolution.recording_id,
        primary_genre="House",
        secondary_genres=["Techno / Trance"],
        identity_confidence=0.99,
        evidence_confidence=0.9,
        normalisation_confidence=0.98,
        source_summary=["test"],
    )

    assert assignment["autoApplied"] is True
    assert assignment["identityConfidence"] == 0.99
    assert assignment["evidenceConfidence"] == 0.9
    assert assignment["normalisationConfidence"] == 0.98
    details = catalog.details(resolution.recording_id)
    assert details and details["genreAssignment"]["combinedConfidence"] == assignment["combinedConfidence"]


def test_low_identity_evidence_is_saved_but_not_applied(tmp_path) -> None:
    catalog = RecordingCatalog(tmp_path / "catalog.db")
    resolution = catalog.resolve_track({"video_id": "uncertain", "title": "Song", "primary_artist": "Artist"})
    assert resolution
    assignment = catalog.save_assignment(
        resolution.recording_id,
        primary_genre="Mainstream Pop",
        secondary_genres=[],
        identity_confidence=0.65,
        evidence_confidence=0.95,
        normalisation_confidence=0.98,
        source_summary=["test"],
    )
    assert assignment["autoApplied"] is False


def test_stale_taxonomy_assignment_is_rebuilt_from_durable_evidence(tmp_path) -> None:
    db_path = tmp_path / "catalog.db"
    catalog = RecordingCatalog(db_path)
    resolution = catalog.resolve_track({"video_id": "post-punk", "title": "Song", "primary_artist": "Artist"})
    assert resolution
    catalog.save_evidence(
        resolution.recording_id,
        provider="musicbrainz",
        provider_recording_id="mbid",
        raw_genres=["new wave", "rock", "post-punk"],
        identity_confidence=0.95,
        evidence_confidence=0.92,
    )
    catalog.save_assignment(
        resolution.recording_id,
        primary_genre="Classic Rock / Hard Rock",
        secondary_genres=[],
        identity_confidence=0.95,
        evidence_confidence=0.92,
        normalisation_confidence=0.98,
        source_summary=["musicbrainz"],
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE genre_assignments SET taxonomy_version = 1 WHERE recording_id = ?", (resolution.recording_id,))

    assert catalog.rebuild_stale_assignments() == 1
    assert catalog.assignment(resolution.recording_id)["primaryGenre"] == "Post-Punk / Goth / Darkwave"


def test_lookup_failure_backoff_skips_immediate_retries(tmp_path) -> None:
    catalog = RecordingCatalog(tmp_path / "catalog.db")
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    catalog.record_failure("recording:artist:song", None, "musicbrainz", "not_found", future)

    assert catalog.can_retry("recording:artist:song") is False
    assert catalog.can_retry("recording:artist:different-song") is True
    assert catalog.blocked_lookup_keys() == {"recording:artist:song"}


def test_unknown_duration_alias_does_not_grow_on_every_sync(tmp_path) -> None:
    catalog = RecordingCatalog(tmp_path / "catalog.db")
    track = {"video_id": "same", "title": "Song", "primary_artist": "Artist"}
    catalog.resolve_track(track)
    catalog.resolve_track(track)
    catalog.resolve_track(track)

    assert catalog.summary()["recording_aliases"] == 1


def test_supplemental_assignment_flows_to_track_genres(tmp_path) -> None:
    catalog = RecordingCatalog(tmp_path / "catalog.db")
    normalised = normalise_collection(
        {"history": [history_item("house-video", "Unknown House Song", "2026-07-01T12:00:00Z")]},
        today=date(2026, 7, 2),
    )
    catalog.sync_normalised(normalised)
    recording_id = normalised["tracks"][0]["recording_id"]
    catalog.save_assignment(
        recording_id,
        primary_genre="House",
        secondary_genres=[],
        identity_confidence=1.0,
        evidence_confidence=0.9,
        normalisation_confidence=0.98,
        source_summary=["musicbrainz_recording"],
    )
    catalog.sync_normalised(normalised)
    track = normalised["tracks"][0]

    assert "House" in source_genres_for_artist(track, normalised.get("artist_metadata"), "Example Artist")
    assert "Electronic / Atmospheric" in clusters_for_genres(["House"])


def test_taxonomy_has_thirty_stable_buckets_and_regional_coverage() -> None:
    assert len(INTERNAL_GENRES) == 30
    assert normalise_external_genres(["mandarin pop"]).primary_genre == "Mandopop"
    assert normalise_external_genres(["malaysian rock"]).primary_genre == "Malay / Nusantara Rock & Indie"
    assert normalise_external_genres(["kollywood soundtrack"]).primary_genre == "Tamil / Indian Film & Pop"
    assert normalise_external_genres(["melodic house & techno"]).primary_genre == "House"
    assert normalise_external_genres(["pop rock"]).primary_genre == "Pop Rock"
    assert normalise_external_genres(["new wave", "rock", "post-punk"]).primary_genre == "Post-Punk / Goth / Darkwave"
    assert normalise_external_genres(["alternative rock", "pop"]).primary_genre == "Alternative / Indie Rock"
    assert normalise_external_genres(["c-pop", "r&b"]).primary_genre == "Mandopop"
    assert normalise_external_genres(["breakcore"]).primary_genre == "EDM / Bass Music"
    assert normalise_external_genres(["electronic"]).primary_genre == "EDM / Bass Music"
    assert normalise_external_genres(["post-punk"]).primary_genre == "Post-Punk / Goth / Darkwave"
    assert normalise_external_genres(["pop rap"]).primary_genre == "Hip-Hop / Rap"
    assert normalise_external_genres(["hip-hop", "k-pop"]).primary_genre == "K-pop"


def test_musicbrainz_candidate_requires_version_and_uses_duration_for_identity() -> None:
    track = {"title": "Signal (Live)", "primary_artist": "Band", "duration_seconds": 241}
    candidates = [
        {
            "id": "studio",
            "score": 100,
            "title": "Signal",
            "length": 240000,
            "artist-credit": [{"artist": {"name": "Band"}}],
        },
        {
            "id": "live",
            "score": 100,
            "title": "Signal (Live)",
            "length": 241000,
            "artist-credit": [{"artist": {"name": "Band"}}],
        },
    ]

    matches = exact_recording_candidates(candidates, track)
    assert len(matches) == 1
    assert matches[0][0]["id"] == "live"
    assert matches[0][1] == 0.92


def test_musicbrainz_title_artist_only_stays_below_auto_identity_threshold() -> None:
    matches = exact_recording_candidates(
        [{"id": "weak", "score": 100, "title": "Signal", "artist-credit": [{"artist": {"name": "Band"}}]}],
        {"title": "Signal", "primary_artist": "Band"},
    )
    assert matches[0][1] == 0.65


def test_musicbrainz_album_supports_medium_identity_without_duration() -> None:
    matches = exact_recording_candidates(
        [{
            "id": "album-match",
            "score": 100,
            "title": "Signal",
            "artist-credit": [{"artist": {"name": "Band"}}],
            "releases": [{"title": "The Album"}],
        }],
        {"title": "Signal", "primary_artist": "Band", "album": "The Album"},
    )
    assert matches[0][1] == 0.86
