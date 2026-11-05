import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { App, locationState, navigationLocation } from "./App";
import { pages } from "./data";
import { toneFor } from "./components/Panel";

describe("ATIS Operator Console", () => {
  it("defines exactly twelve unique routes", () => {
    expect(pages).toHaveLength(12);
    expect(new Set(pages.map((page) => page.slug)).size).toBe(12);
  });

  for (const page of pages) {
    it(`renders /${page.slug} with its active navigation state`, () => {
      const markup = renderToStaticMarkup(<App initialPath={`/${page.slug}`} />);
      const expectedHeading = page.slug === "dashboard" ? "Operator Dashboard"
        : page.slug === "markets" ? "<h1>Markets</h1>"
        : page.slug === "portfolio" ? `<h1 class="sr-only">${page.title.replaceAll("&", "&amp;")}</h1>`
        : `<h1>${page.title.replaceAll("&", "&amp;")}</h1>`;
      expect(markup).toContain(expectedHeading);
      expect(markup).toContain(`href=\"/${page.slug}\"`);
      expect(markup).toContain("aria-current=\"page\"");
      expect((markup.match(/aria-current=\"page\"/g) ?? [])).toHaveLength(1);
      expect(markup).toContain("PAPER / REPLACEABLE DESIGN DATA");
      expect(markup).toContain("NO ORDER SUBMISSION");
    });
  }

  it("keeps protected operations representational", () => {
    const markup = renderToStaticMarkup(<App initialPath="/settings" />);
    expect(markup).toContain("NONINTERACTIVE");
    expect(markup).toContain("NOT AUTHORISED");
  });

  it("preserves the recovered critical record counts and values", () => {
    const html = (slug: string) => renderToStaticMarkup(<App initialPath={`/${slug}`} />);
    expect(html("dashboard")).toContain("£21,482.33");
    const portfolio = html("portfolio");
    expect(portfolio).toContain('aria-label="Eight paper portfolio positions"');
    expect((portfolio.match(/class="row" role="row"/g) ?? [])).toHaveLength(8);
    expect(portfolio).not.toContain("Portfolio record");
    expect(html("orders")).toContain("OMS-P-0008");
    expect(html("risk-gateway")).toContain("47 evaluations");
    expect(html("execution")).toContain("FILL-P-0003-01");
    expect(html("reconciliation")).toContain("NOT ESTABLISHED");
    for (const value of ["68%", "74%", "61%", "42%"]) expect(html("system-health")).toContain(value);
    const audit = html("audit-log");
    expect(audit).toContain("Audit Event Register");
    expect(audit).toMatch(/data-figma-id="382:829"[^>]*><span>INGESTION FRESHNESS<\/span>/);
    expect(audit).toMatch(/data-figma-id="382:830"[^>]*><span>STALE<\/span>/);
    expect(audit).toMatch(/data-figma-id="382:832"[^>]*><span>TRADING AUTHORITY IMPACT<\/span>/);
    expect(audit).toMatch(/data-figma-id="382:833"[^>]*><span>NO CHANGE<\/span>/);
    expect(new Set(audit.match(/AUD-P-00(?:10|0[1-9])/g))).toHaveLength(10);
  });

  it("uses explicit fail-closed display-state semantics", () => {
    expect(toneFor("HEALTHY")).toBe("healthy");
    expect(toneFor("STALE")).toBe("attention");
    expect(toneFor("UNKNOWN")).toBe("unknown");
    expect(toneFor("NOT ESTABLISHED")).toBe("unknown");
    expect(toneFor("RESTRICTED")).toBe("restricted");
    expect(toneFor("This text happens to say healthy")).toBe("info");
  });

  it("keeps cross-page order and evidence identities coherent", () => {
    for (const slug of ["orders", "risk-gateway", "execution", "reconciliation", "audit-log"]) {
      const markup = renderToStaticMarkup(<App initialPath={`/${slug}`} />);
      const identity = slug === "audit-log" ? "RISK-P-0003" : slug === "risk-gateway" ? "RGE-P-00043" : "OMS-P-0003";
      expect(markup).toContain(identity);
    }
  });

  it("does not accept route-wide text as a substitute for record-local Phase B associations", () => {
    const risk = renderToStaticMarkup(<App initialPath="/risk-gateway" />);
    expect(risk).toMatch(/data-figma-id="299:626"[^>]*><span>RGE-P-00043<\/span>/);
    expect(risk).not.toMatch(/data-figma-id="299:626"[^>]*><span>OMS-P-0003<\/span>/);
    const execution = renderToStaticMarkup(<App initialPath="/execution" />);
    expect(execution).toMatch(/data-figma-id="328:630"[^>]*><span>FILL-P-0003-01<\/span>/);
    expect(execution).not.toMatch(/data-figma-id="328:630"[^>]*><span>OMS-P-0003<\/span>/);
  });
});

describe("URL-authoritative fit mode", () => {
  for (const query of ["", "?fit=window", "?fit=native", "?other=window", "?fit=Window"]) {
    it(`derives direct and restored route/mode from ${query || "native URL"}`, () => {
      const state = locationState(`/markets${query}#details`);
      expect(state).toEqual({ path: "/markets", fitToWindow: query === "?fit=window" });
      const markup = renderToStaticMarkup(<App initialPath={`/markets${query}`} />);
      expect(markup).toContain(query === "?fit=window" ? "console-viewport fit-to-window" : "console-viewport native-fidelity");
      expect(markup).toContain("<h1>Markets</h1>");
    });
  }
  for (const fitted of [false, true]) {
    it(`retains ${fitted ? "fitted" : "native"} mode through all twelve destinations and restored URLs`, () => {
      const start = `/dashboard${fitted ? "?fit=window" : ""}`;
      for (const page of pages) {
        const next = navigationLocation(`/${page.slug}`, start);
        expect(next).toBe(`/${page.slug}${fitted ? "?fit=window" : ""}`);
        expect(locationState(next)).toEqual({ path: `/${page.slug}`, fitToWindow: fitted });
      }
    });
  }
  it("uses the current URL over a conflicting destination fit query", () => {
    expect(navigationLocation("/markets?fit=window", "/dashboard")).toBe("/markets");
    expect(navigationLocation("/markets?fit=native", "/dashboard?fit=window")).toBe("/markets?fit=window");
  });
});
