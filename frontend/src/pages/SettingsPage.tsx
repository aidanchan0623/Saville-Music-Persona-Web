import { ExternalLink, RefreshCw, ShieldCheck } from "lucide-react";
import { useState } from "react";
import type { ReactNode } from "react";
import { MusicSourceModal } from "../components/settings/MusicSourceModal";
import { GlowPanel } from "../components/GlowPanel";
import { PageTitlePanel } from "../components/PageTitlePanel";
import { StatusPill } from "../components/StatusPill";
import type { AuthStatus, Prerequisites, SessionStatus, SpotifyStatus } from "../types/api";

interface Props {
  auth: AuthStatus | null;
  prerequisites: Prerequisites | null;
  runtime: SessionStatus | null;
  useDemo: boolean;
  busy: boolean;
  onUseDemoChange: (value: boolean) => void;
  onCheckAuth: () => void;
  onImportTakeout: (file: File) => Promise<boolean>;
  onImportSpotifyHistory: (file: File) => Promise<boolean>;
  spotifyStatus: SpotifyStatus | null;
  onConnectSpotify: () => void;
  onRefreshSpotify: () => void;
  onDisconnectSpotify: () => void;
  onImproveGenres: () => void;
  message: string | null;
  canRetryTakeout: boolean;
  onRetryTakeout: () => void;
  canRetrySpotifyHistory: boolean;
  onRetrySpotifyHistory: () => void;
  onViewOverview: () => void;
  onDeleteSession: () => void;
  titleAnimationKey: string;
}

