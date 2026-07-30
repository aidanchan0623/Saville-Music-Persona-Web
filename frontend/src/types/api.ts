export type MusicSource = "youtube" | "spotify";

export interface SessionStatus {
  mode: "local" | "anonymous";
  anonymous: boolean;
  sessionHint: string | null;
  expiresAt: string | null;
  accountConnectionsEnabled: boolean;
}

export interface SessionDeleteResult {
  deleted: boolean;
  cacheRowsDeleted: number;
  listeningEventsDeleted: number;
}

export type AnalyticsContractStatus = "complete" | "partial" | "insufficient_data" | "stale_import" | "processing" | "failed";

export interface AnalyticsContractWarning {
  code: string;
  severity: "info" | "warning" | "error";
  message: string;
  affectedFields: string[];
}

export interface AnalyticsEnvelope<T> {
  apiSchemaVersion: 1;
  status: AnalyticsContractStatus;
  source: MusicSource;
  period: { type: string; month: string | null; start: string; end: string; timezone: string; label: string };
  provenance: { importBatchId: string | null; dataFingerprint: string; parserVersion: number | null; eventSchemaVersion: number; analyticsVersion: number };
  dataQuality: { acceptedPlayCount: number; timestampCoverage: number; durationCoverage: number; genreCoverage: number; releaseYearCoverage: number };
  warnings: AnalyticsContractWarning[];
  data: T;
}

export type TakeoutImportStage = "queued" | "parsing" | "normalizing" | "rebuilding" | "saving" | "complete" | "failed";

export interface TakeoutImportQueued {
  jobId: string;
  status: "queued";
}

export interface TakeoutImportStatus {
  jobId: string;
  status: TakeoutImportStage;
  progress: number;
  message: string;
  errorCode: string | null;
  importedCount: number | null;
  trackCount: number | null;
  playCount: number | null;
}

export type RefreshStage = "queued" | "fetching" | "normalizing" | "enriching" | "rebuilding" | "saving" | "complete" | "failed";

export interface RefreshQueued {
  jobId: string;
  status: "queued";
}

export interface RefreshStatus {
  jobId: string;
  status: RefreshStage;
  progress: number;
  message: string;
  errorCode: string | null;
  refreshedAt: string | null;
  useDemo: boolean;
  warnings: string[];
  coverage: Record<string, unknown> | null;
  trackCount: number | null;
  playCount: number | null;
}

export type DurationEnrichmentStage = "idle" | "queued" | "resolving" | "rebuilding" | "complete" | "failed";

export interface DurationEnrichmentStatus {
  jobId: string | null;
  status: DurationEnrichmentStage;
  progress: number;
  message: string;
  errorCode: string | null;
  attempted: number | null;
  added: number | null;
  failed: number | null;
  continueQueued: boolean | null;
}

export type GenreEnrichmentStage = "idle" | "queued" | "resolving" | "rebuilding" | "complete" | "failed";

export interface GenreEnrichmentStatus {
  jobId: string | null;
  status: GenreEnrichmentStage;
  progress: number;
  message: string;
  errorCode: string | null;
  provider: string;
  source: MusicSource;
  attempted: number | null;
  matched: number | null;
  failed: number | null;
  providerError: string | null;
  matchedEventCount: number | null;
  appliedCached: number | null;
  remainingCandidates: number | null;
  unknownArtistCount: number | null;
  recordingAttempted: number | null;
  recordingMatched: number | null;
  recordingAppliedEventCount: number | null;
  recordingFailed: number | null;
  recordingRemainingCandidates: number | null;
  recordingProviderError: string | null;
  recordingMetadataAdded: number | null;
  beforeCoverage: number | null;
  afterCoverage: number | null;
  unknownEventCount: number | null;
}

export type ReportGenerationStage = "queued" | "building" | "writing" | "saving" | "complete" | "failed";

export interface ReportGenerationQueued {
  jobId: string;
  status: "queued";
}

export interface ReportGenerationStatus {
  jobId: string;
  status: ReportGenerationStage;
  progress: number;
  message: string;
  errorCode: string | null;
  provider: string;
  createdAt: string;
  updatedAt: string;
  finishedAt: string | null;
  report: PersonaReport | null;
}

