import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { App } from "./App";

describe("Page 03 Strategies Figma composition", () => {
  const markup = renderToStaticMarkup(<App initialPath="/strategies" />);

  it("renders the nine authoritative Page 03 regions", () => {
    for (const nodeId of ["230:2563", "230:2564", "230:2565", "230:2566", "230:2567", "230:2568", "230:2569", "230:2570", "230:2571"]) {
      expect(markup).toContain(`data-node-id="${nodeId}"`);
    }
  });

  it("preserves the exact summary and strategy records", () => {
    for (const value of ["3 Running · 1 Restricted · 1 Paused", "+£4,260", "31 Pass · 9 Restricted · 7 Blocked", "−3.2%", "Momentum Strategy", "Mean Reversion", "Statistical Arbitrage", "Volatility Breakout", "Trend Strategy"]) {
      expect(markup).toContain(value);
    }
    expect((markup.match(/class="p03-strategy-row/g) ?? [])).toHaveLength(5);
  });

  it("preserves distinct blocked decision-trace semantics", () => {
    for (const value of ["BUY PROPOSED", "CREATED", "BLOCKED", "NOT CREATED", "NOT SENT", "N/A", "RECORDED", "DEC-EV-20261022-0184"]) {
      expect(markup).toContain(value);
    }
  });

  it("uses the recovered chart vectors and labels", () => {
    expect(markup).toContain("M 0 45 L 35.5 38.70000076293945");
    expect(markup).toContain("M 0 27.899999618530273 L 35.5 24.299999237060547");
    expect(markup).toContain("M 0 0 L 35.5 0 L 71 0 L 106.5 2.0710058212280273");
    for (const value of ["S&amp;P 500 benchmark", "Sample base £25,000", "Drawdown · max −2.4%", "17 Sep", "01 Oct", "17 Oct"]) expect(markup).toContain(value);
  });

  it("keeps controls display-only and authority bounded", () => {
    expect((markup.match(/data-display-only="true"/g) ?? [])).toHaveLength(9);
    expect(markup).toContain("PAPER / REPLACEABLE DESIGN DATA");
    expect(markup).toContain("NO LIVE TRADING AUTHORITY");
    expect(markup).not.toContain("onSubmit");
  });

  it("preserves native and fit-to-window routing", () => {
    expect(markup).toContain("console-viewport native-fidelity");
    expect(markup).toContain("strategies-frame");
    const fitted = renderToStaticMarkup(<App initialPath="/strategies?fit=window" />);
    expect(fitted).toContain("console-viewport fit-to-window");
    expect((markup.match(/aria-current="page"/g) ?? [])).toHaveLength(1);
  });
});
