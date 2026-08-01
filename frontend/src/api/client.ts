import type {
  AnalyticsEnvelope,
  AuthStatus,
  DurationEnrichmentStatus,
  GenreEnrichmentStatus,
  InsightsResponse,
  ListeningMinutes,
  MusicSource,
  MusicCharacterResponse,
  MusicCharacterRewrite,
  OverviewResponse,
  PersonaReport,
  ReportGenerationQueued,
  ReportGenerationStatus,
  TopAlbumSongsResponse,
  TopAlbumsResponse,
  TopArtistSongsResponse,
  PeriodTopResponse,
  Prerequisites,
  Recommendation,
  RefreshQueued,
  RefreshStatus,
  SessionStatus,
  SessionDeleteResult,
  SpotifyStatus,
  TasteDnaComparison,
  TasteDnaExplorer,
  TopArtist,
  TopTrack,
  TakeoutImportQueued,
  TakeoutImportStatus,
} from "../types/api";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api";

function paramsWithSource(source: MusicSource = "youtube", values: Record<string, string | null | undefined> = {}) {
  const params = new URLSearchParams({ source });
  for (const [key, value] of Object.entries(values)) {
    if (value) params.set(key, value);
  }
  return params;
}

function hostedSafeHttpMessage(status: number, message: string) {
  return [502, 503, 504].includes(status)
    ? "The server is temporarily busy processing saved metadata. Your listening data is safe; retry in a moment."
    : message;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const data = await response.json();
      const detail = data.detail;
      if (typeof detail === "string") message = detail;
      if (detail?.detail) message = detail.detail;
      if (detail?.error) message = `${detail.error}: ${message}`;
    } catch {
      // Keep the HTTP status message.
    }
    throw new Error(hostedSafeHttpMessage(response.status, message));
  }
  return response.json() as Promise<T>;
}

async function analyticsRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const envelope = await request<AnalyticsEnvelope<T>>(path, init);
  if (envelope.apiSchemaVersion !== 1) throw new Error("This analytics response uses an unsupported contract version. Refresh or re-import your data.");
  if (envelope.status === "stale_import") throw new Error("Your Takeout import needs to be re-imported before analytics can be used.");
  if (envelope.status === "failed") throw new Error(envelope.warnings[0]?.message ?? "Analytics could not be prepared.");
  return envelope.data;
}

function requireOverviewSchema(value: OverviewResponse) {
  if (value.schemaVersion !== 3 || !value.identity || !value.musicalAge || !value.topFive) {
    throw new Error("Saved Overview data uses an older schema. Refresh your music data to rebuild it.");
  }
  return value;
}

