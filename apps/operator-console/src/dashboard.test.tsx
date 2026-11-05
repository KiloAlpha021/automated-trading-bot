import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { App } from "./App";

describe("Page 01 Dashboard reference composition", () => {
  const markup = renderToStaticMarkup(<App initialPath="/dashboard" />);

  it("renders the exact Page 01 record populations", () => {
    expect((markup.match(/class="strategy-row"/g) ?? [])).toHaveLength(6);
    expect((markup.match(/class="health-row"/g) ?? [])).toHaveLength(8);
    expect((markup.match(/<li class="(?:complete|current|pending|gated)"/g) ?? [])).toHaveLength(7);
    expect((markup.match(/class="market-row"/g) ?? [])).toHaveLength(4);
  });

  it("preserves the exact financial, status and authority text", () => {
    for (const value of ["£52,347.16", "+4.69%", "+£312.48", "+£1,234.56", "£21,482.33", "PAPER ONLY", "No Live Trading Authority", "Running", "Blocked", "Paused", "Restricted", "Inactive", "Healthy", "Degraded", "Stale", "Unknown"]) {
      expect(markup).toContain(value);
    }
  });

  it("keeps protected controls representational", () => {
    expect(markup).not.toContain("onSubmit");
    expect(markup).toContain("No broker connectivity, order submission, or live-capital authority");
  });

  it("uses the recovered Figma vector primitives", () => {
    expect((markup.match(/data-vector-kind="risk-segment"/g) ?? [])).toHaveLength(66);
    expect((markup.match(/data-vector-kind="performance-bar"/g) ?? [])).toHaveLength(28);
    expect((markup.match(/data-vector-kind="market-sparkline"/g) ?? [])).toHaveLength(4);
    expect((markup.match(/data-assurance-icon=/g) ?? [])).toHaveLength(4);
    expect(markup).toContain("M 0 43 L 11 42 L 22 41");
    expect(markup).toContain("/assets/figma-page01/1dbe6.svg");
    expect(markup).toContain("/assets/figma-page01/b6160.svg");
    expect((markup.match(/class="pipeline-connector /g) ?? [])).toHaveLength(6);
    expect(markup).toContain("/assets/figma-page01/7f7b8.svg");
    expect(markup).toContain("/assets/figma-page01/99cc4.svg");
  });

  it("keeps native fidelity separate from fit-to-window mode", () => {
    expect(renderToStaticMarkup(<App initialPath="/dashboard" />)).toContain("native-fidelity");
    expect(renderToStaticMarkup(<App initialPath="/dashboard?fit=window" />)).toContain("fit-to-window");
  });
});
