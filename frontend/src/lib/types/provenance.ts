// The provenance graph.
// Re-exported by the compatibility barrel `@/lib/types`; import siblings directly, never the barrel.

import type { components } from "../../../contracts/internal-api";

/** A node of the project provenance graph. Generated transport. */
export type ProvenanceNode = components["schemas"]["ProvenanceNode"];

export type ProvenanceEdge = components["schemas"]["ProvenanceEdge"];

/** GET /api/projects/{id}/provenance. Generated transport. */
export type ProvenanceDAG = components["schemas"]["ProvenanceDAG"];