export const api = {
  health: () => request<{ ok: boolean }>("/health"),
  sessionStatus: () => request<SessionStatus>("/session"),
  deleteSession: () => request<SessionDeleteResult>("/session", { method: "DELETE" }),
  prerequisites: () => request<Prerequisites>("/prerequisites"),
  authStatus: (live = false) => request<AuthStatus>(`/auth/status${live ? "?live=true" : ""}`),
  authSetup: () => request<Record<string, unknown>>("/auth/setup", { method: "POST", body: "{}" }),
  spotifyStatus: () => request<SpotifyStatus>("/spotify/status"),
  spotifyLoginUrl: () => `${API_BASE}/spotify/login`,
  spotifyRefresh: () =>
    request<{ refreshed_at: string; warnings: string[]; coverage: unknown; track_count: number; play_count: number; profile: Record<string, unknown> }>("/spotify/refresh", {
      method: "POST",
      body: "{}",
    }),
  spotifyDisconnect: () => request<{ connected: boolean; message: string }>("/spotify/disconnect", { method: "POST", body: "{}" }),
  refresh: (useDemo: boolean, signal?: AbortSignal) =>
    request<RefreshQueued>("/data/refresh", {
      method: "POST",
      body: JSON.stringify({ use_demo: useDemo }),
      signal,
    }),
  refreshStatus: (jobId: string, signal?: AbortSignal) =>
    request<RefreshStatus>(`/data/refresh/${encodeURIComponent(jobId)}`, { signal }),
  importTakeout: async (file: File, signal?: AbortSignal) => {
    const form = new FormData();
    form.append("file", file);
    const response = await fetch(`${API_BASE}/data/import-takeout`, { method: "POST", body: form, signal, credentials: "include" });
    if (!response.ok) {
      let message = `${response.status} ${response.statusText}`;
      try {
        const data = await response.json();
        message = data.detail?.detail || data.detail?.error || message;
      } catch {
        // Keep HTTP status message.
      }
      throw new Error(hostedSafeHttpMessage(response.status, message));
    }
    return response.json() as Promise<TakeoutImportQueued>;
  },
  takeoutImportStatus: (jobId: string, signal?: AbortSignal) =>
    request<TakeoutImportStatus>(`/data/import-takeout/${encodeURIComponent(jobId)}`, { signal }),
  importSpotifyHistory: async (file: File, signal?: AbortSignal) => {
    const form = new FormData();
    form.append("file", file);
    const response = await fetch(`${API_BASE}/data/import-spotify-history`, { method: "POST", body: form, signal, credentials: "include" });
    if (!response.ok) {
      let message = `${response.status} ${response.statusText}`;
      try {
        const data = await response.json();
        message = data.detail?.detail || data.detail?.error || message;
      } catch {
        // Keep HTTP status message.
      }
      throw new Error(hostedSafeHttpMessage(response.status, message));
    }
    return response.json() as Promise<TakeoutImportQueued>;
  },
  spotifyHistoryImportStatus: (jobId: string, signal?: AbortSignal) =>
    request<TakeoutImportStatus>(`/data/import-spotify-history/${encodeURIComponent(jobId)}`, { signal }),
  startDurationEnrichment: () => request<DurationEnrichmentStatus>("/data/duration-enrichment", { method: "POST", body: "{}" }),
  durationEnrichmentStatus: (signal?: AbortSignal) => request<DurationEnrichmentStatus>("/data/duration-enrichment", { signal }),
  startGenreEnrichment: (source: MusicSource = "youtube") => request<GenreEnrichmentStatus>(`/data/genre-enrichment?${paramsWithSource(source).toString()}`, { method: "POST", body: "{}" }),
  genreEnrichmentStatus: (signal?: AbortSignal) => request<GenreEnrichmentStatus>("/data/genre-enrichment", { signal }),
  overview: (period = "this_month", month?: string | null, source: MusicSource = "youtube") => {
    const params = paramsWithSource(source, { period, month });
    return analyticsRequest<OverviewResponse>(`/v1/analysis/overview?${params.toString()}`).then(requireOverviewSchema);
  },
  topTracks: (source: MusicSource = "youtube") => request<TopTrack[]>(`/analysis/top-tracks?${paramsWithSource(source).toString()}`),
  topArtists: (source: MusicSource = "youtube") => request<TopArtist[]>(`/analysis/top-artists?${paramsWithSource(source).toString()}`),
  listeningMinutes: (period = "rolling_year", month?: string | null, source: MusicSource = "youtube") => {
    const params = paramsWithSource(source, { period, month });
    return request<ListeningMinutes>(`/analytics/listening-minutes?${params.toString()}`);
  },
  insights: (period = "rolling_year", month?: string | null, source: MusicSource = "youtube") => {
    const params = paramsWithSource(source, { period, month });
    return analyticsRequest<InsightsResponse>(`/v1/insights?${params.toString()}`).then((value) => {
      if (value.schemaVersion !== 1) throw new Error("Insights data uses an unsupported schema. Refresh your music data and try again.");
      return value;
    });
  },
  periodTop: (period = "this_month", type: "tracks" | "artists" = "tracks", month?: string | null, source: MusicSource = "youtube") => {
    const params = paramsWithSource(source, { period, type, month });
    return analyticsRequest<PeriodTopResponse>(`/v1/top?${params.toString()}`);
  },
  topAlbums: (period = "this_month", month?: string | null, source: MusicSource = "youtube", limit?: number) => {
    const params = paramsWithSource(source, { period, month, limit: limit ? String(limit) : undefined });
    return request<TopAlbumsResponse>(`/top/albums?${params.toString()}`);
  },
  artistSongs: (artist: string, period = "this_month", month?: string | null, source: MusicSource = "youtube") => {
    const params = paramsWithSource(source, { artist, period, month });
    return request<TopArtistSongsResponse>(`/top/artist-songs?${params.toString()}`);
  },
  albumSongs: (album: string, artist: string | null | undefined, period = "this_month", month?: string | null, source: MusicSource = "youtube") => {
    const params = paramsWithSource(source, { album, artist, period, month });
    return request<TopAlbumSongsResponse>(`/top/album-songs?${params.toString()}`);
  },
  tasteDna: (period = "rolling_year", month?: string | null, source: MusicSource = "youtube") => {
    const params = paramsWithSource(source, { period, month });
    return request<TasteDnaExplorer>(`/taste-dna?${params.toString()}`);
  },
  tasteDnaCompare: (base = "rolling_year", compare = "this_month", month?: string | null, source: MusicSource = "youtube") => {
    const params = paramsWithSource(source, { base, compare, month });
    return request<TasteDnaComparison>(`/taste-dna/compare?${params.toString()}`);
  },
  musicCharacter: (period = "rolling_year", month?: string | null, source: MusicSource = "youtube") => {
    const params = paramsWithSource(source, { period, month });
    return request<MusicCharacterResponse>(`/persona/character?${params.toString()}`);
  },
  rewriteMusicCharacter: (period = "rolling_year", month?: string | null, mode = "playful", source: MusicSource = "youtube") =>
    request<MusicCharacterRewrite>("/persona/character/rewrite", {
      method: "POST",
      body: JSON.stringify({ period, month, mode, source }),
    }),
  latestReport: (source: MusicSource = "youtube", period: "rolling_year" | "this_month" = "rolling_year") =>
    request<PersonaReport>(`/report/latest?${paramsWithSource(source, { period }).toString()}`),
  generateReport: (mode: "serious" | "playful" | "roast", source: MusicSource = "youtube", period: "rolling_year" | "this_month" = "rolling_year") =>
    request<PersonaReport>("/report/generate", { method: "POST", body: JSON.stringify({ mode, source, period }) }),
  startReportGeneration: (mode: "serious" | "playful" | "roast", source: MusicSource = "youtube", period: "rolling_year" | "this_month" = "rolling_year") =>
    request<ReportGenerationQueued>("/report/jobs", { method: "POST", body: JSON.stringify({ mode, source, period }) }),
  reportGenerationStatus: (jobId: string, signal?: AbortSignal) =>
    request<ReportGenerationStatus>(`/report/jobs/${encodeURIComponent(jobId)}`, { signal }),
  recommendations: () => analyticsRequest<{ items: Recommendation[] }>("/v1/recommendations").then((data) => data.items),
  generateRecommendations: () => request<Recommendation[]>("/recommendations/generate", { method: "POST", body: "{}" }),
  createPlaylist: (title: string) =>
    request<{ playlist_id: string; title: string; added_count: number; message: string }>("/recommendations/create-playlist", {
      method: "POST",
      body: JSON.stringify({ confirm: true, title }),
    }),
};
