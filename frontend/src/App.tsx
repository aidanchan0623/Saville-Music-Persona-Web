import { Menu, Music2, X } from "lucide-react";
import type { CSSProperties } from "react";
import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api/client";
import { pollChainedJob, pollTakeoutImport, runExclusiveOperation } from "./api/takeoutImport";
import { GlowPanel } from "./components/GlowPanel";
import { DesktopSidebar } from "./components/navigation/DesktopSidebar";
import { NAVIGATION_ITEMS } from "./components/navigation/navigation";
import type { Page } from "./components/navigation/navigation";
import { OverviewPage } from "./pages/OverviewPage";
import { InsightsPage } from "./pages/InsightsPage";
import { RecommendationsPage } from "./pages/RecommendationsPage";
import { ReportPage } from "./pages/ReportPage";
import { SettingsPage } from "./pages/SettingsPage";
import { Top10Page } from "./pages/Top10Page";
import type { AuthStatus, MusicSource, OverviewResponse, PersonaReport, Prerequisites, Recommendation, SessionStatus, SpotifyStatus, TopArtist, TopTrack } from "./types/api";

const PAGE_PATHS: Record<Page, string> = {
  overview: "/",
  top10: "/top10",
  insights: "/insights",
  report: "/report",
  recommendations: "/recommendations",
  settings: "/settings",
};

const PATH_PAGES = new Map(Object.entries(PAGE_PATHS).map(([page, path]) => [path, page as Page]));
const ColorBends = lazy(() => import("./components/reactbits/ColorBends/ColorBends"));

function getHistoryPage(): Page {
  if (typeof window === "undefined") return "overview";
  const path = normalisePath(window.location.pathname);
  if (path === "/scores" || path === "/patterns") return "insights";
  const routePage = PATH_PAGES.get(path);
  if (routePage) return routePage;
  const value = window.history.state?.page;
  return NAVIGATION_ITEMS.some((item) => item.id === value) ? value : "overview";
}

function normalisePath(pathname: string) {
  if (!pathname || pathname === "/") return "/";
  return pathname.replace(/\/+$/, "").toLowerCase();
}