export interface SpotifyStatus {
  configured: boolean;
  connected: boolean;
  cached_data_available: boolean;
  historical_data_available: boolean;
  historical_play_count: number;
  display_name: string | null;
  profile_image: string | null;
  spotify_user_id: string | null;
  last_synced_at: string | null;
  message: string;
}

export interface Coverage {
  earliest_detected_play: string | null;
  latest_detected_play: string | null;
  earliest_available_play: string | null;
  days_represented: number;
  full_365_day_analysis: boolean;
  dated_history_items: number;
  undated_history_items: number;
  history_items_returned: number;
  date_data_available: boolean;
  history_coverage_status: string;
  notes: string[];
}

export interface ScoreMetric {
  key: string;
  name: string;
  value: number;
  label: string;
  explanation: string;
  formula: string;
  inputs: Record<string, unknown>;
  interpretation?: ScoreInterpretation;
}

export interface ScoreInterpretation {
  status_title: string;
  plain_english: string;
  confidence: string;
  evidence: string[];
}

export interface TasteCluster {
  name: string;
  share: number;
  value: number;
  play_weight?: number;
}

export interface TasteDNA {
  core_dna: string[];
  secondary_influences: string[];
  sonic_traits: string[];
  era_preference: string;
  artist_concentration: { label: string; value: number };
  exploration_vs_comfort: { label: string; value: number };
}

export interface TasteInterpretation {
  core_genre_families: TasteCluster[];
  secondary_genre_families: TasteCluster[];
  side_quests: TasteCluster[];
  cluster_shares: TasteCluster[];
  canonical_genre_shares: TasteCluster[];
  sonic_traits: string[];
  listening_character: string[];
  evidence: string[];
  summary: string;
  coverage: {
    totalEventCount: number;
    usableArtistEventCount: number;
    invalidArtistEventCount: number;
    classifiedEventCount: number;
    curatedEventCount: number;
    sourceGenreEventCount: number;
    unknownEventCount: number;
    genreCoveragePercent: number;
    genre_coverage_percent: number;
    curated_artist_coverage_percent: number;
    inferred_artist_coverage_percent: number;
    unknown_artist_coverage_percent: number;
    topUnknownArtists: { artist: string; playCount: number; playShare: number }[];
    unmatchedNameVariants: { variant: string; playCount: number }[];
  };
  diversity: {
    broad_cluster_score: number;
    within_cluster_score: number;
    label: string;
  };
  taste_dna: TasteDNA;
}

export interface GenreProfile {
  canonical_genres: string[];
  broad_clusters: string[];
  sonic_traits: string[];
  confidence: string;
  confidence_label: string;
  source: string;
  display_genres: string[];
  taste_role_hint?: string;
  is_curated: boolean;
}

export interface TopTrack {
  rank: number;
  track_id: string;
  video_id: string | null;
  source?: MusicSource | string;
  source_track_id?: string | null;
  title: string;
  artist: string;
  artists: string[];
  album: string | null;
  release_year: number | null;
  thumbnail: string | null;
  track_image_url?: string | null;
  track_image_source?: string | null;
  album_art_url?: string | null;
  album_art_source?: string | null;
  play_count: number;
  last_played: string | null;
  why_it_ranked: string;
  genre_clusters: string[];
  popularity?: number | null;
  spotify_time_range?: string | null;
  spotify_rank?: number | null;
  spotify_signal_label?: string | null;
}

export interface TopArtist {
  rank: number;
  artist: string;
  artist_id: string | null;
  source?: MusicSource | string;
  source_artist_id?: string | null;
  image: string | null;
  artist_image_url?: string | null;
  artist_image_source?: string | null;
  play_count: number;
  share_of_listens: number;
  unique_songs_played: number;
  most_played_song: string | null;
  artist_loyalty_label: string;
  related_genres: string[];
  observation: string;
  genre_profile: GenreProfile;
  broad_clusters: string[];
  genre_confidence: string;
  genre_confidence_label: string;
  taste_role: string;
  why_it_matters: string;
  popularity?: number | null;
  followers?: number | null;
}

