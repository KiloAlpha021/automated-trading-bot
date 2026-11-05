import type { CSSProperties, ReactNode } from "react";

type Paint = { type: string; visible?: boolean; opacity?: number; color?: { r: number; g: number; b: number }; blendMode?: string };
type Effect = { type: string; visible?: boolean; radius?: number; spread?: number; offset?: { x: number; y: number }; color?: { r: number; g: number; b: number; a?: number } };
type Bounds = { x: number; y: number; width: number; height: number };
type ContractNode = {
  id: string; parentId?: string; name?: string; type: string; visible?: boolean; opacity?: number; blendMode?: string;
  absoluteBoundingBox?: Bounds; fills?: Paint[]; strokes?: Paint[]; strokeWeight?: number;
  cornerRadius?: number; topLeftRadius?: number; topRightRadius?: number; bottomRightRadius?: number; bottomLeftRadius?: number;
  effects?: Effect[]; rotation?: number; clipsContent?: boolean;
};
type TextNode = {
  id: string; characters: string; fontName?: { family?: string; style?: string }; fontSize?: number; fontWeight?: number;
  lineHeight?: { unit?: string; value?: number }; letterSpacing?: { unit?: string; value?: number };
  textAlignHorizontal?: string; textAlignVertical?: string; textAutoResize?: string; textCase?: string; textDecoration?: string; fills?: Paint[];
};
type VectorNode = { id: string; vectorPaths?: { data: string; windingRule?: string }[]; fills?: Paint[]; strokes?: Paint[]; strokeWeight?: number; width?: number; height?: number };
type FrameContract = { frame: { id: string }; nodes: ContractNode[] };
type TextContract = { texts: TextNode[] };
type VectorContract = { vectors: VectorNode[] };

export type ContractBundle = { frame: unknown; text: unknown; vectors: unknown };

const channel = (value = 0) => Math.round(value * 255);
const paintColor = (paint?: Paint) => paint?.color
  ? `rgba(${channel(paint.color.r)},${channel(paint.color.g)},${channel(paint.color.b)},${paint.opacity ?? 1})`
  : undefined;

function visualStyle(node: ContractNode, text?: TextNode): CSSProperties {
  const fill = (text?.fills ?? node.fills)?.find((item) => item.visible !== false && item.type === "SOLID");
  const stroke = node.strokes?.find((item) => item.visible !== false && item.type === "SOLID");
  const shadow = node.effects?.find((item) => item.visible !== false && item.type === "DROP_SHADOW");
  const radius = node.cornerRadius ?? 0;
  return {
    position: "absolute",
    opacity: node.opacity ?? 1,
    background: node.type === "TEXT" || node.type === "VECTOR" ? undefined : paintColor(fill),
    color: node.type === "TEXT" ? paintColor(fill) : undefined,
    border: stroke && node.type !== "VECTOR" && node.type !== "LINE" ? `${node.strokeWeight ?? 1}px solid ${paintColor(stroke)}` : undefined,
    borderRadius: node.type === "ELLIPSE" ? "50%" : radius || node.topLeftRadius
      ? `${node.topLeftRadius ?? radius}px ${node.topRightRadius ?? radius}px ${node.bottomRightRadius ?? radius}px ${node.bottomLeftRadius ?? radius}px`
      : undefined,
    boxShadow: shadow ? `${shadow.offset?.x ?? 0}px ${shadow.offset?.y ?? 0}px ${shadow.radius ?? 0}px ${shadow.spread ?? 0}px ${paintColor({ type: "SOLID", color: shadow.color, opacity: shadow.color?.a ?? 1 })}` : undefined,
    mixBlendMode: node.blendMode && !["NORMAL", "PASS_THROUGH"].includes(node.blendMode) ? node.blendMode.toLowerCase() as CSSProperties["mixBlendMode"] : undefined,
    overflow: node.clipsContent ? "hidden" : undefined,
    transform: node.rotation ? `rotate(${node.rotation}deg)` : undefined,
    transformOrigin: "center",
  };
}

