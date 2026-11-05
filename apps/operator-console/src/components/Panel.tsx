import type { DisplayState, Metric, PanelDefinition, Tone } from "../data";
import { Chart } from "./Chart";

const explicitStates: Record<string, DisplayState> = {
  HEALTHY: "healthy", CURRENT: "healthy", VERIFIED: "healthy", CONNECTED: "healthy", COMPLETE: "healthy", PASS: "healthy", VALID: "healthy", STORED: "healthy", APPLIED: "healthy", AVAILABLE: "healthy", RECORDED: "healthy", SUCCESS: "healthy", FILLED: "healthy", ACKNOWLEDGED: "healthy",
  DEGRADED: "attention", STALE: "attention", PENDING: "attention", REVIEW: "attention", "IN REVIEW": "attention", ATTENTION: "attention", PARTIAL: "attention", PARTIALLY_FILLED: "attention", OPEN: "attention", HELD: "attention",
  UNKNOWN: "unknown", UNVERIFIED: "unknown", "NOT VERIFIED": "unknown", "NOT ESTABLISHED": "unknown", MISSING: "unknown", INSUFFICIENT: "unknown",
  RESTRICTED: "restricted", BLOCKED: "restricted", BLOCK: "restricted", REJECTED: "restricted", FAILED: "restricted", EXCEPTION: "restricted", UNAUTHORISED: "restricted", "NOT AUTHORISED": "restricted", CANCELLED: "restricted",
};

export function toneFor(value: string, tone?: Tone): Tone {
  if (tone) return tone;
  const normalized = value.trim().toUpperCase();
  if (explicitStates[normalized]) return explicitStates[normalized];
  return "info";
}

function MetricCard({ metric }: { metric: Metric }) {
  return <div className={`metric tone-${toneFor(metric.value, metric.tone)}`}><span>{metric.label}</span><strong>{metric.value}</strong>{metric.detail && <small>{metric.detail}</small>}</div>;
}

export function Panel({ panel }: { panel: PanelDefinition }) {
  return (
    <section className={`panel ${panel.className ?? "span-4"}`}>
      <div className="panel-heading"><div>{panel.eyebrow && <small>{panel.eyebrow}</small>}<h2>{panel.title}</h2></div><span className="panel-menu" aria-hidden="true">•••</span></div>
      {panel.kind === "metrics" && <div className="metrics-grid">{panel.metrics?.map((metric) => <MetricCard key={metric.label} metric={metric} />)}</div>}
      {panel.kind === "list" && <ul className="data-list">{panel.items?.map((item, index) => <li key={item}><span className={`state-dot tone-${panel.rowStates?.[index] ?? "info"}`} />{item}</li>)}</ul>}
      {panel.kind === "trace" && <ol className="trace">{panel.items?.map((item, index) => <li key={item}><b>{index + 1}</b><span>{item}</span></li>)}</ol>}
      {panel.kind === "table" && <div className="table-wrap"><table><thead><tr>{panel.columns?.map((column) => <th key={column}>{column}</th>)}</tr></thead><tbody>{panel.rows?.map((row, index) => <tr key={`${panel.title}-${index}`}>{panel.columns?.map((column) => <td key={column}><span className={/status|state|outcome|assurance|integrity/i.test(column) ? `status tone-${toneFor(row[column] ?? "")}` : ""}>{row[column]}</span></td>)}</tr>)}</tbody></table></div>}
      {panel.kind === "chart" && <><Chart /><div className="metrics-inline">{panel.metrics?.map((metric) => <MetricCard key={metric.label} metric={metric} />)}</div></>}
      {panel.note && <p className="panel-note">{panel.note}</p>}
    </section>
  );
}
