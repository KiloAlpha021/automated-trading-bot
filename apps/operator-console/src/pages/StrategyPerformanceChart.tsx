const strategyPath = "M 0 45 L 35.5 38.70000076293945 L 71 32.400001525878906 L 106.5 35.099998474121094 L 142 26.100000381469727 L 177.5 18 L 213 9 L 248.5 0 L 284 7.199999809265137 L 319.5 22.68000030517578 L 355 16.200000762939453 L 390.5 4.679999828338623";
const benchmarkPath = "M 0 27.899999618530273 L 35.5 24.299999237060547 L 71 20.700000762939453 L 106.5 22.5 L 142 17.100000381469727 L 177.5 12.600000381469727 L 213 9 L 248.5 6.300000190734863 L 284 8.100000381469727 L 319.5 5.400000095367432 L 355 2.700000047683716 L 390.5 0";
const drawdownPath = "M 0 0 L 35.5 0 L 71 0 L 106.5 2.0710058212280273 L 142 0 L 177.5 0 L 213 0 L 248.5 0 L 284 5.333333492279053 L 319.5 16.799999237060547 L 355 12 L 390.5 3.4666666984558105";

export function StrategyPerformanceChart() {
  return <div className="strategy-performance-chart" data-node-id="234:741" aria-label="Illustrative paper performance chart">
    <span className="chart-legend strategy">Strategy</span>
    <span className="chart-legend benchmark">S&amp;P 500 benchmark</span>
    <span className="chart-sample-base">Sample base £25,000</span>
    <svg className="return-chart" viewBox="0 15 431 62" role="img" aria-label="Strategy and S&P 500 benchmark returns">
      <text x="0" y="75">0%</text><text x="0" y="48">3%</text><text x="0" y="21">6%</text>
      <path d="M 40 76 L 431 76" className="chart-grid" />
      <path d="M 40 49 L 431 49" className="chart-grid" />
      <path d="M 40 22 L 431 22" className="chart-grid" />
      <path d={strategyPath} transform="translate(40 31)" className="chart-strategy" data-vector-kind="strategy-return" />
      <path d={benchmarkPath} transform="translate(40 48.1)" className="chart-benchmark" data-vector-kind="benchmark-return" />
    </svg>
    <span className="drawdown-label">Drawdown · max −2.4%</span>
    <svg className="drawdown-chart" viewBox="0 0 431 18" role="img" aria-label="Strategy drawdown, maximum minus 2.4 percent">
      <path d="M 40 0 L 431 0" className="chart-grid" />
      <path d={drawdownPath} transform="translate(40 0)" className="chart-drawdown" data-vector-kind="drawdown" />
    </svg>
    <span className="chart-date start">17 Sep</span>
    <span className="chart-date middle">01 Oct</span>
    <span className="chart-date end">17 Oct</span>
  </div>;
}