export interface Overview {
  headline_persona: string;
  top_3_artists: TopArtist[];
  top_3_tracks: TopTrack[];
  top_genre_cluster: string;
  favourite_decade: string;
  repeat_score: ScoreMetric;
  discovery_score: ScoreMetric;
  taste_confidence: ScoreMetric;
  last_refreshed_at: string | null;
  coverage: Coverage;
  total_detected_plays: number;
  unique_tracks: number;
  unique_artists: number;
  taste_interpretation: TasteInterpretation;
  taste_dna: TasteDNA;
  genre_coverage_percent: number;
  curated_artist_coverage_percent: number;
  inferred_artist_coverage_percent: number;
  unknown_artist_coverage_percent: number;
  use_demo: boolean;
  warnings: string[];
  source?: MusicSource;
  source_label?: string;
  selected_period?: OverviewPeriod;
}

export type OverviewPeriodKey = "this_month" | "month" | "last_7" | "last_30" | "rolling_year" | "all";

export interface OverviewPeriod {
  key: OverviewPeriodKey;
  month: string | null;
  label: string;
  timezone: string;
  startDate: string;
  endDate: string;
  availableMonths: { value: string; label: string }[];
}

export interface MusicIdentity {
  characterTitle: string;
  tagline: string;
  explanation: string;
  mostActiveSound: {
    label: string;
    description: string;
  };
  generationSource: "gemma" | "cache-gemma" | "fallback";
}

export interface MusicalAgeFactors {
  repeatAttachment: number;
  discovery: number;
  tasteStability: number;
  catalogMaturity: number;
  albumDepth: number;
  crossEraBreadth: number;
  emotionalIntensity: number;
  reflectiveListening: number;
}

export interface MusicalAge {
  age: number;
  likelyMin: number;
  likelyMax: number;
  title: string;
  summary: string;
  explanation: string;
  confidence: number;
  confidenceLabel: string;
  factors: MusicalAgeFactors;
  calculationVersion: number;
  generationSource: "gemma" | "cache-gemma" | "fallback";
  sourcePeriod: OverviewPeriod;
  strongestFactors: string[];
  metadataCoverage: {
    releaseYearPercent: number;
    traitPercent: number;
    durationPercent: number;
  };
}

export interface TopFiveSong {
  rank: number;
  title: string;
  artist: string;
  album: string | null;
  imageUrl: string | null;
  detectedPlays: number;
  detectedMinutes: number | null;
}

export interface TopFiveArtist {
  rank: number;
  name: string;
  imageUrl: string | null;
  detectedPlays: number;
  uniqueSongs: number;
}

export interface TopFive {
  period: OverviewPeriod;
  songs: TopFiveSong[];
  artists: TopFiveArtist[];
}

export interface OverviewResponse {
  schemaVersion: 3;
  source: MusicSource;
  sourceLabel: string;
  selectedPeriod: OverviewPeriod;
  musicalAgePeriod: OverviewPeriod;
  overview: Overview;
  identity: MusicIdentity;
  musicalAge: MusicalAge;
  topFive: TopFive;
  languageFingerprint: string;
}

export interface PeriodSpec {
  period: string;
  month: string | null;
  label: string;
  timezone: string;
  start_date: string;
  end_date: string;
  available_months: { value: string; label: string }[];
}

export interface DurationQuality {
  total_detected_plays: number;
  detected_music_plays: number;
  plays_with_usable_duration: number;
  duration_coverage_percent: number;
  total_minutes_included: number;
  events_excluded_from_minutes: number;
  main_exclusion_reasons: { reason: string; count: number }[];
  confidence_badge: string;
  methodology: string;
}

export interface ListeningMinutes {
  period: PeriodSpec;
  metrics: {
    today_detected_minutes: number;
    yesterday_detected_minutes: number;
    current_week_total_minutes: number;
    current_month_total_minutes: number;
    rolling_365_total_minutes: number;
    selected_period_total_minutes: number;
    selected_period_total_formatted: string;
    daily_average_minutes: number;
    average_active_day_minutes: number;
    longest_detected_listening_day: { date: string; minutes: number; formatted: string } | null;
    quietest_active_day: { date: string; minutes: number; formatted: string } | null;
    active_listening_days: number;
    current_listening_streak_days: number;
  };
  duration_quality: DurationQuality;
  daily: ChartPoint[];
  weekly: ChartPoint[];
  monthly: ChartPoint[];
  heatmap: { date: string; week_start: string; weekday: string; weekday_index: number; value: number }[];
  summary_sentence: string;
  methodology: string;
}

