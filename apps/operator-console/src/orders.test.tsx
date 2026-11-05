import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { App } from "./App";
import { orders } from "./pages/orders.fixtures";

describe("Page05 Orders", () => {
  const markup = renderToStaticMarkup(<App initialPath="/orders" />);
  it("renders the complete canonical register", () => {
    expect(orders).toHaveLength(6);
    for (const order of orders) expect(markup).toContain(order.id);
    expect(markup).toContain("PARTIALLY_FILLED");
    expect(markup).toContain("Evidence recorded ≠ independently verified execution");
  });
  it("keeps selected-order quantities coherent", () => {
    const selected = orders.find(order => order.id === "OMS-P-0003");
    expect(Number(selected?.quantity)).toBe(Number(selected?.filled) + 75);
    expect(markup).toContain("45 shares @ $189.82");
  });
  it("preserves fail-closed authority semantics", () => {
    expect(markup).toContain("does not grant live authority");
    expect(markup).toContain("NO LIVE TRADING AUTHORITY");
    expect(markup).toContain("NO ORDER SUBMISSION");
    expect(markup).toContain("orders-frame");
  });
  it("keeps controls noninteractive", () => {
    expect(markup).toContain("TABLE OPTIONS · NONINTERACTIVE");
    expect((markup.match(/aria-disabled="true"/g) ?? []).length).toBeGreaterThanOrEqual(3);
  });
});
