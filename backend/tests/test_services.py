from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings
from app.analysis.thumbnails import best_thumbnail_url
from app.analysis.media import album_cache_failure, album_cache_set, album_id_key, album_name_artist_key, artist_cache_set, artist_id_key, artist_name_key
from app.services.recommendations import dedupe_candidates
from app.services.ytmusic_service import YTMusicService, friendly_auth_error, normalise_artist_name


def test_recommendation_duplicate_removal() -> None:
    candidates = [
        {"videoId": "1", "title": "Song (Official Video)", "artists": [{"name": "Artist"}]},
        {"videoId": "2", "title": "Song", "artists": [{"name": "Artist"}]},
        {"videoId": "3", "title": "Different", "artists": [{"name": "Artist"}]},
    ]
    result = dedupe_candidates(candidates, existing_keys=set(), existing_video_ids=set())
    assert [item["videoId"] for item in result] == ["1", "3"]


def test_no_authenticated_youtube_music_account(tmp_path: Path) -> None:
    settings = Settings()
    settings.private_dir = tmp_path
    settings.ytmusic_auth_file = tmp_path / "oauth.json"
    settings.ytmusic_browser_auth_file = tmp_path / "browser.json"
    settings.ytmusic_client_id = ""
    settings.ytmusic_client_secret = ""
    service = YTMusicService(settings)
    status = service.auth_status()
    assert status["connected"] is False
    assert status["auth_file_exists"] is False


def test_verbose_youtube_account_menu_error_is_sanitized() -> None:
    message = friendly_auth_error(KeyError("Unable to find 'header' on {'multiPageMenuRenderer': {'secret': 'value'}}"), is_browser=True)
    assert "account menu" in message
    assert "secret" not in message


def test_duration_enrichment_uses_public_client_and_retries_legacy_negative_cache() -> None:
    fake = FakeYTMusic(song_pages={"played-often": {"videoDetails": {"lengthSeconds": "242"}}})
    service = fake_service(fake)
    service.public_client = lambda: fake  # type: ignore[method-assign]
    cache: dict[str, object] = {"played-often": {"duration_seconds": None, "duration_source": "ytmusicapi.get_song"}}
    normalised = {
        "tracks": [{"video_id": "played-often", "duration_seconds": None}],
        "play_events": [{"video_id": "played-often"}] * 9,
    }

    stats = service.enrich_duration_cache(normalised, cache, limit=10)

    assert stats["added"] == 1
    assert fake.get_song_calls == ["played-often"]
    assert cache["played-often"]["duration_seconds"] == 242  # type: ignore[index]
    assert cache["played-often"]["status"] == "resolved"  # type: ignore[index]


def test_duration_enrichment_checkpoints_each_hosted_lookup() -> None:
    fake = FakeYTMusic(
        song_pages={
            "first": {"videoDetails": {"lengthSeconds": "180"}},
            "second": {"videoDetails": {"lengthSeconds": "240"}},
            "third": {"videoDetails": {"lengthSeconds": "300"}},
        }
    )
    service = fake_service(fake)
    service.settings.duration_public_batch_limit = 2
    cache: dict[str, object] = {}
    normalised = {
        "tracks": [
            {"video_id": "first", "duration_seconds": None},
            {"video_id": "second", "duration_seconds": None},
            {"video_id": "third", "duration_seconds": None},
        ],
        "play_events": [
            {"video_id": "first"},
            {"video_id": "second"},
            {"video_id": "third"},
        ],
    }
    checkpoints: list[set[str]] = []
    progress: list[tuple[int, int, int]] = []

    stats = service.enrich_duration_cache(
        normalised,
        cache,
        limit=10,
        checkpoint=lambda value: checkpoints.append(set(value)),
        progress_callback=lambda completed, total, added: progress.append((completed, total, added)),
    )

    assert stats == {
        "attempted": 2,
        "added": 2,
        "failed": 0,
        "api_batches": 0,
        "fallback_attempted": 2,
        "remaining": 1,
    }
    assert checkpoints == [{"first"}, {"first", "second"}]
    assert progress[-1] == (2, 2, 2)


