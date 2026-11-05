export type StrategyState = "CURRENT" | "DEGRADED";
export type OperatingState = "RUNNING" | "RESTRICTED" | "PAUSED";
export type RiskState = "PASS" | "RESTRICTED" | "BLOCKED";

export type StrategyRow = {
  strategy: string;
  assurance: StrategyState;
  operating: OperatingState;
  risk: RiskState;
  universe: string;
  timeframe: string;
  paperPnl: string;
};

export const strategySummary = [
  { label: "Operating Strategies", value: "3 Running · 1 Restricted · 1 Paused", tone: "neutral" },
  { label: "Paper P&L", value: "+£4,260", tone: "positive" },
  { label: "Signals Today", value: "47", tone: "neutral" },
  { label: "Risk Outcomes — Today (Paper)", value: "31 Pass · 9 Restricted · 7 Blocked", tone: "neutral" },
  { label: "Max Drawdown — Paper", value: "−3.2%", tone: "negative" },
] as const;

export const strategies: StrategyRow[] = [
  { strategy: "Momentum Strategy", assurance: "CURRENT", operating: "RUNNING", risk: "PASS", universe: "US Large Cap", timeframe: "Daily", paperPnl: "+£1,840" },
  { strategy: "Mean Reversion", assurance: "CURRENT", operating: "RUNNING", risk: "RESTRICTED", universe: "US ETFs", timeframe: "Daily", paperPnl: "+£920" },
  { strategy: "Statistical Arbitrage", assurance: "CURRENT", operating: "RUNNING", risk: "BLOCKED", universe: "US Equities", timeframe: "Daily", paperPnl: "+£1,120" },
  { strategy: "Volatility Breakout", assurance: "DEGRADED", operating: "RESTRICTED", risk: "BLOCKED", universe: "US Large Cap", timeframe: "Daily", paperPnl: "−£410" },
  { strategy: "Trend Strategy", assurance: "CURRENT", operating: "PAUSED", risk: "PASS", universe: "US ETFs", timeframe: "Daily", paperPnl: "+£790" },
];

export const selectedStrategy = {
  strategy: "Statistical Arbitrage",
  operating: "RUNNING",
  strategyType: "Statistical / Relative Value",
  universe: "US Equities",
  timeframe: "Daily",
  positionStyle: "Market Neutral",
  maxPositions: "8",
  riskProfile: "Standard Systematic",
  paperPnl: "+£1,120",
  lastDecision: "BUY proposed — blocked by Risk Gateway",
} as const;

export const decisionHistory = [
  { time: "15:42", strategy: "Stat Arb", instrument: "AAPL", signal: "BUY", decision: "PROPOSED", risk: "BLOCKED", downstream: "NOT SENT" },
  { time: "14:58", strategy: "Stat Arb", instrument: "MSFT", signal: "SELL", decision: "PROPOSED", risk: "PASS", downstream: "FILLED" },
  { time: "13:31", strategy: "Stat Arb", instrument: "SPY", signal: "HOLD", decision: "NO ACTION", risk: "N/A", downstream: "NOT SENT" },
  { time: "11:47", strategy: "Stat Arb", instrument: "NVDA", signal: "BUY", decision: "PROPOSED", risk: "RESTRICTED", downstream: "LIMITED" },
  { time: "10:16", strategy: "Stat Arb", instrument: "QQQ", signal: "SELL", decision: "PROPOSED", risk: "PASS", downstream: "FILLED" },
] as const;

export const decisionTrace = [
  { label: "Strategy Decision", value: "BUY PROPOSED", tone: "neutral" },
  { label: "Portfolio Intent", value: "CREATED", tone: "neutral" },
  { label: "Risk Gateway", value: "BLOCKED", tone: "negative" },
  { label: "OMS", value: "NOT CREATED", tone: "muted" },
  { label: "Execution / Broker", value: "NOT SENT", tone: "muted" },
  { label: "Reconciliation", value: "N/A", tone: "muted" },
  { label: "Evidence", value: "RECORDED", tone: "positive" },
] as const;

export const allocation = [
  { label: "Technology", value: 42, width: 189 },
  { label: "Communication", value: 18, width: 81 },
  { label: "Consumer Cyclical", value: 15, width: 67.5 },
  { label: "Industrials", value: 13, width: 58.5 },
  { label: "Other", value: 12, width: 54 },
] as const;

export const riskContext = [
  ["Max Position Size", "5%", "Daily Loss Limit", "−1.5%"],
  ["Max Drawdown Limit", "−5%", "Leverage Limit", "1.5×"],
  ["Concentration Limit", "20%", "", ""],
] as const;