export default function App() {
  const [page, setPage] = useState<Page>(() => getHistoryPage());
  const [titleVisitId, setTitleVisitId] = useState(0);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => localStorage.getItem("smp_sidebar_collapsed") !== "false");
  const [source, setSource] = useState<MusicSource>(() => {
    const querySource = new URLSearchParams(window.location.search).get("source");
    if (querySource === "spotify") return "spotify";
    return "youtube";
  });
  const [useDemo, setUseDemo] = useState(() => localStorage.getItem("smp_use_demo") === "true");
  const [overview, setOverview] = useState<OverviewResponse | null>(null);
  const [tracks, setTracks] = useState<TopTrack[]>([]);
  const [artists, setArtists] = useState<TopArtist[]>([]);
  const [report, setReport] = useState<PersonaReport | null>(null);
  const [recommendations, setRecommendations] = useState<Recommendation[]>([]);
  const [auth, setAuth] = useState<AuthStatus | null>(null);
  const [spotifyStatus, setSpotifyStatus] = useState<SpotifyStatus | null>(null);
  const [prerequisites, setPrerequisites] = useState<Prerequisites | null>(null);
  const [runtime, setRuntime] = useState<SessionStatus | null>(null);
  const [sessionReady, setSessionReady] = useState(false);
  const [sessionError, setSessionError] = useState<string | null>(null);
  const [sessionAttempt, setSessionAttempt] = useState(0);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const loadAnalysisTokenRef = useRef(0);
  const operationInFlightRef = useRef(false);
  const importAbortControllerRef = useRef<AbortController | null>(null);
  const refreshAbortControllerRef = useRef<AbortController | null>(null);
  const durationAbortControllerRef = useRef<AbortController | null>(null);
  const genreAbortControllerRef = useRef<AbortController | null>(null);
  const reportAbortControllerRef = useRef<AbortController | null>(null);
  const lastTakeoutFileRef = useRef<File | null>(null);
  const lastSpotifyHistoryFileRef = useRef<File | null>(null);
  const skipNextSourceLoadRef = useRef(false);
  const [canRetryTakeout, setCanRetryTakeout] = useState(false);
  const [canRetrySpotifyHistory, setCanRetrySpotifyHistory] = useState(false);

  const loadStatus = async () => {
    const [nextPrerequisites, nextAuth, nextSpotifyStatus] = await Promise.all([api.prerequisites(), api.authStatus(), api.spotifyStatus()]);
    setPrerequisites(nextPrerequisites);
    setAuth(nextAuth);
    setSpotifyStatus(nextSpotifyStatus);
  };

  const clearAnalysis = () => {
    loadAnalysisTokenRef.current += 1;
    setOverview(null);
    setTracks([]);
    setArtists([]);
    setReport(null);
    setRecommendations([]);
  };

  const navigate = (next: Page) => {
    if (next !== page) {
      window.history.pushState({ ...(window.history.state ?? {}), page: next }, "", `${PAGE_PATHS[next]}${window.location.search}`);
      setTitleVisitId((value) => value + 1);
    }
    setPage(next);
    setMobileOpen(false);
  };

  const loadAnalysis = async (activeSource: MusicSource = source) => {
    const requestToken = loadAnalysisTokenRef.current + 1;
    loadAnalysisTokenRef.current = requestToken;
    const isCurrentRequest = () => loadAnalysisTokenRef.current === requestToken;
    const setIfCurrent = <T,>(setter: (value: T) => void) => (value: T) => {
      if (isCurrentRequest()) setter(value);
    };
    setReport((current) => current?.source === activeSource ? current : null);
    void api.latestReport(activeSource)
      .then(setIfCurrent(setReport))
      .catch(() => { if (isCurrentRequest()) setReport((current) => current?.source === activeSource ? current : null); });
    const nextOverview = await api.overview("this_month", null, activeSource);
    if (!isCurrentRequest()) return;
    setOverview(nextOverview);
    setMessage(null);
    setTracks([]);
    setArtists([]);
    void api.topTracks(activeSource).then(setIfCurrent(setTracks)).catch(() => { if (isCurrentRequest()) setTracks([]); });
    void api.topArtists(activeSource).then(setIfCurrent(setArtists)).catch(() => { if (isCurrentRequest()) setArtists([]); });
    if (activeSource === "youtube") {
      try {
        const nextRecommendations = await api.recommendations();
        if (isCurrentRequest()) setRecommendations(nextRecommendations);
      } catch {
        if (isCurrentRequest()) setRecommendations([]);
      }
    } else {
      if (isCurrentRequest()) setRecommendations([]);
    }
  };

  useEffect(() => {
    localStorage.setItem("smp_use_demo", String(useDemo));
  }, [useDemo]);

  useEffect(() => {
    let cancelled = false;
    setSessionReady(false);
    setSessionError(null);
    api.sessionStatus()
      .then((status) => {
        if (cancelled) return;
        setRuntime(status);
        if (status.anonymous) setUseDemo(false);
        setSessionReady(true);
      })
      .catch((error) => {
        if (!cancelled) setSessionError(error instanceof Error ? error.message : "Could not start a private upload session.");
      });
    return () => { cancelled = true; };
  }, [sessionAttempt]);

  useEffect(() => {
    localStorage.setItem("smp_sidebar_collapsed", String(sidebarCollapsed));
  }, [sidebarCollapsed]);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get("spotify_connected") === "1") setMessage("Spotify connected and refreshed. YouTube Music data remains separate.");
    if (params.get("spotify_error")) setMessage("Spotify connection did not complete. You can retry from Settings.");
  }, []);

  useEffect(() => {
    if (!sessionReady) return;
    const refreshStatus = () => {
      void loadStatus().catch((error) => setMessage(error.message));
    };
    refreshStatus();
    const interval = window.setInterval(refreshStatus, 30_000);
    return () => window.clearInterval(interval);
  }, [sessionReady]);

  useEffect(() => {
    const legacyPath = ["/scores", "/patterns"].includes(normalisePath(window.location.pathname));
    window.history.replaceState(
      { ...(window.history.state ?? {}), page },
      "",
      legacyPath ? `${PAGE_PATHS.insights}${window.location.search}` : window.location.href,
    );
    const handlePopState = () => {
      setPage(getHistoryPage());
      setTitleVisitId((value) => value + 1);
      setMobileOpen(false);
    };
    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, []);

  useEffect(() => {
    if (!sessionReady) return;
    if (skipNextSourceLoadRef.current) {
      skipNextSourceLoadRef.current = false;
      return;
    }
    void loadAnalysis(source).catch((error) => {
      clearAnalysis();
      setMessage(
        error instanceof Error
          ? error.message
          : source === "spotify"
            ? "Upload Spotify history or connect Spotify in Settings."
            : "YouTube Music analysis could not be loaded.",
      );
    });
  }, [source, sessionReady]);

  useEffect(() => () => {
    importAbortControllerRef.current?.abort();
    refreshAbortControllerRef.current?.abort();
    durationAbortControllerRef.current?.abort();
    genreAbortControllerRef.current?.abort();
    reportAbortControllerRef.current?.abort();
  }, []);

  const refresh = async () => {
    if (runtime?.anonymous) {
      navigate("settings");
      setMessage("Upload a Google Takeout or Spotify export to begin this anonymous session.");
      return;
    }
    const started = await runExclusiveOperation(operationInFlightRef, setBusy, async () => {
      setMessage(source === "spotify" ? "Refreshing local Spotify data..." : useDemo ? "Loading anonymised demo listening history..." : "Refreshing local YouTube Music data...");
      try {
        if (source === "spotify") {
          const response = await api.spotifyRefresh();
          await loadStatus();
          await loadAnalysis(source);
          setMessage(`Refreshed ${response.track_count} tracks and ${response.play_count} local signals.`);
          return;
        }
        refreshAbortControllerRef.current?.abort();
        const controller = new AbortController();
        refreshAbortControllerRef.current = controller;
        const queued = await api.refresh(useDemo, controller.signal);
        const response = await pollTakeoutImport(
          (signal) => api.refreshStatus(queued.jobId, signal),
          {
            signal: controller.signal,
            timeoutMs: 12 * 60 * 1000,
            intervalMs: 1000,
            onStatus: (status) => setMessage(`${status.message} (${status.progress}%)`),
          },
        );
        await loadStatus();
        await loadAnalysis(source);
        if (source === "youtube" && !useDemo) void enrichDurationsInBackground();
        setMessage(`Refreshed ${(response.trackCount ?? 0).toLocaleString()} tracks and ${(response.playCount ?? 0).toLocaleString()} detected plays.`);
      } catch (error) {
        if (!(error instanceof DOMException && error.name === "AbortError")) {
          setMessage(error instanceof Error ? error.message : "Refresh failed. Your previous profile is still available.");
        }
      } finally {
        refreshAbortControllerRef.current = null;
      }
    });
    if (!started) {
      setMessage("Another data operation is already running. Wait for it to finish before refreshing again.");
    }
  };

  const generateReport = async (period: "rolling_year" | "this_month" = "rolling_year"): Promise<{ ok: boolean; message: string }> => {
    reportAbortControllerRef.current?.abort();
    const controller = new AbortController();
    reportAbortControllerRef.current = controller;
    const activeSource = source;
    setBusy(true);
    setMessage(runtime?.anonymous ? "Queuing your private persona report..." : "Queuing the local Gemma report writer...");
    try {
      const queued = await api.startReportGeneration("roast", activeSource, period);
      const result = await pollTakeoutImport(
        (signal) => api.reportGenerationStatus(queued.jobId, signal),
        {
          signal: controller.signal,
          intervalMs: 700,
          timeoutMs: 75_000,
          onStatus: (status) => setMessage(`${status.message} (${status.progress}%)`),
        },
      );
      if (!result.report) throw new Error("The report writer finished without a validated report. Please retry.");
      const nextReport = result.report;
      setReport(nextReport);
      navigate("report");
      void loadStatus().catch(() => undefined);
      const message = reportGenerationMessage(nextReport.generation.source, nextReport.generation.fallbackReason, Boolean(runtime?.anonymous));
      setMessage(message);
      return { ok: true, message };
    } catch (error) {
      const message = error instanceof Error ? error.message : "Report generation failed.";
      setMessage(message);
      return { ok: false, message };
    } finally {
      if (reportAbortControllerRef.current === controller) reportAbortControllerRef.current = null;
      setBusy(false);
    }
  };

  const generateRecommendations = async () => {
    if (source === "spotify") {
      setMessage("Recommendations currently use YouTube Music history. Switch to YouTube Music to generate them.");
      return;
    }
    setBusy(true);
    setMessage("Building recommendations from your local taste profile...");
    try {
      const next = await api.generateRecommendations();
      setRecommendations(next);
      setMessage(`Generated ${next.length} recommendations.`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Recommendation generation failed.");
    } finally {
      setBusy(false);
    }
  };

  const importTakeout = async (file: File): Promise<boolean> => {
    lastTakeoutFileRef.current = file;
    setCanRetryTakeout(false);
    let completed = false;
    const started = await runExclusiveOperation(operationInFlightRef, setBusy, async () => {
      const controller = new AbortController();
      importAbortControllerRef.current = controller;
      setMessage(`Uploading ${file.name} from Google Takeout...`);
      try {
        const queued = await api.importTakeout(file, controller.signal);
        const result = await pollTakeoutImport(
          (signal) => api.takeoutImportStatus(queued.jobId, signal),
          {
            signal: controller.signal,
            onStatus: (status) => setMessage(`${status.message} (${status.progress}%)`),
          },
        );
        await loadStatus();
        await loadAnalysis("youtube");
        if (source !== "youtube") {
          skipNextSourceLoadRef.current = true;
          setSource("youtube");
        }
        setCanRetryTakeout(false);
        setMessage(`${result.message} Imported ${result.importedCount ?? 0} history entries.`);
        void enrichDurationsInBackground();
        completed = true;
      } catch (error) {
        if (!(error instanceof DOMException && error.name === "AbortError")) {
          setCanRetryTakeout(true);
          setMessage(error instanceof Error ? error.message : "Takeout import failed. Retry with the same file.");
        }
      } finally {
        if (importAbortControllerRef.current === controller) importAbortControllerRef.current = null;
      }
    });
    if (!started) {
      setMessage("A Takeout import or refresh is already running. Wait for it to finish before starting another.");
      return false;
    }
    return completed;
  };

  const retryTakeout = () => {
    const file = lastTakeoutFileRef.current;
    if (file) void importTakeout(file);
  };

  const importSpotifyHistory = async (file: File): Promise<boolean> => {
    lastSpotifyHistoryFileRef.current = file;
    setCanRetrySpotifyHistory(false);
    let completed = false;
    const started = await runExclusiveOperation(operationInFlightRef, setBusy, async () => {
      const controller = new AbortController();
      importAbortControllerRef.current = controller;
      setMessage(`Uploading ${file.name} from Spotify...`);
      try {
        const queued = await api.importSpotifyHistory(file, controller.signal);
        const result = await pollTakeoutImport(
          (signal) => api.spotifyHistoryImportStatus(queued.jobId, signal),
          {
            signal: controller.signal,
            onStatus: (status) => setMessage(`${status.message} (${status.progress}%)`),
          },
        );
        await loadStatus();
        await loadAnalysis("spotify");
        if (source !== "spotify") {
          skipNextSourceLoadRef.current = true;
          setSource("spotify");
        }
        setCanRetrySpotifyHistory(false);
        setMessage(`${result.message} Imported ${result.importedCount ?? 0} Spotify plays.`);
        void enrichGenresInBackground("spotify", true);
        completed = true;
      } catch (error) {
        if (!(error instanceof DOMException && error.name === "AbortError")) {
          setCanRetrySpotifyHistory(true);
          setMessage(error instanceof Error ? error.message : "Spotify history import failed. Retry with the same file.");
        }
      } finally {
        if (importAbortControllerRef.current === controller) importAbortControllerRef.current = null;
      }
    });
    if (!started) {
      setMessage("Another import or refresh is already running. Wait for it to finish before importing Spotify history.");
      return false;
    }
    return completed;
  };

  const retrySpotifyHistory = () => {
    const file = lastSpotifyHistoryFileRef.current;
    if (file) void importSpotifyHistory(file);
  };

  const enrichDurationsInBackground = async () => {
    durationAbortControllerRef.current?.abort();
    const controller = new AbortController();
    durationAbortControllerRef.current = controller;
    try {
      const result = await pollChainedJob(
        () => api.startDurationEnrichment(),
        (signal) => api.durationEnrichmentStatus(signal),
        {
          signal: controller.signal,
          intervalMs: 1500,
          timeoutMs: 30 * 60 * 1000,
          networkFailureLimit: 12,
          batchDelayMs: 6000,
          onStatus: (status) => setMessage(`${status.message} (${status.progress}%)`),
          onBatchComplete: async (status) => {
            await loadAnalysis("youtube");
            if (status.continueQueued) {
              setMessage(`${status.message} Pausing briefly before the next saved batch.`);
            }
          },
        },
      );
      setMessage(result.message);
      void enrichGenresInBackground("youtube", true);
    } catch (error) {
      if (!(error instanceof DOMException && error.name === "AbortError")) {
        const failureMessage = error instanceof Error ? error.message : "Track duration enrichment could not finish. Your existing analysis is still available.";
        // Duration repair is optional background work. A transient hosted
        // gateway interruption must not leave a scary banner over analysis
        // that has already loaded successfully and remains fully usable.
        setMessage(/temporarily busy|stayed unavailable|saved progress will resume/i.test(failureMessage) ? null : failureMessage);
      }
    } finally {
      if (durationAbortControllerRef.current === controller) durationAbortControllerRef.current = null;
    }
  };

  const enrichGenresInBackground = async (activeSource: MusicSource, announce: boolean) => {
    genreAbortControllerRef.current?.abort();
    const controller = new AbortController();
    genreAbortControllerRef.current = controller;
    if (announce) setMessage("Checking the reusable genre and artwork catalogue...");
    try {
      const queued = await api.startGenreEnrichment(activeSource);
      const result = queued.status === "complete"
        ? queued
        : await pollTakeoutImport(
            (signal) => api.genreEnrichmentStatus(signal),
            {
              signal: controller.signal,
              intervalMs: 1200,
              timeoutMs: 5 * 60 * 1000,
              onStatus: announce ? (status) => setMessage(`${status.message} (${status.progress}%)`) : undefined,
            },
          );
      await loadAnalysis(activeSource);
      if (announce) {
        const coverage = result.afterCoverage == null ? "updated" : `${result.afterCoverage.toFixed(1)}%`;
        setMessage(`Metadata enrichment finished: ${result.matched ?? 0} artist and ${result.recordingMatched ?? 0} recording match(es), coverage ${coverage}.`);
      }
      return result;
    } catch (error) {
      if (!(error instanceof DOMException && error.name === "AbortError") && announce) {
        setMessage(error instanceof Error ? error.message : "Metadata enrichment could not finish. Your existing analysis is still available.");
      }
      return null;
    } finally {
      if (genreAbortControllerRef.current === controller) genreAbortControllerRef.current = null;
    }
  };

  const improveGenres = async () => {
    const started = await runExclusiveOperation(operationInFlightRef, setBusy, async () => {
      await enrichGenresInBackground(source, true);
    });
    if (!started) setMessage("Another data operation is already running. Wait for it to finish before enriching genres.");
  };

  const analysisReady = Boolean(overview);
  useEffect(() => {
    if (analysisReady && source === "youtube" && !useDemo) void enrichDurationsInBackground();
  }, [analysisReady, source, useDemo]);

  const createPlaylist = async () => {
    if (!runtime?.accountConnectionsEnabled) {
      setMessage("Playlist creation is unavailable in anonymous upload mode.");
      return;
    }
    if (source === "spotify") {
      setMessage("Playlist creation currently uses YouTube Music recommendations. Switch back to YouTube Music first.");
      return;
    }
    const confirmed = window.confirm('Create a private YouTube Music playlist named "Saville Recommendations"?');
    if (!confirmed) return;
    setBusy(true);
    try {
      const result = await api.createPlaylist("Saville Recommendations");
      setMessage(`${result.message} Playlist ID: ${result.playlist_id}`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Playlist creation failed.");
    } finally {
      setBusy(false);
    }
  };

  const connectSpotify = () => {
    if (!runtime?.accountConnectionsEnabled) {
      setMessage("Upload your Spotify export instead; anonymous sessions do not connect accounts.");
      navigate("settings");
      return;
    }
    window.location.href = api.spotifyLoginUrl();
  };

  const refreshSpotify = async () => {
    setBusy(true);
    setMessage("Refreshing local Spotify data...");
    try {
      const response = await api.spotifyRefresh();
      await loadStatus();
      if (source === "spotify") {
        await loadAnalysis("spotify");
      }
      setMessage(`Refreshed Spotify with ${response.track_count} tracks and ${response.play_count} local signals.`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Spotify refresh failed.");
    } finally {
      setBusy(false);
    }
  };

  const disconnectSpotify = async () => {
    setBusy(true);
    try {
      const result = await api.spotifyDisconnect();
      await loadStatus();
      if (source === "spotify") {
        setSource("youtube");
      }
      setMessage(result.message);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Spotify disconnect failed.");
    } finally {
      setBusy(false);
    }
  };

  const deleteAnonymousSession = async () => {
    if (!runtime?.anonymous) return;
    const confirmed = window.confirm("Permanently delete this session's uploaded listening data and reports?");
    if (!confirmed) return;
    setBusy(true);
    setMessage("Deleting this private session...");
    try {
      await api.deleteSession();
      window.location.reload();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "This session could not be deleted.");
      setBusy(false);
    }
  };

  const activePage = useMemo(() => {
    const titleAnimationKey = `${page}:${titleVisitId}`;
    switch (page) {
      case "overview":
        return (
          <OverviewPage
            overview={overview}
            auth={auth}
            prerequisites={prerequisites}
            busy={busy}
            useDemo={useDemo}
            anonymousMode={Boolean(runtime?.anonymous)}
            onRefresh={refresh}
            onOpenSettings={() => navigate("settings")}
            onOpenReport={() => navigate("report")}
            source={source}
            titleAnimationKey={titleAnimationKey}
          />
        );
      case "top10":
        return <Top10Page source={source} titleAnimationKey={titleAnimationKey} />;
      case "insights":
        return <InsightsPage source={source} titleAnimationKey={titleAnimationKey} onOpenTop10={() => navigate("top10")} />;
      case "report":
        return <ReportPage report={report} busy={busy} onGenerate={generateReport} source={source} titleAnimationKey={titleAnimationKey} />;
      case "recommendations":
        return <RecommendationsPage recommendations={recommendations} busy={busy} onGenerate={generateRecommendations} onCreatePlaylist={createPlaylist} canCreatePlaylist={Boolean(runtime?.accountConnectionsEnabled)} source={source} titleAnimationKey={titleAnimationKey} />;
      case "settings":
        return (
          <SettingsPage
            auth={auth}
            prerequisites={prerequisites}
            runtime={runtime}
            useDemo={useDemo}
            busy={busy}
            onUseDemoChange={setUseDemo}
            onCheckAuth={async () => {
              try {
                const liveAuth = await api.authStatus(true);
                setAuth(liveAuth);
                setMessage(liveAuth.message);
              } catch (error) {
                setMessage(error instanceof Error ? error.message : "YouTube live auth check failed.");
              }
            }}
            onImportTakeout={importTakeout}
            onImportSpotifyHistory={importSpotifyHistory}
            message={message}
            canRetryTakeout={canRetryTakeout}
            onRetryTakeout={retryTakeout}
            canRetrySpotifyHistory={canRetrySpotifyHistory}
            onRetrySpotifyHistory={retrySpotifyHistory}
            onViewOverview={() => navigate("overview")}
            onDeleteSession={deleteAnonymousSession}
            spotifyStatus={spotifyStatus}
            onConnectSpotify={connectSpotify}
            onRefreshSpotify={refreshSpotify}
            onDisconnectSpotify={disconnectSpotify}
            onImproveGenres={improveGenres}
            titleAnimationKey={titleAnimationKey}
          />
        );
    }
  }, [page, titleVisitId, overview, auth, spotifyStatus, prerequisites, runtime, busy, useDemo, tracks, artists, report, recommendations, source, message, canRetryTakeout, canRetrySpotifyHistory]);

  const youtubeAnalysisReady = overview?.source === "youtube";
  const youtubeReady = Boolean(auth?.connected || auth?.cached_data_available || youtubeAnalysisReady || (useDemo && overview));
  const youtubeLabel = useDemo
    ? youtubeAnalysisReady ? "Demo data" : "Demo data loading"
    : auth?.connected
      ? "YouTube connected"
      : auth?.cached_data_available
        ? "YouTube data loaded"
        : "YouTube offline";
  const currentNav = NAVIGATION_ITEMS.find((item) => item.id === page) ?? NAVIGATION_ITEMS[0];

  if (!sessionReady) {
    return (
      <div className="grid min-h-screen place-items-center bg-ink px-5 text-white">
        <GlowPanel as="section" variant="card" className="max-w-lg p-7 text-center">
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-red-200">Saville Music</p>
          <h1 className="mt-3 text-3xl font-black">{sessionError ? "Session could not start" : "Starting your private session"}</h1>
          <p className="mt-3 text-sm leading-6 text-mist">{sessionError || "Preparing an isolated space before any listening data is requested."}</p>
          {sessionError ? <button className="btn-primary mt-5" type="button" onClick={() => setSessionAttempt((value) => value + 1)}>Try again</button> : null}
        </GlowPanel>
      </div>
    );
  }

  return (
    <div
      className="relative isolate min-h-screen overflow-x-clip bg-ink text-white"
      style={{ "--app-sidebar-width": sidebarCollapsed ? "5.5rem" : "15rem" } as CSSProperties}
    >
      <div className="pointer-events-none fixed inset-0 z-0 overflow-hidden" aria-hidden="true">
        <Suspense fallback={<div className="h-full w-full bg-[#050304]" />}>
          <ColorBends
            className="smp-color-bends"
            colors={["#120205", "#2b050a", "#540b13", "#851522", "#d13842"]}
            rotation={108}
            autoRotate={0.45}
            speed={0.075}
            scale={0.9}
            frequency={0.82}
            warpStrength={0.72}
            mouseInfluence={0}
            parallax={0}
            noise={0.025}
            iterations={2}
            intensity={0.66}
            bandWidth={4.8}
            transparent={false}
          />
        </Suspense>
      </div>
      <DesktopSidebar activePage={page} collapsed={sidebarCollapsed} youtubeReady={youtubeReady} youtubeLabel={youtubeLabel} spotifyConnected={Boolean(spotifyStatus?.connected || spotifyStatus?.cached_data_available)} modelInstalled={runtime?.anonymous ? true : Boolean(prerequisites?.ollama_reachable && prerequisites.model_installed)} writerLabel={runtime?.anonymous ? "Report writer ready" : undefined} onToggle={() => setSidebarCollapsed((value) => !value)} onNavigate={navigate} />

      {mobileOpen ? (
        <div className="fixed inset-0 z-40 lg:hidden">
          <button className="absolute inset-0 bg-black/72 backdrop-blur-sm" type="button" aria-label="Close navigation overlay" onClick={() => setMobileOpen(false)} />
          <aside className="relative flex h-full w-[min(20rem,calc(100vw-2rem))] flex-col border-r border-line bg-backgroundElevated p-5 shadow-[24px_0_80px_rgba(0,0,0,0.5)]">
            <div className="flex items-center justify-between gap-3">
              <div className="flex items-center gap-3">
                <div className="grid h-10 w-10 place-items-center rounded-lg border border-red-400/25 bg-red-600/[0.18] text-red-100">
                  <Music2 size={20} />
                </div>
                <div>
                  <p className="font-bold">Saville Music</p>
                  <p className="text-xs text-mist">Navigation</p>
                </div>
              </div>
              <button className="grid h-10 w-10 place-items-center rounded-md border border-line bg-white/[0.055] text-mist hover:text-white" type="button" aria-label="Close navigation" onClick={() => setMobileOpen(false)}>
                <X size={18} />
              </button>
            </div>
            <nav className="mt-8 min-h-0 flex-1 space-y-2 overflow-y-auto" aria-label="Mobile navigation">
              {NAVIGATION_ITEMS.map((item) => {
                const Icon = item.icon;
                return (
                  <button key={item.id} className={`nav-item ${page === item.id ? "nav-item-active" : ""}`} onClick={() => navigate(item.id)} aria-current={page === item.id ? "page" : undefined}>
                    <Icon size={18} />
                    {item.label}
                  </button>
                );
              })}
            </nav>
          </aside>
        </div>
      ) : null}

      <div className="relative z-10 min-w-0 transition-[padding] duration-300 lg:pl-[var(--app-sidebar-width)]">
        <header className="sticky top-0 z-20 border-b border-line bg-backgroundElevated/90 px-4 py-3 backdrop-blur-xl lg:hidden">
          <div className="flex items-center justify-between gap-3">
            <button className="grid h-10 w-10 shrink-0 place-items-center rounded-md border border-line bg-white/[0.055] text-white" type="button" aria-label="Open navigation" onClick={() => setMobileOpen(true)}>
              <Menu size={19} />
            </button>
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-bold">Saville</p>
              <p className="truncate text-xs text-mist">{currentNav.label}</p>
            </div>
            <select className="max-w-[11rem] rounded-md border border-line bg-panel px-3 py-2 text-sm text-white" value={page} onChange={(event) => navigate(event.target.value as Page)} aria-label="Go to page">
              {NAVIGATION_ITEMS.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
            </select>
          </div>
        </header>
        <main className="mx-auto max-w-7xl px-4 py-6 md:px-8 md:py-10">
          {page !== "report" ? <SourceSwitcher source={source} spotifyStatus={spotifyStatus} accountConnectionsEnabled={Boolean(runtime?.accountConnectionsEnabled)} onChange={setSource} onConnectSpotify={connectSpotify} /> : null}
          {message && page !== "report" ? (
            <GlowPanel as="div" variant="row" wrapperClassName="mb-5" className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 text-sm text-mist">
              <span>{message}</span>
              {canRetryTakeout && lastTakeoutFileRef.current ? (
                <button type="button" className="btn-secondary" disabled={busy} onClick={retryTakeout}>Retry</button>
              ) : null}
            </GlowPanel>
          ) : null}
          {activePage}
        </main>
      </div>
    </div>
  );
}

function reportGenerationMessage(source: PersonaReport["generation"]["source"], fallbackReason: string | null, anonymous = false) {
  if (anonymous) {
    if (source === "hosted-llm" || source === "cache-hosted-llm") return "Persona report generated by the privacy-bounded hosted writer.";
    return fallbackReason?.includes("budget")
      ? "The hosted writing allowance is busy or exhausted; the private deterministic writer completed your report."
      : "Persona report generated with the private deterministic writer.";
  }
  if (source !== "fallback") return "Persona report regenerated locally with Gemma.";
  if (fallbackReason === "ollama_timeout") return "Gemma is available but did not finish in time; the report uses the local fallback.";
  if (fallbackReason === "model_not_installed") return "Gemma is not installed; the report uses the local fallback.";
  if (fallbackReason === "ollama_unavailable") return "Ollama is unavailable; the report uses the local fallback.";
  return "Gemma could not produce a valid report this time; the local fallback was used.";
}

function SourceSwitcher({
  source,
  spotifyStatus,
  accountConnectionsEnabled,
  onChange,
  onConnectSpotify,
}: {
  source: MusicSource;
  spotifyStatus: SpotifyStatus | null;
  accountConnectionsEnabled: boolean;
  onChange: (source: MusicSource) => void;
  onConnectSpotify: () => void;
}) {
  const label = source === "spotify" ? "Spotify" : "YouTube Music";
  return (
    <GlowPanel as="section" variant="card" wrapperClassName="relative z-10 mb-5" className="p-3">
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div>
          <p className="text-xs uppercase tracking-[0.18em] text-mist/70">Music source</p>
          <p className="mt-1 text-sm font-semibold text-white">Currently analysing: {label}</p>
          {source === "spotify" ? (
            <p className="mt-1 max-w-3xl text-xs leading-5 text-mist">
              {spotifyStatus?.historical_data_available
                ? `Using ${spotifyStatus.historical_play_count.toLocaleString()} imported Spotify plays${spotifyStatus.connected ? " plus connected catalogue metadata" : ""}.`
                : accountConnectionsEnabled ? "Connect Spotify for catalogue signals, or upload an export for dated historical play counts." : "Upload a Spotify export for dated historical play counts."}
            </p>
          ) : null}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button className={`rounded-md px-3 py-2 text-sm font-semibold ${source === "youtube" ? "bg-red-600 text-white" : "bg-white/10 text-mist hover:text-white"}`} onClick={() => onChange("youtube")}>
            YouTube Music
          </button>
          <button className={`rounded-md px-3 py-2 text-sm font-semibold ${source === "spotify" ? "bg-red-600 text-white" : "bg-white/10 text-mist hover:text-white"}`} onClick={() => onChange("spotify")}>
            Spotify
          </button>
          {accountConnectionsEnabled && source === "spotify" && !spotifyStatus?.connected && !spotifyStatus?.cached_data_available ? (
            <button className="btn-secondary" onClick={onConnectSpotify}>Connect Spotify</button>
          ) : null}
        </div>
      </div>
    </GlowPanel>
  );
}