def test_duration_enrichment_verifies_unknown_exact_video_as_music() -> None:
    fake = FakeYTMusic(
        song_pages={
            "unknown-video": {
                "videoDetails": {
                    "lengthSeconds": "201",
                    "musicVideoType": "MUSIC_VIDEO_TYPE_OMV",
                    "title": "Verified Song",
                    "author": "Verified Artist - Topic",
                }
            }
        }
    )
    service = fake_service(fake)
    cache: dict[str, object] = {}
    normalised = {
        "tracks": [{"video_id": "unknown-video", "duration_seconds": 201}],
        "play_events": [],
        "excluded_play_events": [{"video_id": "unknown-video", "music_classification": "unknown"}] * 3,
    }

    stats = service.enrich_duration_cache(normalised, cache, limit=10)

    assert stats["added"] == 1
    assert cache["unknown-video"]["music_classification"] == "confirmed_music"  # type: ignore[index]
    assert cache["unknown-video"]["media_author"] == "Verified Artist - Topic"  # type: ignore[index]


def test_duration_enrichment_respects_unknown_identity_retry_window() -> None:
    fake = FakeYTMusic(song_pages={})
    service = fake_service(fake)
    cache: dict[str, object] = {}
    normalised = {
        "tracks": [{"video_id": "unavailable-video", "duration_seconds": 201}],
        "play_events": [],
        "excluded_play_events": [{"video_id": "unavailable-video", "music_classification": "unknown"}],
    }

    first = service.enrich_duration_cache(normalised, cache, limit=10)
    second = service.enrich_duration_cache(normalised, cache, limit=10)

    assert first["attempted"] == 1
    assert second["attempted"] == 0
    assert fake.get_song_calls == ["unavailable-video"]
    assert cache["unavailable-video"]["music_classification_next_retry_at"]  # type: ignore[index]


def test_duration_enrichment_batches_exact_video_ids_through_official_api(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings()
    settings.youtube_data_api_key = "local-test-key"
    service = YTMusicService(settings)
    calls: list[dict[str, object]] = []

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"items": [{"id": "first", "contentDetails": {"duration": "PT3M42S"}}, {"id": "second", "contentDetails": {"duration": "PT1H2M3S"}}]}

    def fake_get(_url: str, **kwargs: object) -> Response:
        calls.append(kwargs)
        return Response()

    monkeypatch.setattr("app.services.ytmusic_service.httpx.get", fake_get)
    service.public_client = lambda: (_ for _ in ()).throw(AssertionError("public fallback should not run"))  # type: ignore[method-assign]
    cache: dict[str, object] = {}
    normalised = {
        "tracks": [{"video_id": "first", "duration_seconds": None}, {"video_id": "second", "duration_seconds": None}],
        "play_events": [{"video_id": "first"}] * 4 + [{"video_id": "second"}],
    }

    stats = service.enrich_duration_cache(normalised, cache, limit=10)

    assert stats["api_batches"] == 1
    assert stats["added"] == 2
    assert calls[0]["params"]["id"] == "first,second"  # type: ignore[index]
    assert cache["first"]["duration_seconds"] == 222  # type: ignore[index]
    assert cache["second"]["duration_seconds"] == 3723  # type: ignore[index]


def test_artist_image_enrichment_uses_existing_artist_id() -> None:
    fake = FakeYTMusic(
        artist_pages={
            "UC-a": {
                "artist": "Artist A",
                "browseId": "UC-a",
                "thumbnails": [
                    {"url": "https://img.example/a-60.jpg", "width": 60, "height": 60},
                    {"url": "https://img.example/a-600.jpg", "width": 600, "height": 600},
                ],
            }
        }
    )
    cache: dict[str, object] = {}
    stats = fake_service(fake).enrich_artist_image_cache({"history": [_history_artist("Artist A", "UC-a")]}, cache)
    assert stats["added"] == 1
    assert fake.get_artist_calls == ["UC-a"]
    assert fake.search_calls == []
    assert cache_record(cache, "Artist A", "UC-a")["url"] == "https://img.example/a-600.jpg"
    assert cache_record(cache, "Artist A", "UC-a")["mediaType"] == "artist"


