import { StrategyPerformanceChart } from "./StrategyPerformanceChart";
import type { ReactNode } from "react";
import {
  allocation,
  decisionHistory,
  decisionTrace,
  riskContext,
  selectedStrategy,
  strategies,
  strategySummary,
} from "./strategies.fixtures";
import "./strategies.css";

function DisplayControl({ className = "", children, nodeId }: { className?: string; children: ReactNode; nodeId?: string }) {
  return <button type="button" className={`p03-display-control ${className}`} aria-disabled="true" data-display-only="true" data-node-id={nodeId}>{children}</button>;
}

function State({ value }: { value: string }) {
  const key = value.toLowerCase().replaceAll(" ", "-").replaceAll("/", "na");
  return <span className={`p03-state state-${key}`}>{value}</span>;
}

function Summary() {
  return <section className="p03-region p03-summary" data-node-id="230:2563" aria-labelledby="strategies-title">
    <h1 id="strategies-title">Strategies</h1>
    <p className="p03-subtitle">Strategy status, assurance, performance and decision trace</p>
    <div className="p03-summary-cards">
      {strategySummary.map((item, index) => <article className={`p03-summary-card tone-${item.tone}`} key={item.label} data-node-id={["232:654", "232:662", "232:670", "232:678", "232:686"][index]}>
        <span>{item.label}</span><strong>{item.value}</strong>
      </article>)}
    </div>
  </section>;
}

function ActiveStrategies() {
  return <section className="p03-panel p03-active" data-node-id="230:2564" aria-labelledby="active-strategies-title">
    <h2 id="active-strategies-title">Active Strategies</h2>
    <p className="p03-risk-scope">Risk Gateway: latest assessed decision · Paper</p>
    <DisplayControl className="p03-compare" nodeId="418:1004">COMPARE ↗</DisplayControl>
    <div className="p03-strategy-table" role="table" aria-label="Active paper strategies">
      <div className="p03-strategy-head" role="row">
        {['Strategy','Assurance','Operating State','Risk Gateway','Universe','Timeframe','Paper P&L'].map(label => <span role="columnheader" key={label}>{label}</span>)}
      </div>
      {strategies.map((row, index) => <div className={`p03-strategy-row${index === 2 ? " selected" : ""}`} role="row" key={row.strategy} data-node-id={["232:700", "232:711", "232:723", "232:735", "232:749"][index]}>
        <span role="cell">{row.strategy}</span>
        <span role="cell"><State value={row.assurance} /></span>
        <span role="cell"><State value={row.operating} /></span>
        <span role="cell"><State value={row.risk} /></span>
        <span role="cell">{row.universe}</span>
        <span role="cell">{row.timeframe}</span>
        <span role="cell" className={row.paperPnl.startsWith("−") ? "negative" : "positive"}>{row.paperPnl}</span>
      </div>)}
    </div>
  </section>;
}

function SelectedStrategy() {
  const fields = [
    ["Strategy", selectedStrategy.strategy, "Operating State", selectedStrategy.operating],
    ["Strategy Type", selectedStrategy.strategyType, "Universe", selectedStrategy.universe],
    ["Timeframe", selectedStrategy.timeframe, "Position Style", selectedStrategy.positionStyle],
    ["Max Positions", selectedStrategy.maxPositions, "Risk Profile / Policy", selectedStrategy.riskProfile],
    ["Paper P&L", selectedStrategy.paperPnl, "Last Decision", selectedStrategy.lastDecision],
  ];
  return <section className="p03-panel p03-selected" data-node-id="230:2565" aria-labelledby="selected-strategy-title">
    <h2 id="selected-strategy-title">Selected Strategy</h2>
    <DisplayControl className="p03-view" nodeId="I232:787;232:764">View Strategy</DisplayControl>
    <div className="p03-selected-fields">
      {fields.map(([a, av, b, bv], index) => <div className={`p03-field-row field-${index}`} key={a}>
        <div><span>{a}</span>{a === "Paper P&L" ? <strong className="positive">{av}</strong> : <strong>{av}</strong>}</div>
        <div><span>{b}</span>{b === "Operating State" ? <State value={bv} /> : <strong>{bv}</strong>}</div>
      </div>)}
    </div>
  </section>;
}

