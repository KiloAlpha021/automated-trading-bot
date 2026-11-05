import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { App } from "./App";

const expectedRows = [
 ["AAPL","50","£134.00","£140.00","£7,000.00","+£300.00","Momentum Strategy","Technology","WATCH"],
 ["MSFT","30","£191.6667","£200.00","£6,000.00","+£250.00","Mean Reversion","Technology","NORMAL"],
 ["NVDA","20","£192.50","£200.00","£4,000.00","+£150.00","Volatility Breakout","Technology","REDUCING"],
 ["TSLA","10","£340.00","£350.00","£3,500.00","+£100.00","Statistical Arbitrage","Consumer Cyclical","NORMAL"],
 ["AMD","20","£121.00","£125.00","£2,500.00","+£80.00","Trend Strategy","Technology","NORMAL"],
 ["META","10","£288.00","£300.00","£3,000.00","+£120.00","Momentum Strategy","Communication","NORMAL"],
 ["GOOGL","10","£241.00","£250.00","£2,500.00","+£90.00","Mean Reversion","Communication","WATCH"],
 ["PLTR","100","£22.2027","£23.6483","£2,364.83","+£144.56","Statistical Arbitrage","Technology","RESTRICTED"],
];
const visibleRows = (html:string) => [...html.matchAll(/<div class="row" role="row">([\s\S]*?)<\/div>/g)].map(row => [...row[1].matchAll(/<span role="cell" class="[^"]*">([^<]*)<\/span>/g)].map(cell => cell[1]));
const matchesRows = (rows:string[][]) => JSON.stringify(rows) === JSON.stringify(expectedRows);
describe("Page 04 Portfolio Figma composition",()=>{
 const markup=renderToStaticMarkup(<App initialPath="/portfolio"/>);
 it("renders the authoritative regions",()=>{for(const value of ["Portfolio Risk Utilisation","Concentration &amp; Risk Alerts","Sector Allocation — Positions","Positions","Portfolio Performance — Paper","Strategy Exposure — Market Value","Portfolio Risk Context"])expect(markup).toContain(value)});
 it("preserves exact fixtures",()=>{for(const value of ["£52,347.16","£30,864.83","+£1,234.56","AAPL","PLTR","70.84%","−0.66% observed · 1M"])expect(markup).toContain(value);expect((markup.match(/class="row" role="row"/g)??[])).toHaveLength(8)});
 it("exposes eight exact record-local position associations without fabrication",()=>{expect(markup).toContain('role="table" aria-label="Eight paper portfolio positions"');const rows=visibleRows(markup);expect(rows).toHaveLength(8);expect(rows).toEqual(expectedRows);expect(markup).not.toContain("Portfolio record")});
 it("rejects swapped quantity and risk-state associations",()=>{const rows=visibleRows(markup);const wrongQuantity=structuredClone(rows);wrongQuantity[0][1]="30";expect(matchesRows(wrongQuantity)).toBe(false);const wrongStatus=structuredClone(rows);wrongStatus[0][8]="NORMAL";expect(matchesRows(wrongStatus)).toBe(false);expect(matchesRows(rows)).toBe(true)});
 it("keeps authority bounded",()=>{expect(markup).toContain("PAPER / REPLACEABLE DESIGN DATA");expect(markup).toContain("NO LIVE TRADING AUTHORITY");expect(markup).toContain("portfolio-frame")});
});
