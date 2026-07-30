import { Menu, Music2 } from "lucide-react";
import { useEffect, useState } from "react";
import { StatusPill } from "../StatusPill";
import { NAVIGATION_ITEMS } from "./navigation";
import type { Page } from "./navigation";

interface DesktopSidebarProps {
  activePage: Page;
  collapsed: boolean;
  youtubeReady: boolean;
  youtubeLabel: string;
  spotifyConnected?: boolean;
  modelInstalled?: boolean;
  writerLabel?: string;
  onToggle: () => void;
  onNavigate: (page: Page) => void;
}

export function DesktopSidebar(props: DesktopSidebarProps) {
  const desktopMounted = useDesktopSidebarMounted();

  if (!desktopMounted) return null;

  return <ReactBitsDesktopSidebar {...props} />;
}

function ReactBitsDesktopSidebar({ activePage, collapsed, youtubeReady, youtubeLabel, spotifyConnected, modelInstalled, writerLabel, onToggle, onNavigate }: DesktopSidebarProps) {
  return (
    <aside className={`fixed inset-y-0 left-0 z-30 hidden flex-col border-r border-line bg-backgroundElevated/95 shadow-[18px_0_70px_rgba(0,0,0,0.28)] backdrop-blur-xl transition-[width,padding] duration-300 lg:flex ${collapsed ? "w-[5.5rem] px-2 py-4" : "w-60 p-5"}`}>
      <div className={`flex items-center ${collapsed ? "flex-col gap-3" : "gap-3"}`}>
        <button
          type="button"
          className="grid h-11 w-11 shrink-0 place-items-center rounded-full text-mist transition hover:bg-white/10 hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-red-400"
          aria-label={collapsed ? "Expand navigation" : "Collapse navigation"}
          aria-expanded={!collapsed}
          onClick={onToggle}
        >
          <Menu size={22} />
        </button>
        <SidebarBrand subtitle="Private taste analysis" collapsed={collapsed} />
      </div>

      <nav className={`mt-7 min-h-0 flex-1 overflow-y-auto ${collapsed ? "space-y-2" : "space-y-1"}`} aria-label="Primary navigation">
        {NAVIGATION_ITEMS.map((item) => {
          const Icon = item.icon;
          const active = item.id === activePage;
          return (
            <button
              key={item.id}
              type="button"
              className={`group flex w-full items-center rounded-xl transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-red-400 ${collapsed ? "min-h-[4.25rem] flex-col justify-center gap-1 px-1 py-2" : "min-h-12 gap-3 px-3 py-2.5"} ${active ? "bg-white/10 text-white" : "text-mist hover:bg-white/[0.07] hover:text-white"}`}
              aria-current={active ? "page" : undefined}
              title={collapsed ? item.label : undefined}
              onClick={() => onNavigate(item.id)}
            >
              <Icon size={collapsed ? 21 : 19} strokeWidth={active ? 2.35 : 1.9} />
              <span className={collapsed ? "max-w-full truncate text-[0.66rem] font-semibold leading-tight" : "truncate text-sm font-semibold"}>{collapsed ? item.compactLabel ?? item.label : item.label}</span>
            </button>
          );
        })}
      </nav>

      {!collapsed ? <LocalStatusPanel youtubeReady={youtubeReady} youtubeLabel={youtubeLabel} spotifyConnected={spotifyConnected} modelInstalled={modelInstalled} writerLabel={writerLabel} /> : null}
    </aside>
  );
}

function SidebarBrand({ subtitle, collapsed }: { subtitle: string; collapsed: boolean }) {
  return (
    <div className={`flex items-center ${collapsed ? "justify-center" : "min-w-0 gap-3"}`}>
      <div className="grid h-11 w-11 shrink-0 place-items-center rounded-lg border border-red-400/25 bg-red-600/[0.18] text-red-100">
        <Music2 size={22} />
      </div>
      {!collapsed ? <div className="min-w-0">
        <p className="font-bold leading-5">Saville Music</p>
        <p className="text-xs text-mist">{subtitle}</p>
      </div> : null}
    </div>
  );
}

function LocalStatusPanel({
  youtubeReady,
  youtubeLabel,
  spotifyConnected,
  modelInstalled,
  writerLabel,
}: {
  youtubeReady: boolean;
  youtubeLabel: string;
  spotifyConnected?: boolean;
  modelInstalled?: boolean;
  writerLabel?: string;
}) {
  return (
    <div className="mt-6 space-y-2 border-t border-line pt-4">
      <p className="text-xs font-semibold uppercase tracking-[0.16em] text-mist/70">Status</p>
      <StatusPill ok={youtubeReady} label={youtubeLabel} />
      <StatusPill ok={spotifyConnected} label={spotifyConnected ? "Spotify connected" : "Spotify optional"} />
      <StatusPill ok={Boolean(modelInstalled)} label={writerLabel ?? (modelInstalled ? "Gemma ready" : "Gemma offline")} />
    </div>
  );
}

function useDesktopSidebarMounted() {
  const [desktopMounted, setDesktopMounted] = useState(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return true;
    return window.matchMedia("(min-width: 1024px)").matches;
  });

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const media = window.matchMedia("(min-width: 1024px)");
    const update = () => setDesktopMounted(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);

  return desktopMounted;
}
