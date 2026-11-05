export type OrderRecord = {
  time: string; id: string; symbol: string; side: "BUY" | "SELL";
  quantity: string; filled: string; type: "LIMIT" | "MARKET";
  oms: string; broker: string; strategy: string;
};

export const orderMetrics = [
  ["Total Orders", "6", "cyan"], ["Open Orders", "3", "amber"],
  ["Filled Orders", "1", "green"], ["Rejected Orders", "1", "red"],
  ["Cancelled Orders", "1", "muted"],
] as const;

export const orders: OrderRecord[] = [
  { time:"15:52", id:"OMS-P-0001", symbol:"MSFT", side:"BUY", quantity:"100", filled:"0", type:"LIMIT", oms:"PENDING", broker:"NOT SENT", strategy:"Mean Reversion" },
  { time:"15:41", id:"OMS-P-0002", symbol:"NVDA", side:"SELL", quantity:"80", filled:"0", type:"LIMIT", oms:"ACCEPTED", broker:"ACKNOWLEDGED", strategy:"Volatility Breakout" },
  { time:"15:28", id:"OMS-P-0003", symbol:"AAPL", side:"BUY", quantity:"120", filled:"45", type:"LIMIT", oms:"PARTIALLY_FILLED", broker:"ACKNOWLEDGED", strategy:"Momentum Strategy" },
  { time:"15:04", id:"OMS-P-0004", symbol:"TSLA", side:"SELL", quantity:"50", filled:"50", type:"MARKET", oms:"FILLED", broker:"ACKNOWLEDGED", strategy:"Statistical Arbitrage" },
  { time:"14:47", id:"OMS-P-0005", symbol:"AMD", side:"BUY", quantity:"60", filled:"0", type:"LIMIT", oms:"REJECTED", broker:"REJECTED", strategy:"Trend Strategy" },
  { time:"14:22", id:"OMS-P-0006", symbol:"META", side:"SELL", quantity:"90", filled:"20", type:"LIMIT", oms:"CANCELLED", broker:"ACKNOWLEDGED", strategy:"Momentum Strategy" },
];

export const selectedFields = [
  ["OMS Order ID","OMS-P-0003"], ["Limit Price","$190.00"],
  ["Symbol","AAPL"], ["OMS Status","PARTIALLY_FILLED"],
  ["Side","BUY"], ["Broker Status","ACKNOWLEDGED"],
  ["Order Type","LIMIT"], ["Strategy","Momentum Strategy"],
  ["Ordered Quantity","120"], ["Portfolio Intent ID","INT-P-0003"],
  ["Filled Quantity","45"], ["Risk Evaluation ID","RISK-P-0003"],
  ["Remaining Open","75"], ["Broker Order ID","BRK-P-0003"],
] as const;

export const lifecycle = [
  ["1","Risk Gateway","PASS","15:27:48 BST · RISK-P-0003","pass"],
  ["2","OMS","ORDER CREATED","15:27:52 BST · OMS-P-0003","created"],
  ["3","Broker","ACKNOWLEDGED","15:27:55 BST · BRK-P-0003","acknowledged"],
  ["4","Execution","45 SHARES FILLED","15:28:00 BST · FILL-P-0003-01","partial"],
  ["5","Remaining Open","75 SHARES","Current state · not a new event","remaining"],
] as const;

export const evidenceRows = [
  ["Risk Evaluation","RISK-P-0003","Synthetic Paper PASS","pass"],
  ["OMS Record","OMS-P-0003","PARTIALLY_FILLED","partial"],
  ["Broker Ack","BRK-P-0003","ACKNOWLEDGED","acknowledged"],
  ["Fill Record","FILL-P-0003-01","45 shares @ $189.82","partial"],
  ["Evidence Ref","EV-P-0003","15:28 BST · SYNTHETIC","muted"],
] as const;