export function SettingsPage({
  auth,
  prerequisites,
  runtime,
  useDemo,
  busy,
  onUseDemoChange,
  onCheckAuth,
  onImportTakeout,
  onImportSpotifyHistory,
  spotifyStatus,
  onConnectSpotify,
  onRefreshSpotify,
  onDisconnectSpotify,
  onImproveGenres,
  message,
  canRetryTakeout,
  onRetryTakeout,
  canRetrySpotifyHistory,
  onRetrySpotifyHistory,
  onViewOverview,
  onDeleteSession,
  titleAnimationKey,
}: Props) {
  const [sourceModalOpen, setSourceModalOpen] = useState(false);
  const spotifyReady = Boolean(spotifyStatus?.connected || spotifyStatus?.cached_data_available);
  if (runtime?.anonymous) {
    const expiresLabel = runtime.expiresAt ? new Date(runtime.expiresAt).toLocaleString() : "after the session expires";
    return (
      <div className="space-y-6">
        <PageTitlePanel
          eyebrow="Private upload session"
          title="Import without an account"
          titleAnimationKey={titleAnimationKey}
          titleClassName="text-3xl font-black text-white md:text-4xl"
          subtitle="No YouTube or Spotify login is connected. Upload an export and Saville keeps its analysis inside this browser session."
          metadata={
            <div className="grid w-full gap-3 md:grid-cols-3">
              <StatusSummary label="Session" value={`Active · ${runtime.sessionHint ?? "private"}`} ok />
              <StatusSummary label="YouTube Music" value={auth?.cached_data_available ? "Takeout imported" : "Ready to import"} ok={Boolean(auth?.cached_data_available)} />
              <StatusSummary label="Spotify" value={spotifyStatus?.historical_data_available ? `${spotifyStatus.historical_play_count.toLocaleString()} plays imported` : "Ready to import"} ok={Boolean(spotifyStatus?.historical_data_available)} />
            </div>
          }
        />

        <SettingsCard>
          <div className="flex flex-col justify-between gap-4 md:flex-row md:items-center">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.18em] text-red-200">Anonymous imports</p>
              <h2 className="mt-2 text-2xl font-black text-white">Choose your listening export</h2>
              <p className="mt-2 max-w-2xl text-sm leading-6 text-mist">Google Takeout and Spotify extended streaming history are supported. Account connection controls are disabled in hosted mode.</p>
            </div>
            <button className="btn-primary" type="button" onClick={() => setSourceModalOpen(true)}>Upload Listening Data</button>
          </div>
          <div className="mt-5 grid gap-3 md:grid-cols-2">
            <Info label="Session reference" value={runtime.sessionHint ?? "Private"} />
            <Info label="Current expiry" value={expiresLabel} />
          </div>
          {message ? <div className="mt-4 flex flex-wrap items-center gap-3 rounded border border-white/10 bg-black/20 p-3 text-sm text-mist" role="status"><span>{message}</span>{canRetryTakeout ? <button className="btn-secondary" type="button" onClick={onRetryTakeout}>Retry Takeout</button> : null}{canRetrySpotifyHistory ? <button className="btn-secondary" type="button" onClick={onRetrySpotifyHistory}>Retry Spotify</button> : null}</div> : null}
          {(auth?.cached_data_available || spotifyStatus?.historical_data_available) ? <button className="btn-secondary mt-4" type="button" onClick={onViewOverview}>View Overview</button> : null}
        </SettingsCard>

        <SettingsCard>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-red-200">Analysis</p>
          <h2 className="mt-2 text-2xl font-black text-white">Metadata coverage</h2>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-mist">Saville can improve unresolved genres using its shared public music metadata cache without exposing another listener's history.</p>
          <button className="btn-secondary mt-5" type="button" disabled={busy || !auth?.cached_data_available} onClick={onImproveGenres}><RefreshCw size={16} /> Improve genre coverage</button>
        </SettingsCard>

        <SettingsCard>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-red-200">Privacy control</p>
          <h2 className="mt-2 text-2xl font-black text-white">Delete this session</h2>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-mist">
            Permanently removes this browser session's uploads, analysis, reports, and derived listening-event index now. Shared public music metadata is retained because it contains no listening history.
          </p>
          <button className="btn-secondary mt-5 border-red-500/40 text-red-100" type="button" disabled={busy} onClick={onDeleteSession}>
            Delete my uploaded data
          </button>
        </SettingsCard>

        <MusicSourceModal open={sourceModalOpen} onClose={() => setSourceModalOpen(false)} onConnectSpotify={onConnectSpotify} onImportTakeout={onImportTakeout} onImportSpotifyHistory={onImportSpotifyHistory} busy={busy} message={message} spotifyConfigured={false} accountConnectionsEnabled={false} />
      </div>
    );
  }
  return (
    <div className="space-y-6">
      <PageTitlePanel
        eyebrow="Settings"
        title="Local integrations and data controls"
        titleAnimationKey={titleAnimationKey}
        titleClassName="text-3xl font-black text-white md:text-4xl"
        subtitle="Connection status, demo mode, private auth guidance, and import tools for the local music profile."
        metadata={
          <div className="grid w-full gap-3 md:grid-cols-3">
          <StatusSummary label="YouTube Music" value={auth?.connected ? "Connected" : auth?.cached_data_available ? "Cached data" : "Offline"} ok={Boolean(auth?.connected || auth?.cached_data_available)} />
          <StatusSummary label="Spotify" value={spotifyStatus?.historical_data_available ? "History imported" : spotifyStatus?.connected ? "Connected" : spotifyStatus?.configured ? "Ready to connect" : "Ready to import"} ok={spotifyReady} />
          <StatusSummary label="Gemma" value={prerequisites?.model_installed ? "Ready" : "Offline"} ok={Boolean(prerequisites?.model_installed)} />
          </div>
        }
      />

      <SettingsCard>
        <div className="flex flex-col justify-between gap-4 md:flex-row md:items-center">
          <div><p className="text-xs font-semibold uppercase tracking-[0.18em] text-red-200">Music data sources</p><h2 className="mt-2 text-2xl font-black text-white">Music Data Sources</h2><p className="mt-2 max-w-2xl text-sm leading-6 text-mist">Connect a streaming service or import your listening history.</p></div>
          <button className="btn-primary" type="button" onClick={() => setSourceModalOpen(true)}>Add or Change Music Source</button>
        </div>
        <div className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <StatusSummary label="YouTube Music" value={auth?.connected ? "Connected" : auth?.cached_data_available ? "Cached data available" : "Ready to connect"} ok={Boolean(auth?.connected || auth?.cached_data_available)} />
          <StatusSummary label="Spotify" value={spotifyStatus?.historical_data_available ? `${spotifyStatus.historical_play_count.toLocaleString()} plays imported` : spotifyStatus?.connected ? "Connected" : "Not imported"} ok={spotifyReady} />
          <StatusSummary label="Google Takeout" value={busy && message?.toLowerCase().includes("takeout") ? "Importing" : auth?.cached_data_available ? "Import complete" : "Not imported"} ok={Boolean(auth?.cached_data_available)} />
          <StatusSummary label="Demo data" value={useDemo ? "Demo mode active" : "Inactive"} ok={useDemo} />
        </div>
        {message?.toLowerCase().includes("takeout") ? <div className="mt-4 flex flex-wrap items-center gap-3 rounded border border-white/10 bg-black/20 p-3 text-sm text-mist" role="status"><span>{message}</span>{canRetryTakeout ? <button className="btn-secondary" type="button" onClick={onRetryTakeout}>Retry import</button> : null}</div> : null}
        {message?.toLowerCase().includes("spotify") ? <div className="mt-4 flex flex-wrap items-center gap-3 rounded border border-white/10 bg-black/20 p-3 text-sm text-mist" role="status"><span>{message}</span>{canRetrySpotifyHistory ? <button className="btn-secondary" type="button" onClick={onRetrySpotifyHistory}>Retry Spotify import</button> : null}</div> : null}
        {auth?.cached_data_available && message?.includes("Imported") ? <button className="btn-secondary mt-4" type="button" onClick={onViewOverview}>View Updated Overview</button> : null}
      </SettingsCard>

      <SettingsCard>
        <div className="flex flex-col justify-between gap-4 md:flex-row md:items-start">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-red-200">Primary source</p>
            <h2 className="mt-2 text-2xl font-black text-white">Connect YouTube Music</h2>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-mist">
              Preferred setup uses ytmusicapi OAuth. Credentials stay in the backend's ignored private config folder.
            </p>
          </div>
          <StatusPill ok={auth?.connected || auth?.cached_data_available} label={auth?.connected ? "Connected" : auth?.cached_data_available ? "Cached data" : "Not connected"} />
        </div>
        <div className="mt-5 grid gap-3 md:grid-cols-2">
          <Info label="Auth storage" value={auth?.auth_file_exists ? "Configured locally" : "Not created yet"} />
          <Info label="OAuth client configured" value={auth?.oauth_client_configured ? "Yes" : "No"} />
          <Info label="Account" value={auth?.account_name || "Unavailable"} />
          <Info label="Cached YouTube profile" value={auth?.cached_data_available ? `Available${auth.last_refreshed_at ? `, refreshed ${auth.last_refreshed_at}` : ""}` : "Unavailable"} />
          <Info label="Status" value={sanitizePrivateDetails(auth?.message || "Not checked yet")} />
        </div>
        <div className="mt-5 flex flex-wrap gap-3">
          <button className="btn-secondary" onClick={onCheckAuth}>Recheck Connection</button>
          <a className="btn-secondary" href="/docs/AUTH_SETUP.md" onClick={(event) => event.preventDefault()}>
            <ExternalLink size={17} /> See docs/AUTH_SETUP.md in the repo
          </a>
        </div>
        <p className="mt-5 border-t border-amber-300/20 pt-4 text-sm leading-6 text-amber-100">
          Browser-header authentication is deliberately not automated. If you use it as an advanced fallback, treat the header file like account-access data and keep it out of Git.
        </p>
      </SettingsCard>

      <SettingsCard>
        <div className="flex flex-col justify-between gap-4 md:flex-row md:items-start">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-red-200">Optional source</p>
            <h2 className="mt-2 text-2xl font-black text-white">Connect Spotify</h2>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-mist">
              Spotify is optional. It stays separate from YouTube Music and uses top artists, top tracks, saved songs, playlists, and recent plays.
            </p>
          </div>
          <StatusPill ok={spotifyReady} label={spotifyStatus?.historical_data_available ? "History imported" : spotifyStatus?.connected ? "Connected" : "Optional"} />
        </div>
        <div className="mt-5 grid gap-3 md:grid-cols-2">
          <div className="border-t border-white/10 pt-4">
            <p className="text-xs uppercase tracking-[0.16em] text-mist/60">Account</p>
            <div className="mt-3 flex items-center gap-3">
              {spotifyStatus?.profile_image ? (
                <img className="h-11 w-11 rounded-full object-cover" src={spotifyStatus.profile_image} alt={spotifyStatus.display_name ?? "Spotify profile"} />
              ) : (
                <span className="grid h-11 w-11 place-items-center rounded-full bg-white/10 text-sm font-bold text-white">SP</span>
              )}
              <p className="text-sm text-white">{spotifyStatus?.display_name || "Not connected"}</p>
            </div>
          </div>
          <Info label="Spotify configured" value={spotifyStatus?.configured ? "Yes" : "No"} />
          <Info label="Last Spotify sync" value={spotifyStatus?.last_synced_at || "Never"} />
          <Info label="Imported history" value={spotifyStatus?.historical_data_available ? `${spotifyStatus.historical_play_count.toLocaleString()} plays` : "Not imported"} />
          <Info label="Status" value={sanitizePrivateDetails(spotifyStatus?.message || "Not checked yet")} />
        </div>
        <div className="mt-5 flex flex-wrap gap-3">
          {!spotifyStatus?.connected ? (
            <button className="btn-primary" disabled={busy || !spotifyStatus?.configured} onClick={onConnectSpotify}>
              Connect Spotify
            </button>
          ) : (
            <>
              <button className="btn-secondary" disabled={busy} onClick={onRefreshSpotify}>
                <RefreshCw size={16} /> Refresh Spotify Data
              </button>
              <button className="btn-secondary" disabled={busy} onClick={onDisconnectSpotify}>
                Disconnect Spotify
              </button>
            </>
          )}
          <label className="btn-secondary inline-flex cursor-pointer">
            Import Spotify History
            <input className="sr-only" disabled={busy} type="file" accept=".json,.zip,application/json,application/zip" onChange={(event) => { const file = event.target.files?.[0]; if (file) void onImportSpotifyHistory(file); event.currentTarget.value = ""; }} />
          </label>
        </div>
        <p className="mt-5 border-t border-white/10 pt-4 text-sm leading-6 text-mist">
          Upload Spotify's extended streaming-history ZIP for dated play counts and actual milliseconds played. OAuth remains optional and can add catalogue images, albums, release dates, and artist genres.
        </p>
      </SettingsCard>

      <SettingsCard>
        <div className="flex items-center justify-between gap-4">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-red-200">Preview mode</p>
            <h2 className="mt-2 text-2xl font-black text-white">Use demo data</h2>
            <p className="mt-2 text-sm text-mist">Explore the whole dashboard with anonymised mock listening history.</p>
          </div>
          <label className="relative inline-flex cursor-pointer items-center">
            <input className="peer sr-only" type="checkbox" checked={useDemo} onChange={(event) => onUseDemoChange(event.target.checked)} />
            <span className="h-7 w-12 rounded-full bg-white/10 transition peer-checked:bg-red-600" />
            <span className="absolute left-1 h-5 w-5 rounded-full bg-white transition peer-checked:translate-x-5" />
          </label>
        </div>
      </SettingsCard>

      <SettingsCard>
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-red-200">Analysis rules</p>
        <h2 className="mt-2 text-2xl font-black text-white">Analytics timezone and metadata enrichment</h2>
        <p className="mt-2 max-w-3xl text-sm leading-6 text-mist">
          Calendar months, daily charts and streaks use the backend local timezone. Change <code>SMP_LOCAL_TIMEZONE</code> in the backend environment to adjust it.
        </p>
        <div className="mt-5 grid gap-3 md:grid-cols-2">
          <Info label="Analytics timezone" value={prerequisites?.local_timezone || "Asia/Kuala_Lumpur"} />
          <Info label="Duration enrichment limit" value={`${prerequisites?.duration_enrichment_limit ?? 150} missing tracks per refresh/import`} />
        </div>
        <div className="mt-5 border-t border-white/10 pt-4">
          <button className="btn-secondary" type="button" disabled={busy || !auth?.cached_data_available} onClick={onImproveGenres}>
            <RefreshCw size={16} /> Improve genre coverage
          </button>
          <p className="mt-3 max-w-3xl text-sm leading-6 text-mist">
            Checks high-impact unclassified artists against MusicBrainz in the background. Only unique exact matches with supported genres are accepted; unresolved listening stays visible as unclassified.
          </p>
        </div>
      </SettingsCard>

      <SettingsCard>
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-red-200">Long history import</p>
        <h2 className="mt-2 text-2xl font-black text-white">Import Google Takeout history</h2>
        <p className="mt-2 max-w-3xl text-sm leading-6 text-mist">
          YouTube Music only exposed a short recent web history feed. Upload a Google Takeout YouTube watch-history JSON, HTML, or ZIP file to rebuild analysis with the longest account history Google provides.
        </p>
        <label className="mt-5 inline-flex cursor-pointer items-center gap-2 rounded-md border border-white/10 bg-white/[0.06] px-4 py-2.5 text-sm font-semibold text-white transition hover:border-red-500/40 hover:bg-white/[0.09]">
          Choose Takeout file
          <input
            className="sr-only"
            disabled={busy}
            type="file"
            accept=".json,.zip,.html,.htm,application/json,application/zip,text/html"
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) onImportTakeout(file);
              event.currentTarget.value = "";
            }}
          />
        </label>
      </SettingsCard>

      <SettingsCard>
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-red-200">Local runtime</p>
        <h2 className="mt-2 flex items-center gap-2 text-2xl font-black text-white">
          <ShieldCheck size={20} /> Local prerequisites
        </h2>
        <div className="mt-4 grid gap-3">
          {prerequisites?.items.map((item) => (
            <div key={item.name} className="flex flex-col justify-between gap-2 border-t border-white/10 py-3 sm:flex-row sm:items-center">
              <StatusPill ok={item.available} label={item.name} />
              <p className="text-sm text-mist">{sanitizePrivateDetails(item.detail)}</p>
            </div>
          ))}
          <p className="border-t border-white/10 py-3 text-sm text-mist">
            Ollama model: <span className="text-white">{prerequisites?.ollama_model || "gemma3:4b"}</span>. Model installed:{" "}
            <span className="text-white">{prerequisites?.model_installed ? "Yes" : "No"}</span>.
          </p>
        </div>
      </SettingsCard>

      <MusicSourceModal open={sourceModalOpen} onClose={() => setSourceModalOpen(false)} onConnectSpotify={onConnectSpotify} onImportTakeout={onImportTakeout} onImportSpotifyHistory={onImportSpotifyHistory} busy={busy} message={message} spotifyConfigured={Boolean(spotifyStatus?.configured)} accountConnectionsEnabled />
    </div>
  );
}

function SettingsCard({ children }: { children: ReactNode }) {
  return (
    <GlowPanel as="section" variant="card" className="p-5">
      {children}
    </GlowPanel>
  );
}

function StatusSummary({ label, value, ok }: { label: string; value: string; ok: boolean }) {
  return (
    <div className="border-t border-white/10 pt-3">
      <p className="text-xs font-semibold uppercase tracking-[0.16em] text-mist/60">{label}</p>
      <p className={`mt-2 text-sm font-semibold ${ok ? "text-red-100" : "text-mist"}`}>{value}</p>
    </div>
  );
}

function Info({ label, value }: { label: string; value: string }) {
  return (
    <div className="border-t border-white/10 pt-3">
      <p className="text-xs uppercase tracking-[0.16em] text-mist/60">{label}</p>
      <p className="mt-2 break-words text-sm text-white">{value}</p>
    </div>
  );
}

function sanitizePrivateDetails(value: string) {
  return value
    .replace(/[A-Za-z]:\\[^\s]+/g, "[local private path]")
    .replace(/backend[\\/]+private[\\/]+\.env/g, "local private settings")
    .replace(/backend[\\/]+private[\\/]?/g, "local private storage");
}
