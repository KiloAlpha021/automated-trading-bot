export const positions = [
  ["AAPL","50","£134.00","£140.00","£7,000.00","+£300.00","Momentum Strategy","Technology","WATCH"],
  ["MSFT","30","£191.6667","£200.00","£6,000.00","+£250.00","Mean Reversion","Technology","NORMAL"],
  ["NVDA","20","£192.50","£200.00","£4,000.00","+£150.00","Volatility Breakout","Technology","REDUCING"],
  ["TSLA","10","£340.00","£350.00","£3,500.00","+£100.00","Statistical Arbitrage","Consumer Cyclical","NORMAL"],
  ["AMD","20","£121.00","£125.00","£2,500.00","+£80.00","Trend Strategy","Technology","NORMAL"],
  ["META","10","£288.00","£300.00","£3,000.00","+£120.00","Momentum Strategy","Communication","NORMAL"],
  ["GOOGL","10","£241.00","£250.00","£2,500.00","+£90.00","Mean Reversion","Communication","WATCH"],
  ["PLTR","100","£22.2027","£23.6483","£2,364.83","+£144.56","Statistical Arbitrage","Technology","RESTRICTED"],
] as const;
export const exposure = [["Momentum Strategy","£10,000.00","32.40%"],["Mean Reversion","£8,500.00","27.54%"],["Statistical Arbitrage","£5,864.83","19.00%"],["Volatility Breakout","£4,000.00","12.96%"],["Trend Strategy","£2,500.00","8.10%"]] as const;
export const risk = [["Gross Exposure","Not established","CONFIG SAMPLE"],["Net Exposure","Not established","CONFIG SAMPLE"],["Largest Position","AAPL · £7,000.00 · 22.68%","HOLDINGS DERIVED"],["Sector Concentration","Technology · 70.84%","HOLDINGS DERIVED"],["Daily Loss","Not established","CONFIG SAMPLE"],["Max Drawdown","−0.66% observed · 1M","PERFORMANCE DERIVED"],["Leverage","Not established","CONFIG SAMPLE"]] as const;