export interface InsightsProfileAxis {
  key: string;
  label: string;
  value: number;
  detectedPlays: number;
  contributingArtists?: string[];
  metadataSource?: string;
  confidence?: string;
}

export interface InsightsRhythmPoint {
  label: string;
  startDate: string;
  detectedMinutes: number;
  playCount: number;
  durationCoveragePercent: number;
}

export interface InsightsRankingArtist {
  rank: number;
  artist: string;
  imageUrl: string | null;
  detectedPlays: number;
  share: number;
}

export interface InsightsRankingSong {
  rank: number;
  title: string;
  artist: string;
  imageUrl: string | null;
  detectedPlays: number;
  share: number;
}

export interface InsightsResponse {
  schemaVersion: 1;
  period: PeriodSpec & { display_label: string };
  summary: {
    detectedMinutes: number;
    detectedMinutesFormatted: string;
    activeDays: number;
    averageActiveDayMinutes: number;
    longestDayMinutes: number;
    longestDayDate: string | null;
    currentStreakDays: number;
    detectedPlays: number;
    durationCoveragePercent: number;
  };
  durationQuality: DurationQuality;
  musicProfile: {
    coverage: number;
    classifiedPlays: number;
    unclassifiedPlays: number;
    totalPlays: number;
    axes: InsightsProfileAxis[];
    methodology: string;
  };
  scores: ScoreMetric[];
  rhythm: {
    weekly: InsightsRhythmPoint[];
    monthly: InsightsRhythmPoint[];
  };
  topArtists: InsightsRankingArtist[];
  repeatedSongs: InsightsRankingSong[];
  dailyIntensity: { date: string; week_start: string; weekday: string; weekday_index: number; value: number }[];
  sampleWarning: string | null;
  methodology: string;
}

export interface TopMovement {
  direction: "up" | "down" | "new" | "no_change";
  previous_rank: number | null;
  rank_delta: number | null;
  label: string;
}

export interface PeriodTopItem {
  key: string;
  rank: number;
  track_id: string | null;
  video_id: string | null;
  source?: MusicSource | string;
  source_track_id?: string | null;
  source_artist_id?: string | null;
  title: string | null;
  artist: string;
  album: string | null;
  thumbnail: string | null;
  artist_image_url?: string | null;
  artist_image_source?: string | null;
  track_image_url?: string | null;
  track_image_source?: string | null;
  album_art_url?: string | null;
  album_art_source?: string | null;
  play_count: number;
  detected_seconds?: number;
  detected_minutes: number;
  detected_minutes_formatted: string;
  share_of_period: number;
  duration_coverage_percent: number;
  unique_songs: number | null;
  most_played_song: string | null;
  last_played: string | null;
  movement: TopMovement | null;
  interpretation_label: string;
  spotify_time_range?: string | null;
  spotify_rank?: number | null;
  spotify_signal_label?: string | null;
}

export interface PeriodTopResponse {
  period: PeriodSpec;
  type: "tracks" | "artists";
  total_play_count: number;
  ranked_music_play_count: number;
  duration_quality: DurationQuality;
  sample_warning: string | null;
  items: PeriodTopItem[];
  totalAvailableResults?: number;
  methodology: string;
  classification_rules: string[];
}

export interface TopAlbumItem {
  rank: number;
  key: string;
  album: string;
  artist: string;
  album_id: string | null;
  thumbnail: string | null;
  album_image_url?: string | null;
  album_image_source?: string | null;
  track_image_url?: string | null;
  plays: number;
  detected_minutes: number;
  detected_minutes_formatted: string;
  unique_songs: number;
  most_played_song: string | null;
  share: number;
  duration_coverage_percent: number;
  last_played: string | null;
  label: string;
  album_signal_note: string;
}

export interface TopAlbumsResponse {
  period: PeriodSpec;
  period_label: string;
  total_play_count: number;
  duration_quality: DurationQuality;
  sample_warning: string | null;
  albums: TopAlbumItem[];
  methodology: string;
}