function Performance() {
  return <section className="p03-panel p03-performance" data-node-id="230:2566" aria-labelledby="performance-title">
    <h2 id="performance-title">Strategy Performance</h2>
    <p className="p03-context">Statistical Arbitrage · Paper</p>
    <strong className="p03-performance-pnl">+£1,120</strong>
    <div className="p03-range" aria-label="Display-only time range">
      {['1D','1W','1M','3M','1Y','ALL'].map(value => <DisplayControl className={value === '1M' ? 'selected' : ''} key={value}>{value}</DisplayControl>)}
    </div>
    <StrategyPerformanceChart />
  </section>;
}

function DecisionHistory() {
  return <section className="p03-panel p03-history" data-node-id="230:2567" aria-labelledby="decision-history-title">
    <h2 id="decision-history-title">Decision History</h2>
    <p className="p03-context">Stat Arb = Statistical Arbitrage · Paper outcomes</p>
    <div className="p03-decision-table" role="table" aria-label="Paper strategy decision history">
      <div className="p03-decision-head" role="row">
        {['Time','Strategy','Instrument','Signal','Decision','Risk Outcome','Downstream Outcome'].map(label => <span role="columnheader" key={label}>{label}</span>)}
      </div>
      {decisionHistory.map((row, index) => <div className="p03-decision-row" role="row" key={`${row.time}-${row.instrument}`} data-node-id={["234:777", "234:793", "234:809", "234:825", "234:841"][index]}>
        <span role="cell">{row.time}</span><span role="cell">{row.strategy}</span><span role="cell">{row.instrument}</span><span role="cell">{row.signal}</span><span role="cell">{row.decision}</span><span role="cell"><State value={row.risk} /></span><span role="cell"><State value={row.downstream} /></span>
      </div>)}
    </div>
  </section>;
}

function DecisionTrace() {
  return <section className="p03-panel p03-trace" data-node-id="230:2568" aria-labelledby="trace-title">
    <h2 id="trace-title">Selected Decision Trace</h2>
    <p className="p03-trace-context">Statistical Arbitrage · AAPL · Paper</p>
    <span className="p03-evidence-id">DEC-EV-20261022-0184</span>
    <DisplayControl className="p03-trace-control" nodeId="404:523">TRACE ↗</DisplayControl>
    <ol>
      {decisionTrace.map(item => <li key={item.label}><span>{item.label}</span><strong className={`tone-${item.tone}`}>{item.value}</strong></li>)}
    </ol>
  </section>;
}

function Exposure() {
  return <section className="p03-panel p03-exposure" data-node-id="230:2569" aria-labelledby="exposure-title">
    <h2 id="exposure-title">Strategy Exposure</h2><p>Statistical Arbitrage · Paper</p>
    <div><strong>Gross 24%</strong><strong>Net 2%</strong></div>
    <small>AAPL 6%&nbsp;&nbsp; · &nbsp;&nbsp;MSFT 5%&nbsp;&nbsp; · &nbsp;&nbsp;NVDA 4%&nbsp;&nbsp; · &nbsp;&nbsp;Other 9%</small>
  </section>;
}

function Allocation() {
  return <section className="p03-panel p03-allocation" data-node-id="230:2570" aria-labelledby="allocation-title">
    <h2 id="allocation-title">Selected Strategy Allocation</h2>
    <div className="p03-allocation-rows">{allocation.map(item => <div key={item.label}><span>{item.label}</span><i style={{ width: item.width }} /><strong>{item.value}%</strong></div>)}</div>
  </section>;
}

function RiskContext() {
  return <section className="p03-panel p03-risk" data-node-id="230:2571" aria-labelledby="risk-context-title">
    <h2 id="risk-context-title">Strategy Risk Context</h2><p>Sample configuration · read-only</p>
    <div className="p03-risk-rows">{riskContext.map((row, index) => <div key={index}><span>{row[0]}</span><strong>{row[1]}</strong>{row[2] && <><span>{row[2]}</span><strong>{row[3]}</strong></>}</div>)}</div>
  </section>;
}

export function Strategies() {
  return <section className="strategies-page" data-page="strategies" data-classification="PAPER / REPLACEABLE DESIGN DATA">
    <div hidden><h1>Trading Strategies</h1></div>
    <Summary /><ActiveStrategies /><SelectedStrategy /><Performance /><DecisionHistory /><DecisionTrace /><Exposure /><Allocation /><RiskContext />
    <p className="p03-classification">PAPER / REPLACEABLE DESIGN DATA · NO LIVE TRADING AUTHORITY</p>
  </section>;
}
