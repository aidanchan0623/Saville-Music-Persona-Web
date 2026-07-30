from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class FriendlyError(BaseModel):
    error: str
    detail: str
    code: str = "request_failed"


class SessionStatusResponse(BaseModel):
    mode: Literal["local", "anonymous"]
    anonymous: bool
    sessionHint: str | None = None
    expiresAt: str | None = None
    accountConnectionsEnabled: bool


class SessionDeleteResponse(BaseModel):
    deleted: bool
    cacheRowsDeleted: int
    listeningEventsDeleted: int


class PrerequisiteItem(BaseModel):
    name: str
    available: bool
    detail: str


class PrerequisitesResponse(BaseModel):
    ok: bool
    items: list[PrerequisiteItem]
    ollama_model: str
    ollama_reachable: bool
    model_installed: bool
    local_timezone: str
    duration_enrichment_limit: int


class AuthStatusResponse(BaseModel):
    connected: bool
    auth_file_exists: bool
    auth_file_path: str
    oauth_client_configured: bool
    account_name: str | None = None
    message: str
    cached_data_available: bool = False
    last_refreshed_at: str | None = None


class RefreshRequest(BaseModel):
    use_demo: bool = False
    enrich_durations: bool = False


class RefreshQueuedResponse(BaseModel):
    jobId: str
    status: Literal["queued"]


class RefreshStatusResponse(BaseModel):
    jobId: str | None = None
    status: str
    progress: int
    message: str
    errorCode: str | None = None
    refreshedAt: str | None = None
    useDemo: bool = False
    warnings: list[str] = Field(default_factory=list)
    coverage: dict[str, Any] | None = None
    trackCount: int | None = None
    playCount: int | None = None
    createdAt: str | None = None
    updatedAt: str | None = None
    finishedAt: str | None = None


class TakeoutImportQueuedResponse(BaseModel):
    jobId: str
    status: str


class TakeoutImportStatusResponse(BaseModel):
    jobId: str
    status: str
    progress: int
    message: str
    errorCode: str | None = None
    importedCount: int | None = None
    trackCount: int | None = None
    playCount: int | None = None
    createdAt: str | None = None
    updatedAt: str | None = None
    finishedAt: str | None = None


class DurationEnrichmentStatusResponse(BaseModel):
    jobId: str | None = None
    status: str
    progress: int
    message: str
    errorCode: str | None = None
    attempted: int | None = None
    added: int | None = None
    failed: int | None = None
    continueQueued: bool | None = None
    releaseYearEnrichment: dict[str, int] | None = None
    trackMetadataEnrichment: dict[str, int] | None = None
    createdAt: str | None = None
    updatedAt: str | None = None
    finishedAt: str | None = None


class GenreEnrichmentStatusResponse(BaseModel):
    jobId: str | None = None
    status: str
    progress: int
    message: str
    errorCode: str | None = None
    provider: str = "musicbrainz"
    attempted: int | None = None
    matched: int | None = None
    failed: int | None = None
    providerError: str | None = None
    matchedEventCount: int | None = None
    appliedCached: int | None = None
    remainingCandidates: int | None = None
    unknownArtistCount: int | None = None
    recordingAttempted: int | None = None
    recordingMatched: int | None = None
    recordingAppliedEventCount: int | None = None
    recordingFailed: int | None = None
    recordingRemainingCandidates: int | None = None
    recordingProviderError: str | None = None
    beforeCoverage: float | None = None
    afterCoverage: float | None = None
    unknownEventCount: int | None = None
    createdAt: str | None = None
    updatedAt: str | None = None
    finishedAt: str | None = None


class OverviewPeriod(BaseModel):
    key: str
    month: str | None = None
    label: str
    timezone: str
    startDate: str
    endDate: str
    availableMonths: list[dict[str, str]] = Field(default_factory=list)


class MostActiveSound(BaseModel):
    label: str
    description: str


class OverviewIdentity(BaseModel):
    characterTitle: str
    tagline: str
    explanation: str
    mostActiveSound: MostActiveSound
    generationSource: Literal["gemma", "cache-gemma", "fallback"]