export interface TopDrilldownSong {
  rank: number;
  track_id: string | null;
  video_id: string | null;
  title: string | null;
  artist: string | null;
  album: string | null;
  thumbnail: string | null;
  track_image_url?: string | null;
  track_image_source?: string | null;
  album_art_url?: string | null;
  album_art_source?: string | null;
  plays: number;
  detected_minutes: number;
  detected_minutes_formatted: string;
  last_played: string | null;
  first_played: string | null;
  duration_coverage_percent: number;
  share_of_artist_plays?: number;
  share_of_album_plays?: number;
}

export interface TopArtistSongsResponse {
  artist: string;
  artist_thumbnail: string | null;
  artist_image_url?: string | null;
  artist_image_source?: string | null;
  period_label: string;
  period: PeriodSpec;
  total_plays: number;
  unique_songs: number;
  detected_minutes: number;
  detected_minutes_formatted: string;
  duration_coverage_percent: number;
  most_replayed_song: string | null;
  songs: TopDrilldownSong[];
}

export interface TopAlbumSongsResponse {
  album: string;
  artist: string | null;
  period_label: string;
  period: PeriodSpec;
  total_plays: number;
  unique_songs: number;
  detected_minutes: number;
  detected_minutes_formatted: string;
  duration_coverage_percent: number;
  most_played_song: string | null;
  songs: TopDrilldownSong[];
}

export interface TasteDnaNode {
  id: string;
  name: string;
  share: number;
  size: number;
  x: number;
  y: number;
  layer: string;
  detected_minutes: number;
  detected_minutes_formatted: string;
  top_artists: { name: string; plays: number }[];
  top_songs: { name: string; plays: number }[];
  canonical_genres: string[];
  sonic_traits: string[];
  confidence: number;
  role: string;
}

export interface TasteTraitNode {
  trait: string;
  support_percent: number;
  confidence: string;
  supporting_artists: { name: string; plays: number }[];
  supporting_clusters: { name: string; plays: number }[];
  explanation: string;
}

export interface TasteDnaExplorer {
  period: PeriodSpec;
  summary: string;
  core_identity: string;
  taste_interpretation: TasteInterpretation;
  duration_quality: DurationQuality;
  nodes: TasteDnaNode[];
  traits: TasteTraitNode[];
  structured_summary: { label: string; items: string[] }[];
  sample_warning: string | null;
  methodology: string;
}

export interface TasteDnaComparison {
  base_period: PeriodSpec;
  compare_period: PeriodSpec;
  deltas: { name: string; base_share: number; compare_share: number; delta: number }[];
  claims: {
    growing_cluster: { name: string; base_share: number; compare_share: number; delta: number } | null;
    declining_cluster: { name: string; base_share: number; compare_share: number; delta: number } | null;
    new_side_interest: { name: string; base_share: number; compare_share: number; delta: number } | null;
    stable_core_identity: string[];
  };
  summary_sentence: string;
  sample_warning: string | null;
}

export interface MusicCharacter {
  id: string;
  name: string;
  category: string;
  roast: string;
  profile: string;
  match_score: number;
}

export interface MusicCharacterResponse {
  period: PeriodSpec;
  primary: MusicCharacter;
  secondary: MusicCharacter | null;
  habits: string[];
  evidence_chips: string[];
  top_artists: { name: string; plays: number }[];
  top_clusters: { name: string; share: number }[];
  sonic_traits: string[];
  key_scores: Record<string, number>;
  sample_warning: string | null;
  deterministic: boolean;
  methodology: string;
}

export interface MusicCharacterRewrite {
  headline: string;
  one_liner: string;
  profile_paragraph: string;
  friendly_roast: string;
  why_it_fits: string[];
  mode: string;
  model: string;
}

export interface ChartPoint {
  name: string;
  value: number;
}

export interface PrerequisiteItem {
  name: string;
  available: boolean;
  detail: string;
}

export interface Prerequisites {
  ok: boolean;
  items: PrerequisiteItem[];
  ollama_model: string;
  ollama_reachable: boolean;
  model_installed: boolean;
  local_timezone: string;
  duration_enrichment_limit: number;
}

