import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { PhaseBPage } from "./pages/PhaseBPages";

const routes = ["risk-gateway", "execution", "reconciliation", "insights", "system-health", "audit-log", "settings"];

const markup = (route: string) => renderToStaticMarkup(<PhaseBPage slug={route} />);
const escapeRegex = (value: string) => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const nodeHas = (html: string, id: string, text: string) => new RegExp(`data-figma-id="${escapeRegex(id)}"[^>]*><span>${escapeRegex(text)}<\\/span>`).test(html);

describe("Phase B Figma contract pages", () => {
  for (const route of routes) {
    it(`renders the authenticated ${route} contract`, () => {
      const html = markup(route);
      expect(html).toContain(`data-page="${route}"`);
      expect(html).toContain("data-figma-id");
      expect(html).toContain("PAPER");
    });
  }

  it("binds route-specific records, states and evidence identities", () => {
    const risk = markup("risk-gateway");
    expect(nodeHas(risk, "299:626", "RGE-P-00043")).toBe(true);
    expect(risk).toContain("47 evaluations");
    expect(nodeHas(risk, "299:617", "NOT CREATED")).toBe(true);
    expect(nodeHas(risk, "299:620", "NOT SENT")).toBe(true);

    const execution = markup("execution");
    expect(nodeHas(execution, "325:649", "OMS-P-0003")).toBe(true);
    expect(nodeHas(execution, "328:630", "FILL-P-0003-01")).toBe(true);
    expect(nodeHas(execution, "325:659", "PARTIALLY_FILLED")).toBe(true);

    const reconciliation = markup("reconciliation");
    expect(nodeHas(reconciliation, "339:607", "NOT ESTABLISHED")).toBe(true);
    expect(nodeHas(reconciliation, "337:526", "EXCEPTION")).toBe(true);

    const insights = markup("insights");
    expect(insights).toContain("BT-P-0001");
    expect(insights).toContain("P9-CHART-EXT-P-001");

    const audit = markup("audit-log");
    expect(audit).toContain("10 EVENTS · EUROPE/LONDON");
    expect(audit).toContain("INGESTION FRESHNESS");
    expect(audit).toContain("TRADING AUTHORITY IMPACT");
    expect(audit).toContain("NO CHANGE");

    const settings = markup("settings");
    expect(nodeHas(settings, "385:955", "AUTH-DEC-P-0012")).toBe(true);
    expect(nodeHas(settings, "385:978", "NOT AUTHORISED")).toBe(true);
    expect(settings).toContain("NONINTERACTIVE");
  });

  it("renders exactly ten audit event records with record-local identities", () => {
    const audit = markup("audit-log");
    const events = [
      ["382:864", "AUD-P-0010"], ["382:872", "AUD-P-0009"], ["382:880", "AUD-P-0008"],
      ["382:888", "AUD-P-0007"], ["382:896", "AUD-P-0006"], ["382:904", "AUD-P-0005"],
      ["382:912", "AUD-P-0004"], ["382:920", "AUD-P-0003"], ["382:928", "AUD-P-0002"],
      ["382:936", "AUD-P-0001"],
    ] as const;
    expect(events).toHaveLength(10);
    for (const [id, identity] of events) expect(nodeHas(audit, id, identity)).toBe(true);
    expect(nodeHas(audit, "382:853", "10 EVENTS · EUROPE/LONDON")).toBe(true);
    expect(new Set(audit.match(/AUD-P-00(?:10|0[1-9])/g))).toHaveLength(10);
  });

  it("renders contract text with its alignment and autosizing association", () => {
    const risk = markup("risk-gateway");
    expect(risk).toMatch(/data-figma-id="297:576"[^>]*justify-content:flex-end[^>]*><span>£4,200<\/span>/);
    const execution = markup("execution");
    expect(execution).toMatch(/data-figma-id="325:632"[^>]*white-space:pre[^>]*><span>OMS-P-0002<\/span>/);
    const audit = markup("audit-log");
    expect(audit).toMatch(/data-figma-id="382:853"[^>]*justify-content:flex-end[^>]*><span>10 EVENTS · EUROPE\/LONDON<\/span>/);
  });

  it("paints Page09 chart paths without CSS box borders", () => {
    const insights = markup("insights");
    for (const id of ["371:524", "371:525", "371:526"]) {
      const vector = insights.match(new RegExp(`<svg[^>]*data-figma-id="${id}"[^>]*>[\\s\\S]*?<\\/svg>`))?.[0];
      expect(vector).toBeDefined();
      expect(vector).toContain("<path");
      expect(vector).not.toMatch(/border:/);
    }
  });

  it("preserves parent-child clipping for overflowing Page09 controls", () => {
    const insights = markup("insights");
    expect(insights).toMatch(/data-figma-id="424:4007"[^>]*overflow:hidden[^>]*><div[^>]*data-figma-id="424:4008"[^>]*><span>TABLE · REPORT ↗<\/span><\/div><\/div>/);
  });

  it("rejects unrelated or fabricated identifiers as substitutes", () => {
    const combined = routes.map(markup).join("\n");
    expect(combined).not.toContain("EXE-P-0008");
    expect(combined).not.toContain("Portfolio record 9");
    expect(markup("risk-gateway")).not.toContain("OMS-P-0003");
    const risk = markup("risk-gateway").replace(/(data-figma-id="299:626"[^>]*><span>)RGE-P-00043/, "$1OMS-P-0003");
    expect(nodeHas(risk, "299:626", "RGE-P-00043")).toBe(false);
    const audit = markup("audit-log").replace(/(data-figma-id="382:888"[^>]*><span>)AUD-P-0007/, "$1AUD-P-0099");
    expect(nodeHas(audit, "382:888", "AUD-P-0007")).toBe(false);
  });
});