function textStyle(text: TextNode): CSSProperties {
  const horizontal = text.textAlignHorizontal ?? "LEFT";
  return {
    fontFamily: `${text.fontName?.family ?? "Inter"}, sans-serif`,
    fontSize: text.fontSize,
    fontWeight: text.fontWeight,
    lineHeight: text.lineHeight?.unit === "PIXELS" ? `${text.lineHeight.value}px` : undefined,
    letterSpacing: text.letterSpacing?.unit === "PIXELS" ? `${text.letterSpacing.value}px` : undefined,
    textAlign: horizontal.toLowerCase() as CSSProperties["textAlign"],
    textTransform: text.textCase === "UPPER" ? "uppercase" : undefined,
    textDecoration: text.textDecoration === "UNDERLINE" ? "underline" : undefined,
    whiteSpace: text.textAutoResize === "WIDTH_AND_HEIGHT" ? "pre" : "pre-wrap",
    display: "flex",
    justifyContent: horizontal === "RIGHT" ? "flex-end" : horizontal === "CENTER" ? "center" : "flex-start",
    alignItems: text.textAlignVertical === "CENTER" ? "center" : text.textAlignVertical === "BOTTOM" ? "flex-end" : "flex-start",
    overflowWrap: "normal",
    wordBreak: "normal",
  };
}

function contractTree(contract: FrameContract) {
  const root = contract.frame.id;
  const children = new Map<string, ContractNode[]>();
  contract.nodes.forEach((node) => {
    if (!node.parentId) return;
    const list = children.get(node.parentId) ?? [];
    list.push(node);
    children.set(node.parentId, list);
  });
  const top = children.get(root) ?? [];
  const excluded = new Set(top.filter((node) => /SHELL|Global Search/i.test((node as ContractNode & { name?: string }).name ?? "")).map((node) => node.id));
  return { top: top.filter((node) => !excluded.has(node.id)), children };
}

export function FigmaContractPage({ bundle, slug, title }: { bundle: ContractBundle; slug: string; title: string }) {
  const frame = bundle.frame as FrameContract;
  const texts = new Map((bundle.text as TextContract).texts.map((item) => [item.id, item]));
  const vectors = new Map((bundle.vectors as VectorContract).vectors.map((item) => [item.id, item]));
  const tree = contractTree(frame);

  const renderNode = (node: ContractNode, parentBounds: Bounds, parentStroke = 0): ReactNode => {
    if (node.visible === false || !node.absoluteBoundingBox) return null;
    const bounds = node.absoluteBoundingBox;
    const text = texts.get(node.id);
    const layout: CSSProperties = {
      ...visualStyle(node, text),
      left: bounds.x - parentBounds.x - parentStroke,
      top: bounds.y - parentBounds.y - parentStroke,
      width: Math.max(0, bounds.width),
      height: Math.max(0, bounds.height),
    };
    const childNodes = tree.children.get(node.id) ?? [];
    if (node.type === "TEXT" && text) return <div key={node.id} data-figma-id={node.id} style={{ ...layout, ...textStyle(text) }}><span>{text.characters}</span></div>;
    const vector = vectors.get(node.id);
    if (vector?.vectorPaths?.length) {
      const fill = vector.fills?.find((item) => item.visible !== false);
      const stroke = vector.strokes?.find((item) => item.visible !== false);
      return <svg key={node.id} data-figma-id={node.id} style={{ ...layout, overflow: "visible" }} viewBox={`0 0 ${vector.width ?? bounds.width} ${vector.height ?? bounds.height}`} aria-hidden="true">
        {vector.vectorPaths.map((path, index) => <path key={index} d={path.data} fill={paintColor(fill) ?? "none"} stroke={paintColor(stroke) ?? "none"} strokeWidth={vector.strokeWeight ?? 1} fillRule={path.windingRule === "EVENODD" ? "evenodd" : "nonzero"} />)}
      </svg>;
    }
    if (node.type === "LINE") {
      const stroke = node.strokes?.find((item) => item.visible !== false);
      return <div key={node.id} data-figma-id={node.id} style={{ ...layout, height: node.strokeWeight ?? 1, background: paintColor(stroke) }} />;
    }
    const inset = node.strokes?.some((item) => item.visible !== false) ? node.strokeWeight ?? 1 : 0;
    return <div key={node.id} data-figma-id={node.id} style={layout}>{childNodes.map((child) => renderNode(child, bounds, inset))}</div>;
  };

  return <section className={`figma-contract-page figma-page-${slug}`} data-page={slug}>
    <h1>{title}</h1>
    <p className="sr-only">PAPER / REPLACEABLE DESIGN DATA</p>
    {tree.top.map((node) => renderNode(node, { x: 196, y: 84, width: 1340, height: 940 }))}
  </section>;
}
