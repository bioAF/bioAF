"use client";

import { useEffect, useRef, useMemo } from "react";
import { useRouter } from "next/navigation";
import dagre from "@dagrejs/dagre";
import type { ProvenanceDAG as ProvenanceDAGType, ProvenanceNode, ProvenanceEdge } from "@/lib/types";

interface Props {
  data: ProvenanceDAGType;
}

const NODE_COLORS: Record<ProvenanceNode["type"], { fill: string; stroke: string; text: string }> = {
  project: { fill: "#dbeafe", stroke: "#3b82f6", text: "#1e40af" },
  experiment: { fill: "#fef3c7", stroke: "#f59e0b", text: "#92400e" },
  sample: { fill: "#d1fae5", stroke: "#10b981", text: "#065f46" },
  pipeline_run: { fill: "#ede9fe", stroke: "#8b5cf6", text: "#5b21b6" },
  snapshot: { fill: "#fce7f3", stroke: "#ec4899", text: "#9d174d" },
  reference: { fill: "#f3f4f6", stroke: "#6b7280", text: "#374151" },
  file: { fill: "#e0e7ff", stroke: "#6366f1", text: "#3730a3" },
};

const NODE_DIMENSIONS: Record<ProvenanceNode["type"], { width: number; height: number }> = {
  project: { width: 180, height: 50 },
  experiment: { width: 160, height: 40 },
  sample: { width: 120, height: 36 },
  pipeline_run: { width: 180, height: 40 },
  snapshot: { width: 140, height: 40 },
  reference: { width: 160, height: 40 },
  file: { width: 100, height: 30 },
};

const EDGE_STYLES: Record<string, string> = {
  contains: "4,0",          // solid
  input_to: "4,0",          // solid
  produced: "4,0",          // solid
  used_reference: "6,3",    // dashed
  captured_at: "2,3",       // dotted
};

function getNodePath(type: ProvenanceNode["type"], x: number, y: number, w: number, h: number): string {
  const hw = w / 2;
  const hh = h / 2;
  switch (type) {
    case "sample":
      // Circle (approximated as ellipse path)
      return `M ${x} ${y - hh} A ${hw} ${hh} 0 1 1 ${x} ${y + hh} A ${hw} ${hh} 0 1 1 ${x} ${y - hh} Z`;
    case "snapshot":
      // Diamond
      return `M ${x} ${y - hh} L ${x + hw} ${y} L ${x} ${y + hh} L ${x - hw} ${y} Z`;
    case "reference": {
      // Hexagon
      const hx = hw * 0.75;
      return `M ${x - hx} ${y - hh} L ${x + hx} ${y - hh} L ${x + hw} ${y} L ${x + hx} ${y + hh} L ${x - hx} ${y + hh} L ${x - hw} ${y} Z`;
    }
    case "pipeline_run":
      // Rounded rectangle (using rect with rx/ry is easier, but for path consistency)
      return `M ${x - hw + 8} ${y - hh} L ${x + hw - 8} ${y - hh} Q ${x + hw} ${y - hh} ${x + hw} ${y - hh + 8} L ${x + hw} ${y + hh - 8} Q ${x + hw} ${y + hh} ${x + hw - 8} ${y + hh} L ${x - hw + 8} ${y + hh} Q ${x - hw} ${y + hh} ${x - hw} ${y + hh - 8} L ${x - hw} ${y - hh + 8} Q ${x - hw} ${y - hh} ${x - hw + 8} ${y - hh} Z`;
    case "file":
      // Small circle
      return `M ${x} ${y - hh} A ${hw} ${hh} 0 1 1 ${x} ${y + hh} A ${hw} ${hh} 0 1 1 ${x} ${y - hh} Z`;
    default:
      // Rectangle (project, experiment)
      return `M ${x - hw} ${y - hh} L ${x + hw} ${y - hh} L ${x + hw} ${y + hh} L ${x - hw} ${y + hh} Z`;
  }
}

function getNavigationUrl(node: ProvenanceNode): string | null {
  const [type, id] = node.id.split(":");
  switch (type) {
    case "project": return `/projects/${id}`;
    case "experiment": return `/experiments/${id}`;
    case "pipeline_run": return `/pipelines/runs/${id}`;
    case "reference": return `/references/${id}`;
    default: return null;
  }
}