def test_album_image_enrichment_uses_existing_album_id() -> None:
    fake = FakeYTMusic(
        album_pages={
            "MPRE-a": {
                "title": "The Black Parade",
                "browseId": "MPRE-a",
                "thumbnails": [
                    {"url": "https://img.example/black-parade-120.jpg", "width": 120, "height": 120},
                    {"url": "https://img.example/black-parade-544.jpg", "width": 544, "height": 544},
                ],
            }
        }
    )
    cache: dict[str, object] = {}
    stats = fake_service(fake).enrich_album_image_cache({"history": [_history_album("The Black Parade", "My Chemical Romance", "MPRE-a")]}, cache)
    assert stats["added"] == 1
    assert fake.get_album_calls == ["MPRE-a"]
    assert fake.search_calls == []
    record = album_cache_record(cache, "The Black Parade", "My Chemical Romance", "MPRE-a")
    assert record["album_image_url"] == "https://img.example/black-parade-544.jpg"
    assert record["mediaType"] == "album"


def test_album_image_enrichment_searches_takeout_album_names() -> None:
    fake = FakeYTMusic(
        search_results={
            "Sempiternal Bring Me The Horizon": [
                {"title": "Sempiternal", "browseId": "MPRE-bmth", "artists": [{"name": "Bring Me The Horizon"}]},
            ]
        },
        album_pages={
            "MPRE-bmth": {
                "title": "Sempiternal",
                "browseId": "MPRE-bmth",
                "thumbnails": [{"url": "https://img.example/sempiternal.jpg", "width": 512, "height": 512}],
            }
        },
    )
    cache: dict[str, object] = {}
    stats = fake_service(fake).enrich_album_image_cache({"takeout_history": [_history_album("Sempiternal", "Bring Me The Horizon")]}, cache)
    assert stats["added"] == 1
    assert fake.search_calls == [("Sempiternal Bring Me The Horizon", "albums", 5)]
    assert fake.get_album_calls == ["MPRE-bmth"]
    assert album_cache_record(cache, "Sempiternal", "Bring Me The Horizon")["album_image_url"] == "https://img.example/sempiternal.jpg"


def test_album_image_enrichment_prioritises_visible_albums() -> None:
    fake = FakeYTMusic(
        search_results={
            "Current Album Current Artist": [{"title": "Current Album", "browseId": "MPRE-current", "artists": [{"name": "Current Artist"}]}],
            "History Album History Artist": [{"title": "History Album", "browseId": "MPRE-history", "artists": [{"name": "History Artist"}]}],
        },
        album_pages={
            "MPRE-current": {"title": "Current Album", "browseId": "MPRE-current", "thumbnails": [{"url": "https://img.example/current-album.jpg", "width": 512, "height": 512}]},
            "MPRE-history": {"title": "History Album", "browseId": "MPRE-history", "thumbnails": [{"url": "https://img.example/history-album.jpg", "width": 512, "height": 512}]},
        },
    )
    cache: dict[str, object] = {}

    stats = fake_service(fake).enrich_album_image_cache(
        {"history": [_history_album("History Album", "History Artist")]},
        cache,
        limit=1,
        preferred_albums=[{"album": "Current Album", "artist": "Current Artist"}],
    )

    assert stats["added"] == 1
    assert fake.search_calls[0][0] == "Current Album Current Artist"
    assert album_cache_record(cache, "Current Album", "Current Artist")["album_image_url"] == "https://img.example/current-album.jpg"


