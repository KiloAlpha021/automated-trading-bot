export const dashboardReference = {
  assurance: [
    ["SYSTEM ASSURANCE", "CURRENT", "All evidence valid", "No blocking issues", "healthy"],
    ["RESEARCH & STRATEGIES", "Healthy", "All strategies in validation", "", "research"],
    ["SECURITY & COMPLIANCE", "Compliant", "All controls active", "", "security"],
    ["MARKET DATA & BROKER", "Operational", "Real-time data connected", "", "broker"],
  ],
  strategies: [
    ["Momentum", "Running", "AAPL, MSFT", "+1.24%", "running"],
    ["Mean Reversion", "Running", "AAPL", "+0.82%", "running"],
    ["Statistical Arb", "Blocked", "AAPL, MSFT", "Historical", "blocked"],
    ["Trend Strategy", "Paused", "MSFT", "Historical", "paused"],
    ["Volatility Breakout", "Restricted", "IWM", "Historical", "restricted"],
    ["ETF Sample", "Inactive", "SPY, QQQ", "—", "inactive"],
  ],
  health: [
    ["Market Data", "HEALTHY", "healthy", "markets.svg"],
    ["Strategy Engine", "HEALTHY", "healthy", "strategies.svg"],
    ["Risk Gateway", "HEALTHY", "healthy", "risk.svg"],
    ["OMS", "Degraded", "attention", "orders.svg"],
    ["Execution Gateway", "HEALTHY", "healthy", "execution.svg"],
    ["Broker Connectivity", "Degraded", "attention", "reconciliation.svg"],
    ["Reconciliation", "Stale", "attention", "audit.svg"],
    ["Telemetry", "Unknown", "unknown", "settings.svg"],
  ],
  pipeline: [
    ["01", "Research", "complete"], ["02", "Backtest", "complete"],
    ["03", "Validate", "complete"], ["04", "Risk Check", "complete"],
    ["05", "Paper Trade", "current"], ["06", "Monitor", "pending"],
    ["07", "Live", "gated"],
  ],
  markets: [
    ["SPY", "575.30", "+0.62%", "positive-a"],
    ["AAPL", "189.64", "-0.18%", "negative"],
    ["MSFT", "428.50", "+0.21%", "positive-b"],
    ["NVDA", "145.27", "+0.45%", "positive-c"],
  ],
} as const;
