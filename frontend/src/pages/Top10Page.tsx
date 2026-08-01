import { ArrowDown, ArrowUp, Minus, Sparkles, X } from "lucide-react";
import type { ReactNode } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import { AlbumCover, ArtistAvatar, TrackArtwork } from "../components/Artwork";
import { GlowPanel } from "../components/GlowPanel";
import { PageTitlePanel } from "../components/PageTitlePanel";
import GradualBlur from "../components/reactbits/GradualBlur/GradualBlur";
import type {
  MusicSource,
  PeriodTopItem,
  PeriodTopResponse,
  TopAlbumItem,
  TopAlbumSongsResponse,
  TopAlbumsResponse,
  TopArtistSongsResponse,
  TopDrilldownSong,
  PeriodSpec,
} from "../types/api";
import { formatDate } from "../utils/format";
import "./Top10Page.css";

type TopPeriod = "this_month" | "month" | "rolling_year" | "all";
type RankingKind = "songs" | "artists";

export function Top10Page({ source, titleAnimationKey }: { source: MusicSource; titleAnimationKey: string }) {
  const [period, setPeriod] = useState<TopPeriod>("this_month");
  const [selectedMonth, setSelectedMonth] = useState<string | null>(null);
  const [tracks, setTracks] = useState<PeriodTopResponse | null>(null);
  const [artists, setArtists] = useState<PeriodTopResponse | null>(null);
  const [albums, setAlbums] = useState<TopAlbumsResponse | null>(null);
  const [selectedArtist, setSelectedArtist] = useState<string | null>(null);
  const [artistSongs, setArtistSongs] = useState<TopArtistSongsResponse | null>(null);
  const [selectedAlbum, setSelectedAlbum] = useState<TopAlbumItem | null>(null);
  const [albumSongs, setAlbumSongs] = useState<TopAlbumSongsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [artistLoading, setArtistLoading] = useState(false);
  const [albumLoading, setAlbumLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [availableMonths, setAvailableMonths] = useState<PeriodSpec["available_months"]>([]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    // Never leave rankings from the previous period on screen while a new
    // request is loading or has failed. That made a selected July filter look
    // like August with zero plays after a transient hosted 502.
    setTracks(null);
    setArtists(null);
    setAlbums(null);
    Promise.all([
      api.periodTop(period, "tracks", period === "month" ? selectedMonth : null, source),
      api.periodTop(period, "artists", period === "month" ? selectedMonth : null, source),
      api.topAlbums(period, period === "month" ? selectedMonth : null, source),
    ])
      .then(([nextTracks, nextArtists, nextAlbums]) => {
        if (cancelled) return;
        setTracks(nextTracks);
        setArtists(nextArtists);
        setAlbums(nextAlbums);
        const availableMonths = nextTracks.period.available_months.length ? nextTracks.period.available_months : nextAlbums.period.available_months;
        setAvailableMonths(availableMonths);
        if (period === "month" && !selectedMonth && availableMonths.length) {
          setSelectedMonth(availableMonths[availableMonths.length - 1].value);
        }
      })
      .catch((nextError: Error) => {
        if (!cancelled) setError(nextError.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [period, selectedMonth, source]);

  useEffect(() => {
    if (!selectedArtist) {
      setArtistSongs(null);
      return;
    }
    let cancelled = false;
    setArtistLoading(true);
    api.artistSongs(selectedArtist, period, period === "month" ? selectedMonth : null, source)
      .then((next) => {
        if (!cancelled) setArtistSongs(next);
      })
      .catch(() => {
        if (!cancelled) setArtistSongs(null);
      })
      .finally(() => {
        if (!cancelled) setArtistLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedArtist, period, selectedMonth, source]);

  useEffect(() => {
    if (!selectedAlbum) {
      setAlbumSongs(null);
      return;
    }
    let cancelled = false;
    setAlbumLoading(true);
    api.albumSongs(selectedAlbum.album, selectedAlbum.artist, period, period === "month" ? selectedMonth : null, source)
      .then((next) => {
        if (!cancelled) setAlbumSongs(next);
      })
      .catch(() => {
        if (!cancelled) setAlbumSongs(null);
      })
      .finally(() => {
        if (!cancelled) setAlbumLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedAlbum, period, selectedMonth, source]);

  const responseMonths = tracks?.period.available_months ?? artists?.period.available_months ?? albums?.period.available_months ?? [];
  const months = responseMonths.length ? responseMonths : availableMonths;
  const selectedMonthLabel = months.find((month) => month.value === selectedMonth)?.label ?? formatMonthValue(selectedMonth);
  const activeLabel = period === "month"
    ? selectedMonthLabel
    : displayPeriodLabel(tracks?.period.label ?? albums?.period.label, period);

  return (
    <div className="space-y-8">
      <PageTitlePanel
        title="Top 10"
        titleAnimationKey={titleAnimationKey}
        titleClassName="text-4xl font-black leading-none text-white md:text-5xl"
        className="top10-title-panel"
        actions={
          <div className="flex flex-wrap items-center gap-2 rounded-xl border border-white/10 bg-black/25 p-2">
          <PeriodButton active={period === "this_month"} label="This Month" onClick={() => setPeriod("this_month")} />
          <PeriodButton active={period === "month"} label="Select Month" onClick={() => setPeriod("month")} />
          <PeriodButton active={period === "rolling_year"} label="Rolling Year" onClick={() => setPeriod("rolling_year")} />
          <PeriodButton active={period === "all"} label="All History" onClick={() => setPeriod("all")} />
          {period === "month" ? (
            <select
              className="rounded-md border border-white/10 bg-ink px-3 py-2 text-sm text-white"
              value={selectedMonth ?? months.at(-1)?.value ?? ""}
              onChange={(event) => setSelectedMonth(event.target.value)}
              aria-label="Select Top 10 month"
            >
              {months.map((month) => (
                <option key={month.value} value={month.value}>{month.label}</option>
              ))}
            </select>
          ) : null}
          </div>
        }
        metadata={
          <span>
            {activeLabel} &middot; {error
              ? "Rankings temporarily unavailable"
              : `${(tracks?.total_play_count ?? 0).toLocaleString()} ${source === "spotify" ? "ranking signals" : "plays"}`}
            {loading ? " · Updating" : ""}
          </span>
        }
      />

      {(tracks?.sample_warning || albums?.sample_warning || error) ? (
        <section className="space-y-3">
          {tracks?.sample_warning ? <GlowPanel as="p" variant="row" className="bg-amber-200/10 p-4 text-sm text-amber-100">{tracks.sample_warning}</GlowPanel> : null}
          {albums?.sample_warning ? <GlowPanel as="p" variant="row" className="bg-amber-200/10 p-4 text-sm text-amber-100">{albums.sample_warning}</GlowPanel> : null}
          {error ? <GlowPanel as="p" variant="row" className="bg-red-400/10 p-4 text-sm text-red-100">{error}</GlowPanel> : null}
        </section>
      ) : null}

      <RankingStorySection
        kind="songs"
        title={`Top Songs - ${activeLabel}`}
        response={tracks}
        loading={loading}
        unavailable={Boolean(error)}
        source={source}
      />

      <RankingStorySection
        kind="artists"
        title={`Top Artists - ${activeLabel}`}
        response={artists}
        loading={loading}
        unavailable={Boolean(error)}
        source={source}
        selectedArtist={selectedArtist}
        onViewSongs={setSelectedArtist}
      />

      {selectedArtist ? (
        <ArtistDrilldownPanel artist={selectedArtist} response={artistSongs} loading={artistLoading} onClose={() => setSelectedArtist(null)} />
      ) : null}

      <FavouriteAlbumsSection response={albums} loading={loading} selectedAlbum={selectedAlbum} onViewSongs={setSelectedAlbum} source={source} />

      {selectedAlbum ? (
        <AlbumDrilldownPanel album={selectedAlbum} response={albumSongs} loading={albumLoading} onClose={() => setSelectedAlbum(null)} />
      ) : null}
    </div>
  );
}

function PeriodButton({ active, label, onClick }: { active: boolean; label: string; onClick: () => void }) {
  return (
    <button className={`rounded-lg px-3 py-2 text-sm font-semibold transition ${active ? "bg-red-600 text-white" : "text-mist hover:bg-white/10 hover:text-white"}`} onClick={onClick}>
      {label}
    </button>
  );
}

function RankingStorySection({
  kind,
  title,
  response,
  loading,
  unavailable = false,
  selectedArtist,
  onViewSongs,
  source,
}: {
  kind: RankingKind;
  title: string;
  response: PeriodTopResponse | null;
  loading: boolean;
  unavailable?: boolean;
  selectedArtist?: string | null;
  onViewSongs?: (artist: string) => void;
  source: MusicSource;
}) {
  const items = (response?.items ?? []).slice(0, 10);
  const { activeId, registerItem } = useActiveRanking(items, kind);
  const activeItem = useMemo(() => items.find((item) => rankingItemId(item, kind) === activeId) ?? items[0] ?? null, [activeId, items, kind]);
  const label = kind === "artists" ? "Top Artists" : "Top Songs";

  if (!items.length) {
    return (
      <GlowPanel as="section" variant="major" lined className="p-5 lg:p-6">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <p className="text-sm font-semibold uppercase tracking-[0.18em] text-red-200">{label}</p>
            <h2 className="mt-2 text-3xl font-black text-white">{title}</h2>
          </div>
          {loading ? <span className="text-sm text-mist">Loading...</span> : null}
        </div>
        <GlowPanel as="div" variant="row" wrapperClassName="mt-5" className="p-5 text-sm text-mist">
          {loading
            ? "Loading rankings..."
            : unavailable
              ? "Rankings are temporarily unavailable. Your saved listening data has not been replaced."
              : "No detected plays in this period."}
        </GlowPanel>
      </GlowPanel>
    );
  }

  return (
    <section className={`ranking-story ranking-story--${kind}`} aria-labelledby={`ranking-story-${kind}`}>
      <div className="ranking-story__visual">
        <StickyRankingVisual kind={kind} activeItem={activeItem} itemCount={items.length} source={source} />
      </div>
      <div className="ranking-story__items">
        <div className="ranking-story__chapter">
          <p className="text-sm font-semibold uppercase tracking-[0.2em] text-red-200">{label}</p>
          <h2 id={`ranking-story-${kind}`} className="mt-3 text-3xl font-black leading-tight text-white md:text-4xl">{title}</h2>
          {loading ? <span className="mt-4 inline-flex rounded-full bg-white/10 px-3 py-1 text-xs text-mist">Loading...</span> : null}
        </div>
        {items.map((item) => {
          const itemId = rankingItemId(item, kind);
          const active = itemId === activeId;
          return (
            <RankingStoryCard
              key={itemId}
              item={item}
              kind={kind}
              active={active}
              selected={kind === "artists" && selectedArtist === item.artist}
              onViewSongs={onViewSongs}
              source={source}
              register={(node) => registerItem(item, node)}
            />
          );
        })}
      </div>
    </section>
  );
}

function StickyRankingVisual({ kind, activeItem, itemCount, source }: { kind: RankingKind; activeItem: PeriodTopItem | null; itemCount: number; source: MusicSource }) {
  const activeId = activeItem ? rankingItemId(activeItem, kind) : "empty";
  const activeTitle = activeItem ? rankingItemTitle(activeItem, kind) : "No ranking yet";
  const activeSubtitle = activeItem ? rankingItemSubtitle(activeItem, kind) : "";
  const label = kind === "artists" ? "Artist chapter" : "Song chapter";
  const fallback = activeItem ? (kind === "artists" ? initials(activeItem.artist) : `#${activeItem.rank}`) : "?";

  return (
    <GlowPanel as="div" variant="major" wrapperClassName="ranking-story__visual-shell" className="ranking-story__visual-panel">
      <div className="ranking-story__visual-bg" aria-hidden="true" />
      <div className="ranking-story__visual-content" key={activeId}>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-red-200">{label}</p>
          <span className="text-xs font-semibold text-mist/75">
            {activeItem ? `${String(activeItem.rank).padStart(2, "0")} / ${String(itemCount).padStart(2, "0")}` : `00 / ${String(itemCount).padStart(2, "0")}`}
          </span>
        </div>
        <div className="ranking-story__hero-art">
          {kind === "artists" ? (
            <ArtistAvatar artistImageUrl={activeItem?.artist_image_url} artistName={activeItem?.artist ?? "Artist"} size="hero" priority fallbackLabel={fallback} shape="circle" className="ranking-story__hero-artwork" />
          ) : (
            <TrackArtwork trackImageUrl={activeItem?.track_image_url} albumArtUrl={activeItem?.album_art_url} title={activeTitle} size="hero" priority fallbackLabel={fallback} className="ranking-story__hero-artwork" />
          )}
        </div>
        <div className="ranking-story__hero-copy">
          <span className="text-6xl font-black leading-none text-white/10">#{activeItem?.rank ?? "--"}</span>
          <h3 className="mt-3 text-3xl font-black leading-tight text-white">{activeTitle}</h3>
          <p className="mt-2 text-base font-semibold leading-6 text-red-100">{activeSubtitle}</p>
          {activeItem ? (
            <div className="mt-5 text-xs font-medium leading-6 text-mist/75">
              {spotifyEvidenceLabel(activeItem, source, kind === "artists")}
              {detectedMinutesLabel(activeItem) ? ` · ${detectedMinutesLabel(activeItem)}` : ""}
            </div>
          ) : null}
        </div>
      </div>
      <GradualBlur
        target="parent"
        position="bottom"
        height="5rem"
        strength={1.4}
        divCount={6}
        curve="bezier"
        exponential={false}
        opacity={0.72}
        animated="scroll"
        duration="0.35s"
        zIndex={4}
        className="ranking-story__blur"
      />
    </GlowPanel>
  );
}

function RankingStoryCard({
  item,
  kind,
  active,
  selected,
  onViewSongs,
  source,
  register,
}: {
  item: PeriodTopItem;
  kind: RankingKind;
  active: boolean;
  selected?: boolean;
  onViewSongs?: (artist: string) => void;
  source: MusicSource;
  register: (node: HTMLDivElement | null) => void;
}) {
  const isArtist = kind === "artists";
  const title = rankingItemTitle(item, kind);
  const rank = `#${String(item.rank).padStart(2, "0")}`;
  const itemId = rankingItemId(item, kind);

  return (
    <div ref={register} className="ranking-story__item" data-active={active ? "true" : "false"} data-ranking-id={itemId}>
      <GlowPanel as="article" variant="row" selected={active || selected} className="ranking-story__card" data-testid={isArtist ? "top-artist-card" : "top-song-card"}>
        <div className="ranking-story__card-grid">
          {isArtist ? (
            <ArtistAvatar artistImageUrl={item.artist_image_url} artistName={item.artist} size="md" fallbackLabel={initials(item.artist)} className="ranking-story__row-artwork" />
          ) : (
            <TrackArtwork trackImageUrl={item.track_image_url} albumArtUrl={item.album_art_url} title={title} size="md" fallbackLabel={rank} className="ranking-story__row-artwork" />
          )}
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-3">
              <span className="text-xl font-black text-red-200">{rank}</span>
            </div>
            <h3 className="mt-2 break-words text-xl font-black leading-tight text-white md:text-2xl">{title}</h3>
            {isArtist ? (
              <p className="mt-2 text-sm leading-6 text-mist">
                {item.unique_songs ?? 0} unique songs{item.most_played_song ? ` - top song: ${item.most_played_song}` : ""}
              </p>
            ) : (
              <>
                <p className="mt-2 text-base font-semibold leading-6 text-mist">{item.artist}</p>
                {item.album ? <p className="mt-1 text-sm leading-5 text-mist/75">{item.album}</p> : null}
              </>
            )}
            <div className="mt-4 flex flex-wrap items-center gap-3">
              <span className="text-lg font-black text-white">{spotifyEvidenceLabel(item, source, isArtist)}</span>
              {detectedMinutesLabel(item) ? <span className="text-xs text-mist/75">{detectedMinutesLabel(item)}</span> : null}
              <Movement movement={item.movement} />
            </div>
            {isArtist && onViewSongs ? (
              <button
                aria-label={`View songs by ${item.artist}`}
                className="mt-4 rounded-md border border-white/10 px-3 py-2 text-sm font-semibold text-white transition hover:border-red-400/50 hover:bg-red-500/15"
                type="button"
                onClick={() => onViewSongs(item.artist)}
              >
                View songs
              </button>
            ) : null}
          </div>
        </div>
      </GlowPanel>
    </div>
  );
}

function useActiveRanking(items: PeriodTopItem[], kind: RankingKind) {
  const firstId = items[0] ? rankingItemId(items[0], kind) : "";
  const [activeId, setActiveId] = useState(firstId);
  const elementsRef = useRef(new Map<string, HTMLDivElement>());
  const visibilityRef = useRef(new Map<string, { ratio: number; order: number }>());

  useEffect(() => {
    const ids = new Set(items.map((item) => rankingItemId(item, kind)));
    setActiveId((current) => (current && ids.has(current) ? current : firstId));
    visibilityRef.current.clear();
  }, [firstId, items, kind]);

  const registerItem = useCallback(
    (item: PeriodTopItem, node: HTMLDivElement | null) => {
      const id = rankingItemId(item, kind);
      if (node) {
        elementsRef.current.set(id, node);
      } else {
        elementsRef.current.delete(id);
        visibilityRef.current.delete(id);
      }
    },
    [kind],
  );

  useEffect(() => {
    if (typeof window === "undefined" || typeof IntersectionObserver === "undefined" || !items.length) return;
    const order = new Map(items.map((item, index) => [rankingItemId(item, kind), index]));
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          const id = entry.target.getAttribute("data-ranking-id");
          if (!id) continue;
          if (entry.isIntersecting) {
            visibilityRef.current.set(id, { ratio: entry.intersectionRatio, order: order.get(id) ?? 0 });
          } else {
            visibilityRef.current.delete(id);
          }
        }
        const candidates = [...visibilityRef.current.entries()].filter(([id]) => order.has(id));
        if (!candidates.length) return;
        candidates.sort(([, a], [, b]) => b.ratio - a.ratio || a.order - b.order);
        setActiveId(candidates[0][0]);
      },
      { rootMargin: "-35% 0px -45% 0px", threshold: [0, 0.15, 0.35, 0.6, 0.9] },
    );

    for (const item of items) {
      const node = elementsRef.current.get(rankingItemId(item, kind));
      if (node) observer.observe(node);
    }

    return () => observer.disconnect();
  }, [items, kind]);

  return { activeId: activeId || firstId, registerItem };
}

function rankingItemId(item: PeriodTopItem, kind: RankingKind) {
  if (kind === "artists") return item.source_artist_id || item.key || item.artist;
  return item.source_track_id || item.track_id || item.video_id || item.key || `${item.title ?? "unknown"}::${item.artist}`;
}

function rankingItemTitle(item: PeriodTopItem, kind: RankingKind) {
  return kind === "artists" ? item.artist : item.title ?? "Unknown track";
}

function rankingItemSubtitle(item: PeriodTopItem, kind: RankingKind) {
  if (kind === "artists") {
    return `${item.unique_songs ?? 0} unique songs${item.most_played_song ? ` - top song: ${item.most_played_song}` : ""}`;
  }
  return item.album ? `${item.artist} - ${item.album}` : item.artist;
}

function FavouriteAlbumsSection({ response, loading, selectedAlbum, onViewSongs, source }: { response: TopAlbumsResponse | null; loading: boolean; selectedAlbum: TopAlbumItem | null; onViewSongs: (album: TopAlbumItem) => void; source: MusicSource }) {
  return (
    <GlowPanel as="section" variant="major" className="p-4 lg:p-5">
      <div className="mb-5 flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h2 className="text-2xl font-black leading-tight text-white md:text-3xl">Favourite Albums</h2>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-mist">{source === "spotify" ? "Projects with the strongest album-level signal from Spotify top tracks, saved music, playlists, and recent sync data." : "Projects with the strongest pull across your local plays."}</p>
        </div>
        {loading ? <span className="text-sm text-mist">Loading...</span> : null}
      </div>
      {response?.albums.length ? (
        <div className="grid gap-3 xl:grid-cols-2">
          {response.albums.map((album) => (
            <AlbumCard key={album.key} album={album} selected={selectedAlbum?.key === album.key} onViewSongs={onViewSongs} source={source} />
          ))}
        </div>
      ) : (
        <GlowPanel as="div" variant="row" className="p-5 text-sm text-mist">Album data is unavailable for this period.</GlowPanel>
      )}
    </GlowPanel>
  );
}

function AlbumCard({ album, selected, onViewSongs, source }: { album: TopAlbumItem; selected: boolean; onViewSongs: (album: TopAlbumItem) => void; source: MusicSource }) {
  const rank = `#${String(album.rank).padStart(2, "0")}`;
  return (
    <GlowPanel as="article" variant="row" selected={selected} className="p-4 transition" data-testid="top-album-card">
      <div className="grid gap-4 sm:grid-cols-[5rem_1fr] lg:grid-cols-[5rem_1fr_auto]">
        <AlbumCover albumImageUrl={album.album_image_url ?? album.thumbnail} fallbackImageUrl={album.track_image_url} albumTitle={album.album} size="md" fallbackLabel={rank} fit="cover" />
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-3">
            <span className="text-xl font-black text-red-200">{rank}</span>
          </div>
          <h3 className="mt-2 text-xl font-black leading-tight text-white md:text-2xl">{album.album}</h3>
          <p className="mt-2 text-base font-semibold leading-6 text-mist">{album.artist}</p>
          <div className="mt-5 flex flex-wrap items-center gap-3">
            <span className="text-lg font-black text-white">{source === "spotify" ? `${album.plays.toLocaleString()} signals` : `${album.plays.toLocaleString()} plays`}</span>
            <span className="text-xs text-mist/75">{album.unique_songs} songs</span>
          </div>
        </div>
        <div className="flex items-start justify-end">
          <button
            aria-label={`View songs from ${album.album}`}
            className="whitespace-nowrap rounded-md border border-white/10 px-3 py-2 text-sm font-semibold text-white transition hover:border-red-400/50 hover:bg-red-500/15"
            type="button"
            onClick={() => onViewSongs(album)}
          >
            View Songs
          </button>
        </div>
      </div>
    </GlowPanel>
  );
}

function ArtistDrilldownPanel({ artist, response, loading, onClose }: { artist: string; response: TopArtistSongsResponse | null; loading: boolean; onClose: () => void }) {
  return (
    <DrilldownShell
      title={`Songs by ${artist} - ${response?.period_label ?? "Selected Period"}`}
      subtitle="Song-level data for this artist in the selected period."
      visual={<ArtistAvatar artistImageUrl={response?.artist_image_url} artistName={artist} size="lg" fallbackLabel={initials(artist)} />}
      loading={loading}
      onClose={onClose}
      emptyMessage="Song-level data for this artist is not available in the selected period."
      summary={response ? [
        `${response.total_plays} total plays`,
        `${response.unique_songs} unique songs`,
        response.most_replayed_song ? `Most replayed: ${response.most_replayed_song}` : null,
      ] : []}
      songs={response?.songs ?? []}
    />
  );
}

function AlbumDrilldownPanel({ album, response, loading, onClose }: { album: TopAlbumItem; response: TopAlbumSongsResponse | null; loading: boolean; onClose: () => void }) {
  return (
    <DrilldownShell
      title={`Songs from ${album.album} - ${response?.period_label ?? "Selected Period"}`}
      subtitle={album.artist}
      visual={<AlbumCover albumImageUrl={album.album_image_url ?? album.thumbnail} fallbackImageUrl={album.track_image_url} albumTitle={album.album} size="lg" fallbackLabel={initials(album.album)} fit="cover" />}
      loading={loading}
      onClose={onClose}
      emptyMessage="Album data is unavailable for this period."
      summary={response ? [
        `${response.total_plays} total plays`,
        `${response.unique_songs} unique songs`,
        response.most_played_song ? `Most played: ${response.most_played_song}` : null,
      ] : []}
      songs={response?.songs ?? []}
    />
  );
}

function DrilldownShell({
  title,
  subtitle,
  visual,
  loading,
  onClose,
  emptyMessage,
  summary,
  songs,
}: {
  title: string;
  subtitle: string;
  visual?: ReactNode;
  loading: boolean;
  onClose: () => void;
  emptyMessage: string;
  summary: (string | null)[];
  songs: TopDrilldownSong[];
}) {
  return (
    <GlowPanel as="section" variant="major" className="p-5 lg:p-6" data-testid="songs-drilldown">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center">
          {visual}
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-red-200">Drilldown</p>
            <h2 className="mt-2 text-3xl font-black text-white">{title}</h2>
            <p className="mt-1 text-sm text-mist">{subtitle}</p>
          </div>
        </div>
        <button className="inline-flex items-center gap-2 rounded-lg border border-white/10 px-3 py-2 text-sm font-semibold text-white hover:border-red-400/50 hover:bg-white/10" onClick={onClose}>
          <X size={16} /> Close
        </button>
      </div>
      {summary.length ? <MetricPills items={summary} /> : null}
      {loading ? <GlowPanel as="p" variant="row" wrapperClassName="mt-5" className="p-4 text-sm text-mist">Loading songs...</GlowPanel> : null}
      {!loading && songs.length ? (
        <div className="mt-5 space-y-2">
          {songs.map((song) => <DrilldownSongRow key={`${song.rank}-${song.track_id ?? song.title}`} song={song} />)}
        </div>
      ) : null}
      {!loading && !songs.length ? <GlowPanel as="p" variant="row" wrapperClassName="mt-5" className="p-4 text-sm text-mist">{emptyMessage}</GlowPanel> : null}
    </GlowPanel>
  );
}

function DrilldownSongRow({ song }: { song: TopDrilldownSong }) {
  return (
    <GlowPanel as="article" variant="row" className="grid gap-4 p-3 sm:grid-cols-[4.5rem_1fr]">
      <TrackArtwork trackImageUrl={song.track_image_url} albumArtUrl={song.album_art_url} title={song.title ?? "Song"} size="sm" fallbackLabel={`#${song.rank}`} />
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs font-black text-white/45">#{song.rank}</span>
          <h3 className="text-base font-semibold leading-6 text-white">{song.title ?? "Unknown track"}</h3>
        </div>
        <p className="mt-1 text-sm leading-5 text-mist">{song.artist ?? "Unknown Artist"}{song.album ? ` - ${song.album}` : ""}</p>
        <MetricPills
          items={[
            `${song.plays} plays`,
            song.last_played ? `Last ${formatDate(song.last_played)}` : null,
            song.first_played ? `First ${formatDate(song.first_played)}` : null,
          ]}
        />
      </div>
    </GlowPanel>
  );
}

function MetricPills({ items }: { items: (string | null | undefined)[] }) {
  const visible = items.filter(Boolean);
  if (!visible.length) return null;
  return (
    <div className="mt-3 text-xs leading-5 text-mist/75">
      {visible.join(" / ")}
    </div>
  );
}

function Movement({ movement }: { movement: PeriodTopItem["movement"] }) {
  if (!movement) return null;
  const Icon = movement.direction === "up" ? ArrowUp : movement.direction === "down" ? ArrowDown : movement.direction === "new" ? Sparkles : Minus;
  return (
    <span className="inline-flex items-center gap-1 text-xs text-mist/75">
      <Icon size={13} /> {movement.label}
    </span>
  );
}

function displayPeriodLabel(label: string | undefined, period: TopPeriod) {
  if (period === "rolling_year") return "Rolling Year";
  if (period === "all") return "All History";
  return label ?? "Selected Period";
}

function formatMonthValue(value: string | null) {
  if (!value || !/^\d{4}-\d{2}$/.test(value)) return "Selected Period";
  const [year, month] = value.split("-").map(Number);
  return new Intl.DateTimeFormat("en-US", { month: "long", year: "numeric", timeZone: "UTC" }).format(new Date(Date.UTC(year, month - 1, 1)));
}

function spotifyEvidenceLabel(item: PeriodTopItem, source: MusicSource, artistList: boolean) {
  if (source !== "spotify") return `${item.play_count.toLocaleString()} plays`;
  if (artistList) return item.play_count > 1 ? `${item.play_count.toLocaleString()} signals` : "Spotify top artist";
  return item.spotify_signal_label || (item.play_count > 1 ? `${item.play_count.toLocaleString()} signals` : "Spotify top track");
}

function detectedMinutesLabel(item: PeriodTopItem) {
  if (!item.detected_minutes || item.detected_minutes <= 0) return null;
  return item.detected_minutes_formatted || `${Math.round(item.detected_minutes)} min detected`;
}

function initials(value: string) {
  const parts = value.split(/\s+/).filter(Boolean);
  return (parts[0]?.[0] ?? "?") + (parts[1]?.[0] ?? "");
}