def test_album_image_enrichment_uses_public_catalogue_when_saved_auth_is_stale() -> None:
    stale = FakeYTMusic(search_results={"Koi No Yokan Deftones": []})
    public = FakeYTMusic(
        search_results={
            "Koi No Yokan Deftones": [{"title": "Koi No Yokan", "browseId": "MPRE-koi", "artists": [{"name": "Deftones"}]}],
        },
        album_pages={
            "MPRE-koi": {"title": "Koi No Yokan", "browseId": "MPRE-koi", "thumbnails": [{"url": "https://img.example/koi-no-yokan.jpg", "width": 512, "height": 512}]},
        },
    )
    service = YTMusicService(Settings())
    service.client = lambda prefer_browser=True: stale  # type: ignore[method-assign]
    service.public_client = lambda: public  # type: ignore[method-assign]
    cache: dict[str, object] = {}
    album_cache_set(
        cache,
        album_cache_failure("Koi No Yokan", "Deftones", "MPRE-stale", "no_exact_album_match"),
        album_id="MPRE-stale",
        album="Koi No Yokan",
        artist="Deftones",
    )

    stats = service.enrich_album_image_cache(
        {"history": [_history_album("Koi No Yokan", "Deftones")]},
        cache,
        limit=1,
        preferred_albums=[{"album": "Koi No Yokan", "artist": "Deftones"}],
    )

    assert stats["added"] == 1
    assert stale.search_calls == []
    assert public.search_calls == [("Koi No Yokan Deftones", "albums", 5)]
    assert album_cache_record(cache, "Koi No Yokan", "Deftones")["album_image_url"] == "https://img.example/koi-no-yokan.jpg"


def test_artist_image_enrichment_prioritises_preferred_artists() -> None:
    fake = FakeYTMusic(
        search_results={
            "Current Artist": [{"artist": "Current Artist", "browseId": "UC-current"}],
            "History Artist": [{"artist": "History Artist", "browseId": "UC-history"}],
        },
        artist_pages={
            "UC-current": {"artist": "Current Artist", "browseId": "UC-current", "thumbnails": [{"url": "https://img.example/current.jpg", "width": 500, "height": 500}]},
            "UC-history": {"artist": "History Artist", "browseId": "UC-history", "thumbnails": [{"url": "https://img.example/history.jpg", "width": 500, "height": 500}]},
        },
    )
    cache: dict[str, object] = {}
    stats = fake_service(fake).enrich_artist_image_cache({"history": [_history_artist("History Artist")]}, cache, preferred_artists=["Current Artist"])
    assert stats["added"] == 2
    assert fake.search_calls[0][0] == "Current Artist"
    assert cache_record(cache, "Current Artist", "UC-current")["url"] == "https://img.example/current.jpg"


def test_artist_image_enrichment_falls_back_to_public_client() -> None:
    fake = FakeYTMusic(
        search_results={"Artist A": [{"artist": "Artist A", "browseId": "UC-a"}]},
        artist_pages={"UC-a": {"artist": "Artist A", "browseId": "UC-a", "thumbnails": [{"url": "https://img.example/a.jpg", "width": 500, "height": 500}]}},
    )
    service = YTMusicService(Settings())
    service.client = lambda prefer_browser=True: (_ for _ in ()).throw(RuntimeError("auth unavailable"))  # type: ignore[method-assign]
    service.public_client = lambda: fake  # type: ignore[method-assign]
    cache: dict[str, object] = {}
    stats = service.enrich_artist_image_cache({"history": [_history_artist("Artist A")]}, cache)
    assert stats["added"] == 1
    assert fake.search_calls == [("Artist A", "artists", 5)]
    assert cache_record(cache, "Artist A", "UC-a")["url"] == "https://img.example/a.jpg"


def test_artist_image_enrichment_does_not_choose_non_exact_search_result() -> None:
    fake = FakeYTMusic(search_results={"Artist A": [{"artist": "Artist Adjacent", "browseId": "UC-other", "thumbnails": [{"url": "https://img.example/other.jpg"}]}]})
    cache: dict[str, object] = {}
    stats = fake_service(fake).enrich_artist_image_cache({"history": [_history_artist("Artist A")]}, cache)
    assert stats["failed"] == 1
    assert cache_record(cache, "Artist A")["url"] is None
    assert cache_record(cache, "Artist A")["failureReason"] == "no_exact_artist_match"
    assert fake.get_artist_calls == []