export interface AuthStatus {
  connected: boolean;
  auth_file_exists: boolean;
  auth_file_path: string;
  oauth_client_configured: boolean;
  account_name: string | null;
  message: string;
  cached_data_available: boolean;
  last_refreshed_at: string | null;
}

export interface PersonaReport {
  schemaVersion: 8;
  source: MusicSource;
  mode: "serious" | "playful" | "roast";
  period: PersonaReportPeriod;
  personality: PersonaReportPersonality;
  listeningWorld: PersonaListeningWorld;
  musicalAge: PersonaMusicalAge;
  topFive: PersonaTopFive;
  summary: PersonaReportSummary;
  explainers: PersonaReportExplainers;
  backgroundAlbums: PersonaBackgroundAlbum[];
  generation: PersonaGeneration;
  analyticsFingerprint: string;
  cacheKey: string;
}

export type PersonaReportPeriodKey = "rolling_year" | "this_month";

export interface PersonaReportExplainers {
  musicalAges: PersonaAgeExplainer[];
  personalities: PersonaPersonalityExplainer[];
}

export interface PersonaAgeExplainer { minAge: number; maxAge: number; title: string; summary: string; }
export interface PersonaPersonalityExplainer { id: string; name: string; category: string; profile: string; }

export interface PersonaReportPeriod {
  key: string;
  label: string;
  startDate: string;
  endDate: string;
  timezone: string;
}

export interface PersonaReportPersonality {
  id: string;
  title: string;
  shortDescription: string;
  roastDescription: string;
  confidence: number;
  habits: string[];
  generationSource: "gemma" | "cache-gemma" | "hosted-llm" | "cache-hosted-llm" | "fallback";
}

export interface PersonaGenre {
  key: string;
  label: string;
  percentage: number;
  detectedPlays: number;
}

export interface PersonaListeningWorld {
  detectedMinutes: number;
  formattedTime: string;
  durationCoverage: number;
  genreCoverage: number;
  genres: PersonaGenre[];
  interpretation: string;
}

export interface PersonaMusicalAge {
  age: number;
  likelyMin: number;
  likelyMax: number;
  title: string;
  confidence: number;
  confidenceLabel: string;
  explanation: string;
  typicalRange: number[];
  weightedMedianReleaseYear: number;
  dominantDecade: string;
  releaseYearCoverage: number;
  sourcePeriod: PersonaReportPeriod;
}

export interface PersonaTopSong {
  rank: number;
  albumImageUrl: string | null;
  trackImageUrl: string | null;
  title: string;
  artist: string;
  album: string | null;
  detectedPlays: number;
  detectedMinutes: number;
  formattedMinutes: string;
}

export interface PersonaTopArtist {
  rank: number;
  artistImageUrl: string | null;
  name: string;
  detectedPlays: number;
  uniqueSongs: number;
}

export interface PersonaTopFive {
  songs: PersonaTopSong[];
  artists: PersonaTopArtist[];
}

export interface PersonaReportSummary {
  headline: string;
  body: string;
  finalLine: string;
  generationSource: "gemma" | "cache-gemma" | "hosted-llm" | "cache-hosted-llm" | "fallback";
}

export interface PersonaBackgroundAlbum {
  albumBrowseId: string | null;
  albumTitle: string;
  artistName: string;
  albumImageUrl: string;
  detectedPlays: number;
}

export interface PersonaGeneration {
  source: "gemma" | "cache-gemma" | "hosted-llm" | "cache-hosted-llm" | "fallback";
  model: string;
  promptVersion: number;
  generatedAt: string;
  fallbackReason: string | null;
  durationMs: number | null;
}

export interface Recommendation {
  rank: number;
  track_title: string;
  artist: string;
  artists: string[];
  album: string | null;
  album_art: string | null;
  track_image_url?: string | null;
  track_image_source?: string | null;
  album_art_url?: string | null;
  album_art_source?: string | null;
  release_year: number | null;
  video_id: string | null;
  recommendation_type: "Safe" | "Adjacent" | "Discovery" | string;
  recommendation_group: string;
  why_this_fits: string;
  musical_connection: string;
  source_reason: string;
  score: number;
}
