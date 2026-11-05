import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { App } from "./App";
import { marketNodes, marketsProvenance, chartNodeId } from "./pages/markets.fixtures";

describe("Markets static design contract", () => {
  const html = renderToStaticMarkup(<App initialPath="/markets" />);
  it("keeps ten regions and twelve correctly labelled navigation destinations", () => {
    expect((html.match(/data-market-region=/g) ?? [])).toHaveLength(10);
    expect((html.match(/<a /g) ?? [])).toHaveLength(12);
    expect(html).toMatch(/href="\/markets" aria-current="page"/);
    for (const [path,label] of [["system-health","System Health"],["audit-log","Audit Log"],["settings","Settings"]]) {
      expect(html).toMatch(new RegExp(`href="/${path}"[^>]*>[\\s\\S]*?${label}</span></a>`));
    }
    expect(html).not.toContain('data-figma-node="403:529"');
    expect(html).not.toContain("PHASE B");
  });
  it("separates illustrative status from connectivity and trading authority", () => {
    expect(marketsProvenance.liveConnectivityVerified).toBe(false);
    expect(marketsProvenance.tradingAuthority).toBe("NONE");
    for (const text of ["PAPER ONLY", "No Live Trading Authority", "FIGMA DESIGN SAMPLES ONLY", "No live connectivity verified", "DISCONNECTED", "UNKNOWN", "MD-EV-20261017-000184", "16:05:32.123", "16:05:32.146"]) expect(html).toContain(text);
    const unknown = marketNodes.find(n => n.text === "UNKNOWN")!;
    expect(unknown.style.color).toBe("rgba(127,145,163,1)");
    const disconnected = marketNodes.find(n => n.text === "DISCONNECTED")!;
    expect(disconnected.style.color).toBe("rgba(255,70,85,1)");
    expect((html.match(/<button/g) ?? [])).toHaveLength(2);
    expect(html).toContain('aria-label="Notifications, three sample alerts"');
    expect(html).toContain('aria-label="Settings display only"');
    expect(html).not.toContain("<input");
  });
  it("retains exact samples and primitive counts without manufacturing history", () => {
    for (const text of ["5,023.41", "15,628.17", "34,816.31", "1,823.64", "$189.64", "42.30M", "48.10M", "$2.94T", "Alpaca SIP", "Polygon.io", "Databento (inactive)"]) expect(html).toContain(text);
    const chart=marketNodes.filter(n=>n.parentId===chartNodeId);
    expect(chart.filter(n=>n.name.toLowerCase().includes("wick"))).toHaveLength(24);
    expect(chart.filter(n=>n.name.toLowerCase().includes("body"))).toHaveLength(24);
    expect(chart.filter(n=>n.name.toLowerCase().includes("volume"))).toHaveLength(24);
    expect(chart.filter(n=>n.type==="TEXT")).toHaveLength(9);
    expect(marketNodes.filter(n=>n.paths)).toHaveLength(21);
    expect(html).toContain("underlying OHLC records are not supplied");
  });
  it("does not mount Markets on Dashboard or another route", () => {
    for(const path of ["/dashboard","/strategies"]){
      const other=renderToStaticMarkup(<App initialPath={path}/>);
      expect(other).not.toContain('class="markets-page"');
      expect(other).not.toContain("MD-EV-20261017-000184");
    }
  });
});
