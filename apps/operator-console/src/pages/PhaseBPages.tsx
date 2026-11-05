import { FigmaContractPage, type ContractBundle } from "./FigmaContractPage";
import p06Frame from "../figma-contracts/page-06-frame.json";
import p06Text from "../figma-contracts/page-06-text.json";
import p06Vectors from "../figma-contracts/page-06-vectors.json";
import p07Frame from "../figma-contracts/page-07-frame.json";
import p07Text from "../figma-contracts/page-07-text.json";
import p07Vectors from "../figma-contracts/page-07-vectors.json";
import p08Frame from "../figma-contracts/page-08-frame.json";
import p08Text from "../figma-contracts/page-08-text.json";
import p08Vectors from "../figma-contracts/page-08-vectors.json";
import p09Frame from "../figma-contracts/page-09-frame.json";
import p09Text from "../figma-contracts/page-09-text.json";
import p09Vectors from "../figma-contracts/page-09-vectors.json";
import p10Frame from "../figma-contracts/page-10-frame.json";
import p10Text from "../figma-contracts/page-10-text.json";
import p10Vectors from "../figma-contracts/page-10-vectors.json";
import p11Frame from "../figma-contracts/page-11-frame.json";
import p11Text from "../figma-contracts/page-11-text.json";
import p11Vectors from "../figma-contracts/page-11-vectors.json";
import p12Frame from "../figma-contracts/page-12-frame.json";
import p12Text from "../figma-contracts/page-12-text.json";
import p12Vectors from "../figma-contracts/page-12-vectors.json";

const contracts: Record<string, ContractBundle> = {
  "risk-gateway": { frame: p06Frame, text: p06Text, vectors: p06Vectors },
  execution: { frame: p07Frame, text: p07Text, vectors: p07Vectors },
  reconciliation: { frame: p08Frame, text: p08Text, vectors: p08Vectors },
  insights: { frame: p09Frame, text: p09Text, vectors: p09Vectors },
  "system-health": { frame: p10Frame, text: p10Text, vectors: p10Vectors },
  "audit-log": { frame: p11Frame, text: p11Text, vectors: p11Vectors },
  settings: { frame: p12Frame, text: p12Text, vectors: p12Vectors },
};

const titles: Record<string, string> = {
  "risk-gateway": "Risk Gateway",
  execution: "Execution & Broker",
  reconciliation: "Reconciliation",
  insights: "Insights (Research & Analytics)",
  "system-health": "System Health & Infrastructure",
  "audit-log": "Audit Log & Evidence",
  settings: "Settings & Authority",
};

export function PhaseBPage({ slug }: { slug: string }) {
  return <FigmaContractPage bundle={contracts[slug]} slug={slug} title={titles[slug]} />;
}