def test_artist_image_enrichment_selects_exact_match_among_multiple_results() -> None:
    fake = FakeYTMusic(
        search_results={
            "Artist A": [
                {"artist": "Artist Adjacent", "browseId": "UC-other"},
                {"artist": "Artist A", "browseId": "UC-a"},
            ]
        },
        artist_pages={"UC-a": {"artist": "Artist A", "browseId": "UC-a", "thumbnails": [{"url": "https://img.example/a.jpg", "width": 512, "height": 512}]}},
    )
    cache: dict[str, object] = {}
    stats = fake_service(fake).enrich_artist_image_cache({"history": [_history_artist("Artist A")]}, cache)
    assert stats["added"] == 1
    assert fake.get_artist_calls == ["UC-a"]
    assert cache_record(cache, "Artist A", "UC-a")["browse_id"] == "UC-a"


def test_artist_image_enrichment_records_missing_thumbnails() -> None:
    fake = FakeYTMusic(
        search_results={"Artist A": [{"artist": "Artist A", "browseId": "UC-a"}]},
        artist_pages={"UC-a": {"artist": "Artist A", "browseId": "UC-a", "thumbnails": []}},
    )
    cache: dict[str, object] = {}
    stats = fake_service(fake).enrich_artist_image_cache({"history": [_history_artist("Artist A")]}, cache)
    assert stats["failed"] == 1
    assert cache_record(cache, "Artist A", "UC-a")["failureReason"] == "missing_thumbnails"


def test_best_thumbnail_prefers_highest_resolution_https() -> None:
    assert best_thumbnail_url(
        [
            {"url": "http://img.example/insecure.jpg", "width": 2000, "height": 2000},
            {"url": "https://img.example/small.jpg", "width": 120, "height": 120},
            {"url": "https://img.example/wide.jpg", "width": 640},
            {"url": "https://img.example/large.jpg", "width": 512, "height": 512},
        ]
    ) == "https://img.example/large.jpg"


def test_artist_image_enrichment_cache_hit_skips_upstream_calls() -> None:
    fake = FakeYTMusic()
    cache = {
        "schemaVersion": 2,
        "items": {
            "artist-name:artist a": {
                "schemaVersion": 2,
                "mediaType": "artist",
                "entityName": "Artist A",
                "url": "https://img.example/cached.jpg",
                "thumbnail_url": "https://img.example/cached.jpg",
                "thumbnails": [{"url": "https://img.example/cached.jpg"}],
            }
        },
    }
    stats = fake_service(fake).enrich_artist_image_cache({"history": [_history_artist("Artist A")]}, cache)
    assert stats["attempted"] == 0
    assert fake.search_calls == []
    assert fake.get_artist_calls == []


def test_artist_image_enrichment_replaces_ambiguous_cached_gem_match() -> None:
    fake = FakeYTMusic(
        artist_pages={
            "UCBRh2Z_U1Lw9-YJ-XGZ8M2Q": {
                "artist": "鄧紫棋 - G.E.M.",
                "browseId": "UCBRh2Z_U1Lw9-YJ-XGZ8M2Q",
                "thumbnails": [{"url": "https://img.example/gem.jpg", "width": 512, "height": 512}],
            }
        }
    )
    cache = {
        "schemaVersion": 2,
        "items": {
            "artist-name:g e m": {
                "schemaVersion": 2,
                "mediaType": "artist",
                "browse_id": "UC-wrong-gem",
                "url": "https://img.example/wrong.jpg",
                "thumbnail_url": "https://img.example/wrong.jpg",
                "thumbnails": [{"url": "https://img.example/wrong.jpg"}],
            }
        },
    }

    stats = fake_service(fake).enrich_artist_image_cache({"history": [_history_artist("G.E.M.")]}, cache)

    assert stats["added"] == 1
    assert fake.search_calls == []
    assert fake.get_artist_calls == ["UCBRh2Z_U1Lw9-YJ-XGZ8M2Q"]
    assert cache_record(cache, "G.E.M.")["url"] == "https://img.example/gem.jpg"
    assert "artist:UC-wrong-gem" not in cache["items"]


