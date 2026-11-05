import { chartNodeId, marketNodes } from "./markets.fixtures";

/** Frozen drawing primitives only: no inferred OHLC, volume or temporal series. */
export function MarketsChart() {
  const root = marketNodes.find(node => node.id === chartNodeId)!;
  const primitives = marketNodes.filter(node => node.parentId === chartNodeId);
  return <figure className="markets-chart" data-figma-node={chartNodeId} style={root.style} aria-label="AAPL illustrative chart. Static design geometry; underlying OHLC records are not supplied.">
    <svg width="940" height="180" viewBox="0 0 940 180" aria-hidden="true">
      {primitives.filter(node => node.type !== "TEXT").map(node => <rect key={node.id} data-figma-node={node.id} data-primitive={node.name} x={node.x} y={node.y} width={node.width} height={node.height} fill={String(node.style.background ?? "none")} />)}
    </svg>
    {primitives.filter(node => node.type === "TEXT").map(node => <span key={node.id} data-figma-node={node.id} style={node.style}>{node.text}</span>)}
  </figure>;
}