export function ProvenanceDAGComponent({ data }: Props) {
  const svgRef = useRef<SVGSVGElement>(null);
  const router = useRouter();

  const layout = useMemo(() => {
    if (data.nodes.length === 0) return null;

    const g = new dagre.graphlib.Graph();
    g.setGraph({ rankdir: "TB", nodesep: 40, ranksep: 60, marginx: 20, marginy: 20 });
    g.setDefaultEdgeLabel(() => ({}));

    const nodeMap = new Map<string, ProvenanceNode>();
    for (const node of data.nodes) {
      nodeMap.set(node.id, node);
      const dims = NODE_DIMENSIONS[node.type] || { width: 140, height: 40 };
      g.setNode(node.id, { width: dims.width, height: dims.height });
    }

    for (const edge of data.edges) {
      g.setEdge(edge.source, edge.target);
    }

    dagre.layout(g);

    const laidOutNodes = data.nodes.map((node) => {
      const layoutNode = g.node(node.id);
      return {
        ...node,
        x: layoutNode.x,
        y: layoutNode.y,
        width: layoutNode.width,
        height: layoutNode.height,
      };
    });

    const laidOutEdges = data.edges.map((edge) => {
      const edgeLayout = g.edge(edge.source, edge.target);
      return {
        ...edge,
        points: edgeLayout?.points || [],
      };
    });

    const graphLabel = g.graph();
    return {
      nodes: laidOutNodes,
      edges: laidOutEdges,
      width: (graphLabel.width || 600) + 40,
      height: (graphLabel.height || 400) + 40,
    };
  }, [data]);

  if (!layout || data.nodes.length === 0) {
    return (
      <div className="text-gray-400 text-center py-8">
        No provenance data to display.
      </div>
    );
  }

  const handleNodeClick = (node: ProvenanceNode) => {
    const url = getNavigationUrl(node);
    if (url) router.push(url);
  };

  return (
    <div className="overflow-auto border border-gray-200 rounded-lg bg-gray-50">
      <svg
        ref={svgRef}
        width={layout.width}
        height={layout.height}
        viewBox={`0 0 ${layout.width} ${layout.height}`}
        className="min-w-full"
      >
        <defs>
          <marker
            id="arrowhead"
            markerWidth="8"
            markerHeight="6"
            refX="8"
            refY="3"
            orient="auto"
          >
            <path d="M 0 0 L 8 3 L 0 6 Z" fill="#9ca3af" />
          </marker>
        </defs>

        {/* Edges */}
        {layout.edges.map((edge, i) => {
          const points = edge.points;
          if (points.length < 2) return null;
          const d = points
            .map((p: { x: number; y: number }, j: number) =>
              j === 0 ? `M ${p.x} ${p.y}` : `L ${p.x} ${p.y}`
            )
            .join(" ");
          const dasharray = EDGE_STYLES[edge.relationship] || "4,0";
          return (
            <g key={`edge-${i}`}>
              <path
                d={d}
                fill="none"
                stroke="#9ca3af"
                strokeWidth={1.5}
                strokeDasharray={dasharray}
                markerEnd="url(#arrowhead)"
              />
            </g>
          );
        })}

        {/* Nodes */}
        {layout.nodes.map((node) => {
          const colors = NODE_COLORS[node.type];
          const isDeprecated = node.metadata?.status === "deprecated";
          const navigable = getNavigationUrl(node) !== null;

          return (
            <g
              key={node.id}
              onClick={() => handleNodeClick(node)}
              className={navigable ? "cursor-pointer" : ""}
            >
              <path
                d={getNodePath(node.type, node.x, node.y, node.width, node.height)}
                fill={colors.fill}
                stroke={isDeprecated ? "#d1d5db" : colors.stroke}
                strokeWidth={isDeprecated ? 1 : 2}
                strokeDasharray={isDeprecated ? "4,2" : undefined}
                opacity={isDeprecated ? 0.6 : 1}
              />
              <text
                x={node.x}
                y={node.y}
                textAnchor="middle"
                dominantBaseline="central"
                fill={colors.text}
                fontSize={11}
                fontWeight={node.type === "project" ? "bold" : "normal"}
                className="select-none"
              >
                {node.label.length > 22 ? node.label.slice(0, 20) + "…" : node.label}
              </text>
            </g>
          );
        })}
      </svg>

      {/* Legend */}
      <div className="flex flex-wrap gap-4 p-3 border-t border-gray-200 text-xs text-gray-500">
        {Object.entries(NODE_COLORS).map(([type, colors]) => (
          <div key={type} className="flex items-center gap-1">
            <div
              className="w-3 h-3 rounded-sm border"
              style={{ backgroundColor: colors.fill, borderColor: colors.stroke }}
            />
            <span>{type.replace("_", " ")}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
