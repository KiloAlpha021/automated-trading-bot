import { useEffect, useState } from "react";
import type { PageDefinition } from "../data";
import { pages } from "../data";
import { Panel, toneFor } from "./Panel";
import { Dashboard } from "../pages/Dashboard";
import { Markets } from "../pages/Markets";
import { Strategies } from "../pages/Strategies";
import { Portfolio } from "../pages/Portfolio";
import { Orders } from "../pages/Orders";
import { PhaseBPage } from "../pages/PhaseBPages";
import "../pages/phase-b.css";

type ShellProps = { page: PageDefinition; navigate: (path: string) => void; fitToWindow?: boolean };

function SharedSidebar({ page, navigate }: Pick<ShellProps, "page" | "navigate">) {
  return <aside className="sidebar">
    <div className="brand"><img src="/assets/atis-logo.png" alt="ATIS" /></div>
    <nav aria-label="Operator Console pages">
      {pages.map((item, index) => <a key={item.slug} className={item.slug === page.slug ? "nav-item active" : "nav-item"} href={`/${item.slug}`} aria-current={item.slug === page.slug ? "page" : undefined} onClick={(event) => { event.preventDefault(); navigate(`/${item.slug}`); }}><em>{String(index + 1).padStart(2, "0")}</em><img src={`/assets/${item.icon}`} alt="" /><span>{item.label}</span></a>)}
    </nav>
    <div className="sidebar-motto">A MORE<br />DISCIPLINED<br />FINANCIAL FUTURE.</div>
    <div className="version"><i/> ATIS v1.0.0<br /><i/> Paper Trading Mode<br/><i className="red"/> No Live Trading Authority</div>
  </aside>;
}

function SharedHeader() {
  return <header className="topbar">
    <div className="top-identity"><b>AUTOMATED TRADING<br/>INTELLIGENCE SYSTEM</b><span>ANALYSE · EXECUTE · MONITOR</span></div>
    <div className="header-cards">
      <div><img src="/assets/environment.svg" alt="" /><span>OPERATING MODE<strong>PAPER TRADING</strong></span></div>
      <div className="authority-card"><img src="/assets/authority.svg" alt="" /><span>TRADING AUTHORITY<strong>PAPER ONLY</strong><small>Live trading requires owner authorisation</small></span></div>
    </div>
    <div className="header-tools"><span><b>Sat, 17 Oct 2026</b>16:07 BST</span><button type="button" aria-label="Notifications, three sample alerts"><img src="/assets/bell.svg" alt="" /><i>3</i></button><button type="button" aria-label="Settings display only"><img src="/assets/settings-header.svg" alt="" /></button><img className="avatar" src="/assets/avatar.svg" alt="Operator SK" /><span className="avatar-initials" aria-hidden="true">SK</span><span className="owner">SK<small>Owner</small></span><span className="chevron">›</span></div>
  </header>;
}

export function Shell({ page, navigate, fitToWindow = false }: ShellProps) {
  const phaseBSlugs = ["risk-gateway", "execution", "reconciliation", "insights", "system-health", "audit-log", "settings"];
  const usesUnifiedP01P03Shell = ["dashboard", "markets", "strategies"].includes(page.slug);
  const [scale, setScale] = useState(1);
  useEffect(() => {
    if (!fitToWindow) { setScale(1); return; }
    const updateScale = () => setScale(Math.min(window.innerWidth / 1536, window.innerHeight / 1024));
    updateScale();
    window.addEventListener("resize", updateScale);
    return () => window.removeEventListener("resize", updateScale);
  }, [fitToWindow]);

  return <div className={`console-viewport${fitToWindow ? " fit-to-window" : " native-fidelity"}`} style={fitToWindow ? { width: 1536 * scale, height: 1024 * scale } : undefined}>
    <main className={`console-frame ${page.slug}-frame${phaseBSlugs.includes(page.slug) ? " strategies-frame" : ""}${usesUnifiedP01P03Shell ? " unified-p01-p03-shell" : ""}`} style={fitToWindow ? { transform: `scale(${scale})` } : undefined}>
      {page.slug === "markets" ? <><Markets navigate={navigate} renderSharedChrome={false} /><SharedSidebar page={page} navigate={navigate} /><SharedHeader /></> : <>
      <SharedSidebar page={page} navigate={navigate} />
      <SharedHeader />
      {page.slug === "dashboard" ? <Dashboard /> : page.slug === "strategies" ? <Strategies /> : page.slug === "portfolio" ? <Portfolio /> : page.slug === "orders" ? <Orders /> : phaseBSlugs.includes(page.slug) ? <PhaseBPage slug={page.slug} /> : <section className={`workspace page-${page.slug}`} data-page={page.slug}>
        <div className="page-title"><div className="page-icon"><img src={`/assets/${page.icon}`} alt="" /></div><div><div className="title-row"><h1>{page.title}</h1><span>PAPER / REPLACEABLE DESIGN DATA</span></div><p>{page.subtitle}</p></div><div className="page-time">OBSERVED<br /><b>17 OCT 2026 · EUROPE/LONDON</b></div></div>
        <div className="summary-row">{page.summary.map((metric) => <article key={metric.label} className={`summary-card tone-${toneFor(metric.value, metric.tone)}`}><span>{metric.label}</span><strong>{metric.value}</strong><small>{metric.detail}</small></article>)}</div>
        <div className="panel-grid">{page.panels.map((panel) => <Panel key={panel.title} panel={panel} />)}</div>
      </section>}
      </>}
      <p className="sr-only">ENGINEERING SAMPLE · NO BROKER CONNECTIVITY · NO ORDER SUBMISSION · NO LIVE-CAPITAL AUTHORITY</p>
    </main>
  </div>;
}
