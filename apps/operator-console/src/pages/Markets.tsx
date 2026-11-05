import { Fragment } from "react";
import { pages } from "../data";
import { chartNodeId, marketNodes, marketsProvenance } from "./markets.fixtures";
import { MarketsChart } from "./MarketsChart";
import "./markets.css";

const nodes = new Map(marketNodes.map(node => [node.id, node]));
const children = new Map<string, string[]>();
for (const node of marketNodes) children.set(node.parentId, [...(children.get(node.parentId) ?? []), node.id]);
const regionIds = ["205:2042", "205:2043", "205:2044", "205:2045", "205:2046", "205:2047", "205:2048", "205:2049", "205:2050", "205:2051"];
const navigationIds = [1320,1324,1328,1332,1336,1340,1344,1348,1352,1360,1364,1368];

/** A dedicated fixed desktop composition. Each sample retains its frozen source node. */
export function Markets({ navigate, renderSharedChrome = true }: { navigate: (path: string) => void; renderSharedChrome?: boolean }) {
  const renderNode = (id: string): React.ReactNode => {
    const node = nodes.get(id);
    if (!node) return null;
    if (id === chartNodeId) return <MarketsChart key={id} />;
    const content = (children.get(id) ?? []).map(renderNode);
    const common = { "data-figma-node": id, "data-design-sample": true, style: node.style };
    if (node.paths) return <svg key={id} {...common} width={node.width} height={node.height} viewBox={`0 0 ${node.width} ${node.height}`} aria-hidden="true" className="markets-vector">
      {node.paths.map((path,index) => <path key={index} d={path} fill={node.fill} stroke={node.stroke} strokeWidth={node.strokeWidth} strokeLinecap={node.linecap === "round" ? "round" : "butt"} strokeLinejoin={node.linejoin === "round" ? "round" : "miter"} />)}
    </svg>;
    if (node.image) return <img key={id} {...common} src={node.image} className="markets-logo" alt="ATIS" />;
    if (node.text !== undefined) {
      if (id === "205:2053") return <div key={id} {...common}><h1>{node.text}</h1></div>;
      if (node.name === "Title") return <h2 key={id} {...common}>{node.text}</h2>;
      return <span key={id} {...common}>{node.text}</span>;
    }
    const navIndex = navigationIds.indexOf(Number(id.split(";")[1]?.split(":")[1]));
    if (id.startsWith("I205:1982;") && navIndex >= 0) {
      const route = pages[navIndex];
      return <a key={id} {...common} href={`/${route.slug}`} aria-current={route.slug === "markets" ? "page" : undefined} onClick={event => { event.preventDefault(); navigate(`/${route.slug}`); }}>{content}</a>;
    }
    if (regionIds.includes(id)) return <section key={id} {...common} aria-label={node.name.replace("REGION — ", "")} data-market-region={id}>{content}</section>;
    return <div key={id} {...common}>{content}</div>;
  };
  return <div className="markets-page" data-page="markets" data-provenance={marketsProvenance.classification}>
    {renderSharedChrome && <aside aria-label="ATIS console"><nav aria-label="Operator Console pages">{renderNode("205:1982")}</nav></aside>}
    {renderSharedChrome && <header>{renderNode("205:1952")}</header>}
    {regionIds.map(id => <Fragment key={id}>{renderNode(id)}</Fragment>)}
    <p className="markets-accessible-note">PAPER / REPLACEABLE DESIGN DATA. All provider statuses, quotes and timestamps are FIGMA DESIGN SAMPLES ONLY. No live connectivity verified. No trading authority. Chart shapes are illustrative drawing geometry; numeric OHLC history is unavailable. Time-range, research, FX and evidence controls are display-only.</p>
  </div>;
}