class MusicalAgeFactors(BaseModel):
    repeatAttachment: float
    discovery: float
    tasteStability: float
    catalogMaturity: float
    albumDepth: float
    crossEraBreadth: float
    emotionalIntensity: float
    reflectiveListening: float


class MusicalAge(BaseModel):
    age: int
    likelyMin: int
    likelyMax: int
    title: str
    summary: str
    explanation: str
    confidence: float
    confidenceLabel: str
    factors: MusicalAgeFactors
    calculationVersion: int
    generationSource: Literal["gemma", "cache-gemma", "fallback"]
    sourcePeriod: OverviewPeriod
    strongestFactors: list[str] = Field(default_factory=list)
    metadataCoverage: dict[str, float] = Field(default_factory=dict)


class TopFiveSong(BaseModel):
    rank: int
    title: str
    artist: str
    album: str | None = None
    imageUrl: str | None = None
    detectedPlays: int
    detectedMinutes: float | None = None


class TopFiveArtist(BaseModel):
    rank: int
    name: str
    imageUrl: str | None = None
    detectedPlays: int
    uniqueSongs: int


class TopFive(BaseModel):
    period: OverviewPeriod
    songs: list[TopFiveSong] = Field(default_factory=list)
    artists: list[TopFiveArtist] = Field(default_factory=list)


class OverviewAnalysisResponse(BaseModel):
    schemaVersion: int
    source: Literal["youtube", "spotify"]
    sourceLabel: str
    selectedPeriod: OverviewPeriod
    musicalAgePeriod: OverviewPeriod
    overview: dict[str, Any]
    identity: OverviewIdentity
    musicalAge: MusicalAge
    topFive: TopFive
    languageFingerprint: str


class InsightsPeriod(BaseModel):
    period: str
    month: str | None = None
    label: str
    display_label: str
    timezone: str
    start_date: str
    end_date: str
    available_months: list[dict[str, str]] = Field(default_factory=list)


class InsightsSummary(BaseModel):
    detectedMinutes: float
    detectedMinutesFormatted: str
    activeDays: int
    averageActiveDayMinutes: float
    longestDayMinutes: float
    longestDayDate: str | None = None
    currentStreakDays: int
    detectedPlays: int
    durationCoveragePercent: float


class InsightsProfileAxis(BaseModel):
    key: str
    label: str
    value: float
    detectedPlays: int
    contributingArtists: list[str] = Field(default_factory=list)
    metadataSource: str = "unavailable genre metadata"
    confidence: str = "low"


class InsightsMusicProfile(BaseModel):
    coverage: float
    classifiedPlays: int
    unclassifiedPlays: int
    totalPlays: int
    axes: list[InsightsProfileAxis] = Field(default_factory=list)
    methodology: str


class InsightsScoreInterpretation(BaseModel):
    status_title: str
    plain_english: str
    confidence: str
    evidence: list[str] = Field(default_factory=list)


class InsightsScore(BaseModel):
    key: str
    name: str
    value: float
    label: str
    explanation: str
    formula: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    interpretation: InsightsScoreInterpretation | None = None


class InsightsRhythmPoint(BaseModel):
    label: str
    startDate: str
    detectedMinutes: float
    playCount: int
    durationCoveragePercent: float


class InsightsRhythm(BaseModel):
    weekly: list[InsightsRhythmPoint] = Field(default_factory=list)
    monthly: list[InsightsRhythmPoint] = Field(default_factory=list)


class InsightsArtist(BaseModel):
    rank: int
    artist: str
    imageUrl: str | None = None
    detectedPlays: int
    share: float


class InsightsSong(BaseModel):
    rank: int
    title: str
    artist: str
    imageUrl: str | None = None
    detectedPlays: int
    share: float


class InsightsIntensityDay(BaseModel):
    date: str
    week_start: str
    weekday: str
    weekday_index: int
    value: float


class InsightsResponse(BaseModel):
    schemaVersion: Literal[1]
    period: InsightsPeriod
    summary: InsightsSummary
    durationQuality: dict[str, Any]
    musicProfile: InsightsMusicProfile
    scores: list[InsightsScore] = Field(default_factory=list)
    rhythm: InsightsRhythm
    topArtists: list[InsightsArtist] = Field(default_factory=list)
    repeatedSongs: list[InsightsSong] = Field(default_factory=list)
    dailyIntensity: list[InsightsIntensityDay] = Field(default_factory=list)
    sampleWarning: str | None = None
    methodology: str


