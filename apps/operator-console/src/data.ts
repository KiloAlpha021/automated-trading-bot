export type Tone = "healthy" | "attention" | "restricted" | "unknown" | "info";
export type DisplayState = Tone;

export type Metric = {
  label: string;
  value: string;
  detail?: string;
  tone?: Tone;
};

export type Row = Record<string, string>;

export type PanelDefinition = {
  title: string;
  eyebrow?: string;
  kind?: "metrics" | "table" | "trace" | "chart" | "list";
  className?: string;
  metrics?: Metric[];
  columns?: string[];
  rows?: Row[];
  rowStates?: DisplayState[];
  items?: string[];
  note?: string;
};

export type PageDefinition = {
  slug: string;
  label: string;
  title: string;
  subtitle: string;
  icon: string;
  summary: Metric[];
  panels: PanelDefinition[];
};

const sampleNote = "PAPER / REPLACEABLE DESIGN DATA";

export const pages: PageDefinition[] = [
  {
    slug: "dashboard",
    label: "Dashboard",
    title: "Operator Dashboard",
    subtitle: "System assurance, portfolio supervision and operational attention",
    icon: "dashboard.svg",
    summary: [
      { label: "System Assurance", value: "DEGRADED", detail: "4 healthy · 4 non-healthy", tone: "attention" },
      { label: "Trading Authority", value: "PAPER ONLY", detail: "Risk Gateway independent", tone: "attention" },
      { label: "Operating Mode", value: "PAPER", detail: "No live-capital authority", tone: "info" },
      { label: "Active Incidents", value: "2", detail: "INC-P-001 · INC-P-002", tone: "restricted" },
    ],
    panels: [
      { title: "Portfolio Overview", className: "span-6 tall", kind: "metrics", metrics: [
        { label: "Net Liquidation", value: "£52,347.16" }, { label: "Cash", value: "£21,482.33" },
        { label: "Day P&L", value: "+£348.20", tone: "healthy" }, { label: "Exposure", value: "64.8%" },
      ], note: sampleNote },
      { title: "Active Trading Strategies", className: "span-3 tall", kind: "list", items: ["Momentum Pro · PAPER", "Mean Reversion · REVIEW", "Volatility Breakout · TESTING", "Sector Rotation · RESEARCH"], note: sampleNote },
      { title: "System Health", className: "span-3 tall", kind: "list", items: ["Market Data · HEALTHY", "Strategy Engine · DEGRADED", "Risk Gateway · HEALTHY", "OMS · DEGRADED", "Execution · STALE", "Telemetry · UNKNOWN"], note: "2 active incidents · fail closed" },
      { title: "Strategy Assurance Pipeline", className: "span-8", kind: "trace", items: ["Research", "Backtest", "Validation", "Risk Review", "Paper"], note: "Promotion requires current evidence" },
      { title: "Attention & Alerts", className: "span-4", kind: "list", items: ["HIGH · OMS queue depth elevated", "MEDIUM · Execution telemetry stale", "UNKNOWN · Telemetry observation missing"], note: "Acknowledgement controls are noninteractive" },
      { title: "Risk Management", className: "span-4", kind: "metrics", metrics: [{ label: "Risk Utilisation", value: "62%" }, { label: "New Orders", value: "RESTRICTED", tone: "restricted" }] },
      { title: "Performance Metrics", className: "span-4", kind: "metrics", metrics: [{ label: "Return (24M)", value: "+12.40%", tone: "healthy" }, { label: "Max Drawdown", value: "−10.30%", tone: "restricted" }] },
      { title: "Markets Overview", className: "span-4", kind: "list", items: ["US session · CLOSED", "AAPL · $189.64", "MSFT · $428.50", "SPY · $575.30"], note: sampleNote },
    ],
  },
  {
    slug: "markets", label: "Markets", title: "Markets & Market Data", icon: "markets.svg",
    subtitle: "Point-in-time market observations, sessions, watchlists and data provenance",
    summary: [
      { label: "Market Data", value: "CURRENT", detail: "Observed 16:05:32 BST", tone: "healthy" },
      { label: "US Session", value: "CLOSED", detail: "Next boundary 14:30 BST", tone: "info" },
      { label: "Provider", value: "ALPACA SIP", detail: "Paper source", tone: "healthy" },
      { label: "Research Universe", value: "US EQUITIES", detail: "ETFs included", tone: "info" },
    ],
    panels: [
      { title: "Selected Instrument", className: "span-8 tall", kind: "chart", eyebrow: "AAPL · NASDAQ", metrics: [{ label: "Last", value: "$189.64" }, { label: "Change", value: "−0.37%", tone: "restricted" }, { label: "Volume", value: "48.2M" }], note: "1D · 1W · 1M · 3M · 1Y · ALL" },
      { title: "Data Feed Status", className: "span-4 tall", kind: "table", columns: ["Feed", "State", "Freshness"], rows: [{ Feed: "US Equities", State: "CONNECTED", Freshness: "CURRENT" }, { Feed: "ETFs", State: "CONNECTED", Freshness: "CURRENT" }, { Feed: "Corporate Actions", State: "CONNECTED", Freshness: "CURRENT" }], note: sampleNote },
      { title: "Market Activity", className: "span-8", kind: "table", columns: ["Symbol", "Price", "Change", "Volume"], rows: [{ Symbol: "NVDA", Price: "$145.27", Change: "+0.93%", Volume: "48.2M" }, { Symbol: "MSFT", Price: "$428.50", Change: "+0.60%", Volume: "21.7M" }, { Symbol: "SPY", Price: "$575.30", Change: "+0.30%", Volume: "62.4M" }] },
      { title: "Watchlist", className: "span-4", kind: "table", columns: ["Symbol", "Price", "Change"], rows: [{ Symbol: "AAPL", Price: "$189.64", Change: "−0.37%" }, { Symbol: "MSFT", Price: "$428.50", Change: "+0.60%" }, { Symbol: "SPY", Price: "$575.30", Change: "+0.30%" }] },
      { title: "Economic Calendar", className: "span-6", kind: "list", items: ["20 Oct 14:00 BST · Fed decision · HIGH", "22 Oct 13:30 BST · CPI · HIGH", "23 Oct 13:30 BST · Employment · MEDIUM"], note: "Scheduled times · observed 17 Oct 2026 · illustrative" },
      { title: "Data Provenance", className: "span-6", kind: "metrics", metrics: [{ label: "Evidence", value: "MD-EV-20261017-000184" }, { label: "PIT Status", value: "CURRENT", tone: "healthy" }, { label: "Validation", value: "VALID", tone: "healthy" }] },
    ],
  },
  {
    slug: "strategies", label: "Strategies", title: "Trading Strategies", icon: "strategies.svg",
    subtitle: "Research lifecycle, assurance state and risk-constrained strategy supervision",
    summary: [
      { label: "Strategies", value: "12", detail: "6 active · 4 complete · 2 on hold" },
      { label: "Current", value: "Momentum Pro", detail: "STRAT-P-MOM-001", tone: "healthy" },
      { label: "Assurance", value: "IN REVIEW", detail: "Evidence not independently verified", tone: "attention" },
      { label: "Order Eligibility", value: "RESTRICTED", detail: "Risk Gateway authority", tone: "restricted" },
    ],
    panels: [
      { title: "Strategy Register", className: "span-8 tall", kind: "table", columns: ["Strategy", "Type", "State", "Assurance", "Updated"], rows: [{ Strategy: "Momentum Pro", Type: "Long/Short", State: "PAPER", Assurance: "IN REVIEW", Updated: "17 Oct" }, { Strategy: "Mean Reversion", Type: "Equities", State: "BACKTEST", Assurance: "INSUFFICIENT", Updated: "17 Oct" }, { Strategy: "Volatility Breakout", Type: "Equities", State: "RESEARCH", Assurance: "NOT VERIFIED", Updated: "16 Oct" }, { Strategy: "Sector Rotation", Type: "ETF", State: "ON HOLD", Assurance: "REJECTED", Updated: "15 Oct" }] },
      { title: "Selected Strategy", className: "span-4 tall", kind: "metrics", metrics: [{ label: "Strategy ID", value: "STRAT-P-MOM-001" }, { label: "Mode", value: "PAPER" }, { label: "Return (24M)", value: "+12.40%", tone: "healthy" }, { label: "Max Drawdown", value: "−10.30%", tone: "restricted" }], note: sampleNote },
      { title: "Trading / Evidence Flow", className: "span-12", kind: "trace", items: ["Portfolio Intent", "Risk Gateway", "OMS", "Execution / Broker", "Reconciliation", "Evidence"], note: "CREATED · BLOCKED · NOT SENT · NOT VERIFIED" },
      { title: "Strategy Comparison", className: "span-7", kind: "table", columns: ["Metric", "Momentum", "Benchmark", "Status"], rows: [{ Metric: "Total return", Momentum: "12.40%", Benchmark: "7.80%", Status: "SAMPLE" }, { Metric: "Annualised", Momentum: "6.02%", Benchmark: "3.83%", Status: "SAMPLE" }, { Metric: "Sharpe", Momentum: "0.92", Benchmark: "0.62", Status: "ILLUSTRATIVE" }, { Metric: "Max drawdown", Momentum: "−10.30%", Benchmark: "−18.60%", Status: "SAMPLE" }] },
      { title: "Selected Decision Trace", className: "span-5", kind: "list", items: ["DEC-P-0003 · CREATED", "RISK-P-0003 · RESTRICTED", "OMS-P-0003 · PARTIALLY_FILLED", "REC-EV-P-0003 · NOT VERIFIED"], note: "Identity linkage does not imply verified evidence" },
    ],
  },
  {
    slug: "portfolio", label: "Portfolio", title: "Portfolio & Positions", icon: "portfolio.svg",
    subtitle: "Paper portfolio valuation, exposure, attribution and risk context",
    summary: [
      { label: "Net Liquidation", value: "£52,347.16", detail: "GBP reporting basis" },
      { label: "Cash", value: "£18,420.00", detail: "35.2%" },
      { label: "Invested", value: "£33,927.16", detail: "64.8%" },
      { label: "Risk State", value: "RESTRICTED", detail: "Independent gateway", tone: "restricted" },
    ],
    panels: [
      { title: "Portfolio Performance", className: "span-8 tall", kind: "chart", metrics: [{ label: "Period P&L", value: "+£1,120" }, { label: "Return", value: "+2.18%", tone: "healthy" }, { label: "Benchmark", value: "+1.36%" }], note: "1D · 1W · 1M · 3M · 1Y · ALL" },
      { title: "Allocation", className: "span-4 tall", kind: "list", items: ["Technology · 38%", "Financials · 17%", "Healthcare · 14%", "Industrials · 11%", "Cash · 20%"], note: sampleNote },
      { title: "Positions", className: "span-8", kind: "table", columns: ["Symbol", "Quantity", "Value", "Day P&L", "Weight"], rows: [{ Symbol: "AAPL", Quantity: "120", Value: "£18,041", "Day P&L": "+£122", Weight: "34.5%" }, { Symbol: "MSFT", Quantity: "35", Value: "£11,862", "Day P&L": "+£96", Weight: "22.7%" }, { Symbol: "SPY", Quantity: "9", Value: "£4,024", "Day P&L": "+£18", Weight: "7.7%" }, { Symbol: "NVDA", Quantity: "18", Value: "£2,081", "Day P&L": "+£32", Weight: "4.0%" }, { Symbol: "JPM", Quantity: "12", Value: "£1,894", "Day P&L": "−£8", Weight: "3.6%" }, { Symbol: "XLF", Quantity: "22", Value: "£764", "Day P&L": "+£5", Weight: "1.5%" }, { Symbol: "XLV", Quantity: "14", Value: "£1,402", "Day P&L": "+£11", Weight: "2.7%" }, { Symbol: "CASH", Quantity: "—", Value: "£12,206", "Day P&L": "—", Weight: "23.3%" }] },
      { title: "Exposure & Concentration", className: "span-4", kind: "metrics", metrics: [{ label: "Largest Position", value: "34.5%" }, { label: "Technology", value: "38.0%" }, { label: "Risk Utilisation", value: "62%" }] },
      { title: "Stress Testing", className: "span-6", kind: "table", columns: ["Scenario", "Shock", "Impact", "Status"], rows: [{ Scenario: "US equity selloff", Shock: "−10%", Impact: "−£3,184", Status: "HYPOTHETICAL" }, { Scenario: "Tech concentration", Shock: "−15%", Impact: "−£2,062", Status: "HYPOTHETICAL" }] },
      { title: "Performance Attribution", className: "span-6", kind: "table", columns: ["Contribution", "Return", "Basis"], rows: [{ Contribution: "Strategies", Return: "+1.42%", Basis: "GBP" }, { Contribution: "Sectors", Return: "+0.91%", Basis: "GBP" }, { Contribution: "Costs", Return: "−0.11%", Basis: "ESTIMATED" }, { Contribution: "Residual", Return: "−0.04%", Basis: "RECONCILIATION" }] },
    ],
  },
  {
    slug: "orders", label: "Orders", title: "Orders & OMS", icon: "orders.svg",
    subtitle: "Paper order lifecycle, execution state and evidence supervision",
    summary: [
      { label: "Open Orders", value: "3", detail: "Paper OMS" },
      { label: "Partially Filled", value: "1", detail: "OMS-P-0003", tone: "attention" },
      { label: "Rejected", value: "1", detail: "Risk restriction", tone: "restricted" },
      { label: "New Orders", value: "RESTRICTED", detail: "Fail closed", tone: "restricted" },
    ],
    panels: [
      { title: "Order Register", className: "span-8 tall", kind: "table", columns: ["Order ID", "Instrument", "Side", "Quantity", "Filled", "State"], rows: [{ "Order ID": "OMS-P-0003", Instrument: "AAPL", Side: "BUY", Quantity: "120", Filled: "45", State: "PARTIALLY_FILLED" }, { "Order ID": "OMS-P-0004", Instrument: "MSFT", Side: "BUY", Quantity: "20", Filled: "0", State: "OPEN" }, { "Order ID": "OMS-P-0005", Instrument: "SPY", Side: "SELL", Quantity: "5", Filled: "0", State: "REJECTED" }, { "Order ID": "OMS-P-0006", Instrument: "NVDA", Side: "BUY", Quantity: "15", Filled: "0", State: "CANCELLED" }, { "Order ID": "OMS-P-0007", Instrument: "XLF", Side: "BUY", Quantity: "40", Filled: "40", State: "FILLED" }, { "Order ID": "OMS-P-0008", Instrument: "XLV", Side: "SELL", Quantity: "8", Filled: "0", State: "HELD" }] },
      { title: "Selected Order", className: "span-4 tall", kind: "metrics", metrics: [{ label: "Order", value: "OMS-P-0003" }, { label: "Ordered", value: "120 shares" }, { label: "Filled", value: "45 @ $189.82" }, { label: "Remaining", value: "75 shares" }], note: "120 = 45 + 75" },
      { title: "Order Lifecycle", className: "span-12", kind: "trace", items: ["Risk Gateway", "OMS", "Broker", "Execution", "Remaining Open"], note: "RISK-P-0003 · OMS-P-0003 · BRK-P-0003 · FILL-P-0003-01" },
      { title: "Execution Summary", className: "span-4", kind: "metrics", metrics: [{ label: "Status", value: "PARTIALLY_FILLED", tone: "attention" }, { label: "Average Fill", value: "$189.82" }, { label: "Estimated Cost", value: "$1.27" }] },
      { title: "Order Exceptions", className: "span-4", kind: "list", items: ["REJECTED · OMS-P-0005", "CANCELLED · OMS-P-0006", "UNKNOWN · fail closed"], note: "No missing value is treated as zero" },
      { title: "Order Evidence", className: "span-4", kind: "list", items: ["Risk · RISK-P-0003", "OMS · OMS-P-0003", "Broker · BRK-P-0003", "Fill · FILL-P-0003-01"], note: "Recorded ≠ independently verified" },
    ],
  },
  {
    slug: "risk-gateway", label: "Risk Gateway", title: "Risk Gateway", icon: "risk.svg",
    subtitle: "Independent pre-trade controls, restrictions and decision evidence",
    summary: [
      { label: "Gateway State", value: "HEALTHY", detail: "Decision engine available", tone: "healthy" },
      { label: "Trading Authority", value: "PAPER ONLY", detail: "Independent ceiling", tone: "attention" },
      { label: "New Orders", value: "RESTRICTED", detail: "Operational incident", tone: "restricted" },
      { label: "Monitoring", value: "CURRENT", detail: "Observed 16:07 BST", tone: "healthy" },
    ],
    panels: [
      { title: "Decision Register · 47 evaluations", className: "span-8 tall", kind: "table", columns: ["Decision", "Order", "Outcome", "Limit", "Evidence"], rows: [{ Decision: "RISK-P-0003", Order: "OMS-P-0003", Outcome: "PASS", Limit: "Paper ceiling", Evidence: "RISK-EV-P-0003" }, { Decision: "RISK-P-0005", Order: "OMS-P-0005", Outcome: "BLOCK", Limit: "New orders", Evidence: "RISK-EV-P-0005" }, { Decision: "RISK-P-0006", Order: "OMS-P-0006", Outcome: "RESTRICT", Limit: "Concentration", Evidence: "RISK-EV-P-0006" }] },
      { title: "Selected Risk Decision", className: "span-4 tall", kind: "metrics", metrics: [{ label: "Decision", value: "RISK-P-0003" }, { label: "Order", value: "OMS-P-0003" }, { label: "Outcome", value: "PASS", tone: "healthy" }, { label: "Authority", value: "PAPER ONLY", tone: "attention" }], note: "PASS does not grant live authority" },
      { title: "Portfolio Risk", className: "span-6", kind: "table", columns: ["Measure", "Value", "Limit", "State"], rows: [{ Measure: "Gross exposure", Value: "64.8%", Limit: "80%", State: "WITHIN" }, { Measure: "Largest position", Value: "34.5%", Limit: "35%", State: "ATTENTION" }, { Measure: "Sector concentration", Value: "38.0%", Limit: "40%", State: "ATTENTION" }] },
      { title: "Evaluation Trace", className: "span-6", kind: "trace", items: ["Identity", "Authority", "Portfolio", "Limits", "Restriction", "Evidence"], note: "Unknown or stale inputs fail closed" },
      { title: "Active Restrictions", className: "span-6", kind: "list", items: ["NEW ORDERS RESTRICTED", "Live authority not established", "Independent incident response required"], note: "No override control" },
      { title: "Decision Evidence", className: "span-6", kind: "list", items: ["Snapshot · RISK-SNAP-P-001", "Decision · RISK-P-0003", "Evidence · RISK-EV-P-0003", "Audit · AUD-P-0007"], note: sampleNote },
    ],
  },
  {
    slug: "execution", label: "Execution", title: "Execution & Broker", icon: "execution.svg",
    subtitle: "Paper execution supervision, broker state and fill evidence",
    summary: [
      { label: "Execution Engine", value: "HEALTHY", detail: "Paper execution", tone: "healthy" },
      { label: "Broker", value: "CONNECTED", detail: "Alpaca Paper", tone: "healthy" },
      { label: "Heartbeat", value: "STALE", detail: "Observation age 74s", tone: "attention" },
      { label: "Reconciliation", value: "NOT VERIFIED", detail: "Fail closed", tone: "unknown" },
    ],
    panels: [
      { title: "Execution Register", className: "span-8 tall", kind: "table", columns: ["Execution", "Order", "Instrument", "Quantity", "Price", "State"], rows: [{ Execution: "EXE-P-0003", Order: "OMS-P-0003", Instrument: "AAPL", Quantity: "45", Price: "$189.82", State: "PARTIAL" }, { Execution: "EXE-P-0004", Order: "OMS-P-0004", Instrument: "MSFT", Quantity: "0", Price: "—", State: "OPEN" }, { Execution: "EXE-P-0005", Order: "OMS-P-0005", Instrument: "SPY", Quantity: "0", Price: "—", State: "REJECTED" }, { Execution: "EXE-P-0006", Order: "OMS-P-0006", Instrument: "NVDA", Quantity: "0", Price: "—", State: "CANCELLED" }, { Execution: "EXE-P-0007", Order: "OMS-P-0007", Instrument: "XLF", Quantity: "40", Price: "$45.12", State: "FILLED" }, { Execution: "EXE-P-0008", Order: "OMS-P-0008", Instrument: "XLV", Quantity: "0", Price: "—", State: "HELD" }] },
      { title: "Broker & Engine Health", className: "span-4 tall", kind: "list", items: ["Execution Engine · HEALTHY", "Broker Session · CONNECTED", "Heartbeat · STALE", "Observation · 16:07:24 BST"], note: "Healthy engine ≠ reconciled fills" },
      { title: "Selected Execution", className: "span-5", kind: "metrics", metrics: [{ label: "Execution", value: "EXE-P-0003" }, { label: "Order", value: "OMS-P-0003" }, { label: "Fill", value: "45 @ $189.82" }, { label: "Broker", value: "BRK-P-0003" }] },
      { title: "Lifecycle Trace", className: "span-7", kind: "trace", items: ["Risk PASS", "OMS created", "Broker ACK", "Fill 45", "Remaining 75", "Reconciliation unknown"], note: "BROKER_CUMULATIVE_FILL=NOT_VERIFIED" },
      { title: "Broker Rejections", className: "span-6", kind: "list", items: ["BRK-REJ-P-0001 · INVALID TIF", "No unrestricted retry", "Reason preserved in evidence"], note: sampleNote },
      { title: "Execution Evidence", className: "span-6", kind: "list", items: ["Order · OMS-P-0003", "Broker · BRK-P-0003", "Fill · FILL-P-0003-01", "Evidence · EXE-EV-P-0003"], note: "Recorded ≠ independently verified" },
    ],
  },
  {
    slug: "reconciliation", label: "Reconciliation", title: "Reconciliation", icon: "reconciliation.svg",
    subtitle: "Consistency between ATIS, broker, positions, cash, orders and execution records",
    summary: [
      { label: "Overall Status", value: "EXCEPTION", detail: "Not fully reconciled", tone: "restricted" },
      { label: "Comparison Groups", value: "5", detail: "3 verified · 1 exception · 1 unverified" },
      { label: "Broker Snapshot", value: "CURRENT", detail: "34s age · 60s limit", tone: "healthy" },
      { label: "Authority", value: "UNCHANGED", detail: "Reconciliation is not authority", tone: "info" },
    ],
    panels: [
      { title: "Reconciliation Health", className: "span-4", kind: "list", items: ["Engine · HEALTHY", "Latest Result · EXCEPTION", "Snapshot · CURRENT", "Unresolved · 1", "Unverified · 1"], note: sampleNote },
      { title: "Data Completeness", className: "span-8", kind: "table", columns: ["Group", "Source", "Freshness", "Comparison"], rows: [{ Group: "Cash", Source: "PRESENT", Freshness: "CURRENT", Comparison: "VERIFIED" }, { Group: "Positions", Source: "PRESENT", Freshness: "CURRENT", Comparison: "VERIFIED" }, { Group: "Orders", Source: "PRESENT", Freshness: "CURRENT", Comparison: "VERIFIED" }, { Group: "Fills", Source: "PRESENT", Freshness: "CURRENT", Comparison: "UNVERIFIED" }, { Group: "Account Balances", Source: "PRESENT", Freshness: "CURRENT", Comparison: "EXCEPTION" }] },
      { title: "Account Comparison", className: "span-8 tall", kind: "table", columns: ["Metric", "ATIS", "Broker", "Difference", "Status"], rows: [{ Metric: "Cash Balance", ATIS: "£18,420.00", Broker: "£18,420.00", Difference: "£0.00", Status: "VERIFIED" }, { Metric: "Account Balance", ATIS: "£52,347.16", Broker: "£52,322.16", Difference: "+£25.00", Status: "EXCEPTION" }, { Metric: "Buying Power", ATIS: "NOT ESTABLISHED", Broker: "NOT ESTABLISHED", Difference: "—", Status: "UNVERIFIED" }, { Metric: "Position Value", ATIS: "£33,927.16", Broker: "£33,927.16", Difference: "£0.00", Status: "VERIFIED" }] },
      { title: "Positions & Transactions", className: "span-4 tall", kind: "list", items: ["Positions · VERIFIED", "Orders · VERIFIED", "Fills · UNVERIFIED", "OMS-P-0003 · 120 = 45 + 75"], note: "Broker cumulative fill NOT VERIFIED" },
      { title: "Discrepancy Investigation", className: "span-4", kind: "list", items: ["REC-DISC-P-0001", "Account Balances", "+£25.00", "CONFIRMED_MISMATCH", "OPEN"], note: "Tolerance is illustrative" },
      { title: "Reconciliation Trace", className: "span-4", kind: "trace", items: ["ATIS snapshot", "Broker snapshot", "Freshness", "Identity", "Financial", "Fill", "Classification", "Evidence"], note: "Financial EXCEPTION · Fill UNVERIFIED" },
      { title: "Evidence & Controls", className: "span-4", kind: "list", items: ["Run · REC-RUN-P-001", "Evidence · REC-EV-P-0003", "Integrity · NOT VERIFIED", "Outcome · EXCEPTION"], note: "Protected controls noninteractive" },
    ],
  },
  {
    slug: "insights", label: "Insights", title: "Insights (Research & Analytics)", icon: "insights.svg",
    subtitle: "Research, backtesting, strategy analysis, robustness testing and market analytics",
    summary: [
      { label: "Research Projects", value: "12", detail: "6 active · 4 complete · 2 on hold" },
      { label: "Sample Review Passed", value: "3 / 12", detail: "2 review · 4 insufficient · 3 rejected", tone: "attention" },
      { label: "Latest Backtest", value: "+12.40%", detail: "BT-P-0001 · 24 months", tone: "healthy" },
      { label: "Evidence", value: "NOT VERIFIED", detail: "INS-EV-P-0001", tone: "unknown" },
    ],
    panels: [
      { title: "Research Pipeline", className: "span-7 tall", kind: "table", columns: ["Project", "Strategy", "Stage", "State", "Updated"], rows: [{ Project: "RD-001", Strategy: "Momentum Pro", Stage: "Validation", State: "IN REVIEW", Updated: "17 Oct" }, { Project: "RD-002", Strategy: "Mean Reversion", Stage: "Backtesting", State: "IN REVIEW", Updated: "17 Oct" }, { Project: "RD-003", Strategy: "Volatility Breakout", Stage: "Development", State: "TESTING", Updated: "16 Oct" }, { Project: "RD-004", Strategy: "News Sentiment", Stage: "Backtesting", State: "INSUFFICIENT", Updated: "16 Oct" }] },
      { title: "Strategy Performance · Illustrative Backtest", className: "span-5 tall", kind: "chart", metrics: [{ label: "Return", value: "+12.40%", tone: "healthy" }, { label: "Sharpe", value: "0.92" }, { label: "Drawdown", value: "−10.30%", tone: "restricted" }], note: "Strategy · SPY proxy · Buy & Hold · Jan 2024–Dec 2025" },
      { title: "Backtest Configuration", className: "span-4", kind: "list", items: ["BT-P-0001", "DS-P-US-EQ-001", "USD · 24 months", "Transaction cost · 1.0 bps", "Slippage · 0.5 bps"], note: sampleNote },
      { title: "Robustness & Validation", className: "span-4", kind: "list", items: ["Walk-forward · SAMPLE PASS", "Out-of-sample · SAMPLE PASS", "Parameter sensitivity · REVIEW", "Data snooping · NOT VERIFIED"], note: "No certified economic edge" },
      { title: "Performance Metrics", className: "span-4", kind: "table", columns: ["Metric", "Strategy", "Benchmark", "Delta"], rows: [{ Metric: "Total return", Strategy: "12.40%", Benchmark: "7.80%", Delta: "+4.60pp" }, { Metric: "Annualised", Strategy: "6.02%", Benchmark: "3.83%", Delta: "+2.19pp" }, { Metric: "Max drawdown", Strategy: "−10.30%", Benchmark: "−18.60%", Delta: "+8.30pp" }] },
      { title: "Recent Research Results", className: "span-8", kind: "table", columns: ["Time", "Strategy", "Test", "Return", "State", "Evidence"], rows: [{ Time: "17 Oct", Strategy: "Momentum Pro", Test: "Walk-forward", Return: "+12.4%", State: "SAMPLE PASS", Evidence: "INS-EV-P-0001" }, { Time: "17 Oct", Strategy: "Mean Reversion", Test: "Out-of-sample", Return: "+8.1%", State: "IN REVIEW", Evidence: "INS-EV-P-0002" }] },
      { title: "Research Evidence & Data", className: "span-4", kind: "list", items: ["Dataset presence · PRESENT", "PIT method · SAMPLE VALIDATED", "Methodology · COMPLETE", "Reproducible results · NOT VERIFIED"], note: "Report / export noninteractive" },
    ],
  },
  {
    slug: "system-health", label: "System Health", title: "System Health & Infrastructure", icon: "health.svg",
    subtitle: "Monitor components, dependencies, resources and operational readiness",
    summary: [
      { label: "System Health", value: "DEGRADED", detail: "4 non-healthy components", tone: "restricted" },
      { label: "Trading Authority", value: "PAPER ONLY", detail: "New orders restricted", tone: "attention" },
      { label: "Dependency Health", value: "DEGRADED", detail: "Missing observations", tone: "attention" },
      { label: "Open Incidents", value: "2", detail: "INC-P-001 · INC-P-002", tone: "restricted" },
    ],
    panels: [
      { title: "Component Health", className: "span-8 tall", kind: "table", columns: ["Component", "Health", "Freshness", "Operational Impact"], rows: [{ Component: "Market Data", Health: "HEALTHY", Freshness: "CURRENT", "Operational Impact": "None" }, { Component: "Strategy Engine", Health: "DEGRADED", Freshness: "CURRENT", "Operational Impact": "Monitoring" }, { Component: "Risk Gateway", Health: "HEALTHY", Freshness: "CURRENT", "Operational Impact": "Independent" }, { Component: "OMS", Health: "DEGRADED", Freshness: "CURRENT", "Operational Impact": "Orders restricted" }, { Component: "Execution", Health: "STALE", Freshness: "74s", "Operational Impact": "Fail closed" }, { Component: "Telemetry", Health: "UNKNOWN", Freshness: "MISSING", "Operational Impact": "Fail closed" }] },
      { title: "Infrastructure Resources", className: "span-4 tall", kind: "metrics", metrics: [{ label: "CPU", value: "68%" }, { label: "Memory", value: "74%" }, { label: "Disk", value: "61%" }, { label: "Network", value: "42%" }], note: "Observed 16:07:21 BST" },
      { title: "Dependency Monitoring", className: "span-6", kind: "table", columns: ["Dependency", "State", "Freshness", "Restriction"], rows: [{ Dependency: "Alpaca Paper", State: "HEALTHY", Freshness: "1s", Restriction: "None" }, { Dependency: "Market Feeds", State: "HEALTHY", Freshness: "3s", Restriction: "None" }, { Dependency: "Telemetry", State: "UNKNOWN", Freshness: "MISSING", Restriction: "Orders restricted" }] },
      { title: "Active Incidents", className: "span-6", kind: "table", columns: ["Incident", "Severity", "Component", "Impact", "State"], rows: [{ Incident: "INC-P-001", Severity: "HIGH", Component: "OMS", Impact: "Orders restricted", State: "ACKNOWLEDGED" }, { Incident: "INC-P-002", Severity: "MEDIUM", Component: "Execution", Impact: "Telemetry stale", State: "OPEN" }] },
      { title: "Incident Timeline", className: "span-7", kind: "trace", items: ["Detection", "Restriction", "Investigation", "Recovery Check", "Verified Restoration"], note: "Restoration NOT VERIFIED" },
      { title: "Diagnostics", className: "span-5", kind: "list", items: ["SYS-EV-P-001 · OMS queue threshold", "SYS-EV-P-002 · Heartbeat stale", "SYS-EV-P-003 · Telemetry missing"], note: sampleNote },
    ],
  },
  {
    slug: "audit-log", label: "Audit Log", title: "Audit Log & Evidence", icon: "audit.svg",
    subtitle: "Append-only supervision of decisions, actions, system state and evidence",
    summary: [
      { label: "Audit Integrity", value: "NOT VERIFIED", detail: "Sample evidence only", tone: "unknown" },
      { label: "Evidence Gaps", value: "2", detail: "Missing or unverified", tone: "restricted" },
      { label: "Ingestion Freshness", value: "STALE", detail: "Age 92s · limit 60s", tone: "attention" },
      { label: "Authority Impact", value: "NO CHANGE", detail: "Audit health grants no authority", tone: "info" },
    ],
    panels: [
      { title: "Audit Event Register · 10 events", className: "span-8 tall", kind: "table", columns: ["Time", "Event ID", "Source", "Category", "Outcome", "Evidence"], rows: [{ Time: "16:07:21 BST", "Event ID": "AUD-P-0010", Source: "RECON", Category: "RECON / SNAPSHOT", Outcome: "EXCEPTION", Evidence: "AUD-EV-P-0010" }, { Time: "16:06:58 BST", "Event ID": "AUD-P-0009", Source: "OMS", Category: "FILL / RECORDED", Outcome: "PARTIAL", Evidence: "AUD-EV-P-0009" }, { Time: "16:06:31 BST", "Event ID": "AUD-P-0008", Source: "RISK", Category: "CHECK / PASSED", Outcome: "PASS", Evidence: "AUD-EV-P-0008" }, { Time: "16:05:49 BST", "Event ID": "AUD-P-0007", Source: "BROKER", Category: "HEARTBEAT / DEGRADED", Outcome: "RESTRICTED", Evidence: "AUD-EV-P-0007" }, { Time: "16:04:22 BST", "Event ID": "AUD-P-0006", Source: "DATA", Category: "BATCH / INGESTED", Outcome: "SUCCESS", Evidence: "AUD-EV-P-0006" }, { Time: "16:03:51 BST", "Event ID": "AUD-P-0005", Source: "OPERATOR", Category: "CONFIG / CHANGE", Outcome: "REJECTED", Evidence: "AUD-EV-P-0005" }, { Time: "16:02:18 BST", "Event ID": "AUD-P-0004", Source: "TELEMETRY", Category: "OBSERVATION / MISSING", Outcome: "MISSING", Evidence: "MISSING" }, { Time: "16:01:42 BST", "Event ID": "AUD-P-0003", Source: "EXECUTION", Category: "BROKER ACK / OBSERVED", Outcome: "UNVERIFIED", Evidence: "AUD-EV-P-0003" }, { Time: "16:00:55 BST", "Event ID": "AUD-P-0002", Source: "STRATEGY", Category: "DECISION / PROPOSED", Outcome: "RECORDED", Evidence: "AUD-EV-P-0002" }, { Time: "15:59:30 BST", "Event ID": "AUD-P-0001", Source: "SYSTEM", Category: "INGESTION / STALE", Outcome: "STALE", Evidence: "AUD-EV-P-0001" }] },
      { title: "Selected Event Details", className: "span-4 tall", kind: "metrics", metrics: [{ label: "Event", value: "EVT-P-0009" }, { label: "Actor", value: "BROKER" }, { label: "Action", value: "ORDER_FILLED" }, { label: "Outcome", value: "PARTIAL" }, { label: "Evidence", value: "FILL-P-0003-01" }], note: sampleNote },
      { title: "Cross-System Evidence Chain", className: "span-12", kind: "trace", items: ["Strategy Decision", "Risk Gateway", "OMS", "Broker", "Fill", "Reconciliation"], note: "RISK-P-0003 · OMS-P-0003 · BRK-P-0003 · FILL-P-0003-01 · NOT VERIFIED" },
      { title: "Operator Actions & Security", className: "span-6", kind: "table", columns: ["Time", "Actor", "Action", "Outcome"], rows: [{ Time: "15:55 BST", Actor: "SK", Action: "Acknowledge incident", Outcome: "RECORDED" }, { Time: "14:22 BST", Actor: "SK", Action: "Export audit log", Outcome: "NONINTERACTIVE" }] },
      { title: "Audit Exceptions & Diagnostics", className: "span-6", kind: "list", items: ["Missing evidence · 1", "Sequence continuity · UNVERIFIED", "Integrity verification · NOT VERIFIED", "Stale ingestion · 0"], note: "No delete or overwrite controls" },
      { title: "Retention, Controls & Reporting", className: "span-12", kind: "list", items: ["Retention · 7 years (sample policy)", "Append-only requirement · REQUIRED", "Last observation · 16:07 BST", "Export / report · NONINTERACTIVE"], note: sampleNote },
    ],
  },
  {
    slug: "settings", label: "Settings", title: "Settings & Authority", icon: "settings.svg",
    subtitle: "Operating mode, access, risk authority and protected configuration",
    summary: [
      { label: "Operating Mode", value: "PAPER", detail: "Research strategies running", tone: "healthy" },
      { label: "Trading Authority", value: "PAPER ONLY", detail: "Authority ceiling", tone: "attention" },
      { label: "New Orders", value: "RESTRICTED", detail: "Independent incident", tone: "restricted" },
      { label: "Safety Interlocks", value: "ACTIVE", detail: "No bypass", tone: "healthy" },
    ],
    panels: [
      { title: "Operating Modes", className: "span-5 tall", kind: "table", columns: ["Mode", "Availability", "Authority"], rows: [{ Mode: "Research", Availability: "AVAILABLE", Authority: "NONE" }, { Mode: "Backtest", Availability: "AVAILABLE", Authority: "NONE" }, { Mode: "Paper", Availability: "CURRENT", Authority: "PAPER ONLY" }, { Mode: "Shadow", Availability: "RESTRICTED", Authority: "NONE" }, { Mode: "Live", Availability: "UNAUTHORISED", Authority: "NONE" }] },
      { title: "Trading Authority", className: "span-4 tall", kind: "metrics", metrics: [{ label: "Ceiling", value: "PAPER ONLY", tone: "attention" }, { label: "Order Eligibility", value: "RESTRICTED", tone: "restricted" }, { label: "Risk Gateway", value: "REQUIRED" }, { label: "Request", value: "NOT AUTHORISED" }] },
      { title: "Live-Authority Prerequisites", className: "span-3 tall", kind: "list", items: ["Infrastructure · NOT ESTABLISHED", "Risk evidence · NOT VERIFIED", "Owner authorisation · REQUIRED", "Audit record · REQUIRED"], note: "No activation control" },
      { title: "Protected Operations", className: "span-5", kind: "list", items: ["Kill switch · NONINTERACTIVE", "Authority change · AUTHORISATION REQUIRED", "Configuration change · PROTECTED", "Recovery · SEPARATE OPERATION"], note: "Restriction and restoration are distinct" },
      { title: "Configuration & Approvals", className: "span-7", kind: "table", columns: ["Parameter", "Current", "Proposed", "Status", "Evidence"], rows: [{ Parameter: "Max position", Current: "5%", Proposed: "2%", Status: "AUTHORISATION REQUIRED", Evidence: "CFG-EV-P-001" }, { Parameter: "Risk limit", Current: "20%", Proposed: "15%", Status: "PENDING", Evidence: "CFG-EV-P-002" }] },
      { title: "Operator Access & Integrations", className: "span-5", kind: "list", items: ["Role · OWNER", "Permission · PAPER CONTROL", "Broker · Alpaca Paper", "Credential status · CONFIGURED / HIDDEN"], note: "No credentials displayed" },
      { title: "Change History & Evidence", className: "span-7", kind: "table", columns: ["Change", "Actor", "Previous", "New", "Outcome", "Evidence"], rows: [{ Change: "CHG-P-001", Actor: "SK", Previous: "5%", New: "2%", Outcome: "PENDING", Evidence: "CFG-EV-P-001" }, { Change: "CHG-P-002", Actor: "SYSTEM", Previous: "ELIGIBLE", New: "RESTRICTED", Outcome: "APPLIED", Evidence: "SYS-EV-P-001" }] },
    ],
  },
];

export const defaultPage = pages[0];

export function pageForPath(pathname: string): PageDefinition {
  const slug = pathname.replace(/^\/+|\/+$/g, "") || defaultPage.slug;
  return pages.find((page) => page.slug === slug) ?? defaultPage;
}
