export function Chart() {
  return (
    <div className="chart" aria-label="Illustrative paper performance chart">
      <svg viewBox="0 0 760 160" role="img" aria-label="Strategy, benchmark and buy and hold sample curves">
        <g className="grid">
          {[20, 55, 90, 125].map((y) => <line key={y} x1="40" y1={y} x2="740" y2={y} />)}
          {[40, 180, 320, 460, 600, 740].map((x) => <line key={x} x1={x} y1="16" x2={x} y2="132" />)}
        </g>
        <polyline className="strategy-line" points="40,118 95,108 150,112 205,93 260,88 315,96 370,72 425,78 480,57 535,65 590,44 645,51 700,29 740,23" />
        <polyline className="benchmark-line" points="40,118 95,113 150,106 205,109 260,99 315,95 370,90 425,84 480,78 535,74 590,66 645,60 700,56 740,51" />
        <polyline className="hold-line" points="40,118 95,116 150,109 205,112 260,105 315,101 370,93 425,91 480,83 535,80 590,72 645,69 700,64 740,59" />
        <text x="40" y="151">Jan 24</text><text x="375" y="151">Jan 25</text><text x="700" y="151">Dec 25</text>
      </svg>
      <div className="legend" aria-label="Chart legend">
        <span><i className="strategy" />Strategy</span><span><i className="benchmark" />SPY proxy</span><span><i className="hold" />Buy &amp; Hold</span>
      </div>
    </div>
  );
}
