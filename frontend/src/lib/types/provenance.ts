// The provenance graph.
// Re-exported by the compatibility barrel `@/lib/types`; import siblings directly, never the barrel.

export interface ProvenanceNode {
  id: string;
  type: "experiment" | "sample" | "pipeline_run" | "snapshot" | "reference" | "file" | "project";
  label: string;
  metadata: Record<string, unknown>;
}

export interface ProvenanceEdge {
  source: string;
  target: string;
  relationship: string;
}

export interface ProvenanceDAG {
  nodes: ProvenanceNode[];
  edges: ProvenanceEdge[];
}