class ReportRequest(BaseModel):
    mode: Literal["serious", "playful", "roast"] = "serious"
    source: Literal["youtube", "spotify"] = "youtube"
    period: Literal["rolling_year", "this_month"] = "rolling_year"


class StrictReportModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReportPeriod(StrictReportModel):
    key: str
    label: str
    startDate: str
    endDate: str
    timezone: str


class ReportPersonality(StrictReportModel):
    id: str
    title: str
    shortDescription: str
    roastDescription: str
    confidence: float = Field(ge=0, le=1)
    habits: list[str] = Field(max_length=3)
    generationSource: Literal["gemma", "cache-gemma", "fallback"]


class ReportGenre(StrictReportModel):
    key: str
    label: str
    percentage: float = Field(ge=0, le=100)
    detectedPlays: int = Field(ge=0)


class ReportListeningWorld(StrictReportModel):
    detectedMinutes: float = Field(ge=0)
    formattedTime: str
    durationCoverage: float = Field(ge=0, le=1)
    genreCoverage: float = Field(ge=0, le=1)
    genres: list[ReportGenre]
    interpretation: str


class ReportMusicalAge(StrictReportModel):
    age: int
    likelyMin: int
    likelyMax: int
    title: str
    confidence: float = Field(ge=0, le=1)
    confidenceLabel: str
    explanation: str
    typicalRange: list[int]
    weightedMedianReleaseYear: int
    dominantDecade: str
    releaseYearCoverage: float
    sourcePeriod: ReportPeriod


class ReportTopSong(StrictReportModel):
    rank: int = Field(ge=1, le=5)
    albumImageUrl: str | None = None
    trackImageUrl: str | None = None
    title: str
    artist: str
    album: str | None = None
    detectedPlays: int = Field(ge=0)
    detectedMinutes: float = Field(ge=0)
    formattedMinutes: str


class ReportTopArtist(StrictReportModel):
    rank: int = Field(ge=1, le=5)
    artistImageUrl: str | None = None
    name: str
    detectedPlays: float = Field(ge=0)
    uniqueSongs: int = Field(ge=0)


class ReportTopFive(StrictReportModel):
    songs: list[ReportTopSong]
    artists: list[ReportTopArtist]


class ReportSummary(StrictReportModel):
    headline: str
    body: str
    finalLine: str
    generationSource: Literal["gemma", "cache-gemma", "fallback"]


class ReportBackgroundAlbum(StrictReportModel):
    albumBrowseId: str | None = None
    albumTitle: str
    artistName: str
    albumImageUrl: str
    detectedPlays: int = Field(ge=0)


class ReportGeneration(StrictReportModel):
    source: Literal["gemma", "cache-gemma", "fallback"]
    model: str
    promptVersion: int
    generatedAt: str
    fallbackReason: str | None = None
    durationMs: int | None = None


class ReportAgeExplainer(StrictReportModel):
    minAge: int
    maxAge: int
    title: str
    summary: str


class ReportPersonalityExplainer(StrictReportModel):
    id: str
    name: str
    category: str
    profile: str


class ReportExplainers(StrictReportModel):
    musicalAges: list[ReportAgeExplainer]
    personalities: list[ReportPersonalityExplainer]


class PersonaReportResponse(StrictReportModel):
    schemaVersion: Literal[8] = 8
    source: Literal["youtube", "spotify"]
    mode: Literal["serious", "playful", "roast"]
    period: ReportPeriod
    personality: ReportPersonality
    listeningWorld: ReportListeningWorld
    musicalAge: ReportMusicalAge
    topFive: ReportTopFive
    summary: ReportSummary
    explainers: ReportExplainers
    backgroundAlbums: list[ReportBackgroundAlbum]
    generation: ReportGeneration
    analyticsFingerprint: str
    cacheKey: str


class PlaylistCreateRequest(BaseModel):
    confirm: bool = False
    title: str = "Saville Recommendations"


class PlaylistCreateResponse(BaseModel):
    playlist_id: str
    title: str
    added_count: int
    message: str