def test_artist_image_enrichment_retries_legacy_curated_failure_once() -> None:
    browse_id = "UCBRh2Z_U1Lw9-YJ-XGZ8M2Q"
    fake = FakeYTMusic(
        artist_pages={
            browse_id: {
                "artist": "G.E.M.",
                "browseId": browse_id,
                "thumbnails": [{"url": "https://img.example/gem-recovered.jpg", "width": 512, "height": 512}],
            }
        }
    )
    cache: dict[str, object] = {}
    artist_cache_set(
        cache,
        "G.E.M.",
        {
            "schemaVersion": 2,
            "mediaType": "artist",
            "artist": "G.E.M.",
            "browse_id": browse_id,
            "url": None,
            "thumbnails": [],
            "failureReason": "artist_page_failed",
            "retry_after": "2099-01-01T00:00:00+00:00",
        },
        browse_id,
    )

    stats = fake_service(fake).enrich_artist_image_cache({"history": [_history_artist("G.E.M.")]}, cache)

    assert stats["attempted"] == 1
    assert stats["added"] == 1
    assert fake.get_artist_calls == [browse_id]
    assert cache_record(cache, "G.E.M.")["url"] == "https://img.example/gem-recovered.jpg"


def test_artist_image_enrichment_uses_verified_lane_8_channel() -> None:
    fake = FakeYTMusic(
        artist_pages={
            "UCqjupXgFQVmnpYo-sJ47dHg": {
                "artist": "Lane 8",
                "browseId": "UCqjupXgFQVmnpYo-sJ47dHg",
                "thumbnails": [{"url": "https://img.example/lane-8.jpg", "width": 512, "height": 512}],
            }
        }
    )
    cache: dict[str, object] = {}

    stats = fake_service(fake).enrich_artist_image_cache({"history": [_history_artist("Lane 8")]}, cache)

    assert stats["added"] == 1
    assert fake.search_calls == []
    assert fake.get_artist_calls == ["UCqjupXgFQVmnpYo-sJ47dHg"]
    assert cache_record(cache, "Lane 8")["url"] == "https://img.example/lane-8.jpg"


def test_artist_image_enrichment_uses_verified_jay_chou_channel() -> None:
    fake = FakeYTMusic(
        artist_pages={
            "UCL2MDNdwEtV6aYUgNjFQGZA": {
                "artist": "周杰倫 - Jay Chou",
                "browseId": "UCL2MDNdwEtV6aYUgNjFQGZA",
                "thumbnails": [{"url": "https://img.example/jay-chou.jpg", "width": 512, "height": 512}],
            }
        }
    )
    cache: dict[str, object] = {}

    stats = fake_service(fake).enrich_artist_image_cache({"history": [_history_artist("周杰倫")]}, cache)

    assert stats["added"] == 1
    assert fake.search_calls == []
    assert fake.get_artist_calls == ["UCL2MDNdwEtV6aYUgNjFQGZA"]
    assert cache_record(cache, "周杰倫")["url"] == "https://img.example/jay-chou.jpg"


def test_artist_image_enrichment_keeps_list_on_upstream_exception() -> None:
    fake = FakeYTMusic(raise_search=True)
    cache: dict[str, object] = {}
    stats = fake_service(fake).enrich_artist_image_cache({"history": [_history_artist("Artist A")]}, cache)
    assert stats["failed"] == 1
    assert cache_record(cache, "Artist A")["failureReason"] == "upstream_exception"


def test_artist_name_matching_handles_unicode_and_topic_suffix() -> None:
    assert normalise_artist_name("Beyonc\u00e9 - Topic") == normalise_artist_name("Beyonce")
    fake = FakeYTMusic(
        search_results={"Beyonc\u00e9 - Topic": [{"artist": "Beyonce", "browseId": "UC-b"}]},
        artist_pages={"UC-b": {"artist": "Beyonce", "browseId": "UC-b", "thumbnails": [{"url": "https://img.example/beyonce.jpg", "width": 400, "height": 400}]}},
    )
    cache: dict[str, object] = {}
    stats = fake_service(fake).enrich_artist_image_cache({"history": [_history_artist("Beyonc\u00e9 - Topic")]}, cache)
    assert stats["added"] == 1
    assert cache_record(cache, "Beyonc\u00e9 - Topic", "UC-b")["url"] == "https://img.example/beyonce.jpg"


