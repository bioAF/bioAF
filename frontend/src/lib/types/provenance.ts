// The provenance graph.
// Re-exported by the compatibility barrel `@/lib/types`; import siblings directly, never the barrel.

import type { paths } from "../../../contracts/internal-api";

/** A node of the project provenance graph. Generated transport. */
export type ProvenanceNode = ProvenanceDAG["nodes"][number];

export type ProvenanceEdge = ProvenanceDAG["edges"][number];

/** GET /api/projects/{id}/provenance. Generated transport. */
export type ProvenanceDAG = paths["/api/projects/{project_id}/provenance"]["get"]["responses"][200]["content"]["application/json"];