class FakeYTMusic:
    def __init__(
        self,
        search_results: dict[str, list[dict[str, object]]] | None = None,
        artist_pages: dict[str, dict[str, object]] | None = None,
        album_pages: dict[str, dict[str, object]] | None = None,
        song_pages: dict[str, dict[str, object]] | None = None,
        raise_search: bool = False,
    ) -> None:
        self.search_results = search_results or {}
        self.artist_pages = artist_pages or {}
        self.album_pages = album_pages or {}
        self.song_pages = song_pages or {}
        self.raise_search = raise_search
        self.search_calls: list[tuple[str, str | None, int | None]] = []
        self.get_artist_calls: list[str] = []
        self.get_album_calls: list[str] = []
        self.get_song_calls: list[str] = []

    def search(self, query: str, filter: str | None = None, limit: int | None = None) -> list[dict[str, object]]:
        self.search_calls.append((query, filter, limit))
        if self.raise_search:
            raise RuntimeError("search failed")
        return self.search_results.get(query, [])

    def get_artist(self, browse_id: str) -> dict[str, object]:
        self.get_artist_calls.append(browse_id)
        payload = self.artist_pages.get(browse_id)
        if payload is None:
            raise RuntimeError("artist page failed")
        return payload

    def get_album(self, browse_id: str) -> dict[str, object]:
        self.get_album_calls.append(browse_id)
        payload = self.album_pages.get(browse_id)
        if payload is None:
            raise RuntimeError("album page failed")
        return payload

    def get_song(self, video_id: str) -> dict[str, object]:
        self.get_song_calls.append(video_id)
        payload = self.song_pages.get(video_id)
        if payload is None:
            raise RuntimeError("song page failed")
        return payload


def fake_service(fake: FakeYTMusic) -> YTMusicService:
    service = YTMusicService(Settings())
    service.client = lambda prefer_browser=True: fake  # type: ignore[method-assign]
    service.public_client = lambda: fake  # type: ignore[method-assign]
    return service


def _history_artist(name: str, artist_id: str | None = None) -> dict[str, object]:
    artist: dict[str, object] = {"name": name}
    if artist_id:
        artist["id"] = artist_id
    return {"videoId": f"v-{normalise_artist_name(name)}", "title": "Song", "artists": [artist]}


def _history_album(album: str, artist: str, album_id: str | None = None) -> dict[str, object]:
    item: dict[str, object] = {
        "videoId": f"v-{normalise_artist_name(artist)}-{normalise_artist_name(album)}",
        "title": "Song",
        "artists": [{"name": artist}],
        "album": {"name": album},
    }
    if album_id:
        item["album"] = {"name": album, "id": album_id}
    return item


def cache_record(cache: dict[str, object], artist: str, artist_id: str | None = None) -> dict[str, object]:
    items = cache["items"]  # type: ignore[index]
    assert isinstance(items, dict)
    for key in (artist_id_key(artist_id), artist_name_key(artist)):
        if key and key in items:
            value = items[key]
            assert isinstance(value, dict)
            return value
    raise AssertionError(f"Missing cache record for {artist}")


def album_cache_record(cache: dict[str, object], album: str, artist: str, album_id: str | None = None) -> dict[str, object]:
    items = cache["items"]  # type: ignore[index]
    assert isinstance(items, dict)
    direct_key = album_id_key(album_id)
    if direct_key and direct_key in items:
        value = items[direct_key]
        assert isinstance(value, dict)
        return value
    index = cache.get("index")  # type: ignore[attr-defined]
    assert isinstance(index, dict)
    alias_key = album_name_artist_key(album, artist)
    mapped = index.get(alias_key) if alias_key else None
    if mapped and mapped in items:
        value = items[mapped]
        assert isinstance(value, dict)
        return value
    raise AssertionError(f"Missing album cache record for {album} by {artist}")
