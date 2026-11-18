/**
 * A deployed cluster whose live status can't be read keeps its Cluster Status card, with a plain
 * reason, instead of disappearing.
 */
import { render, screen } from "@/testing/renderWithProviders";

jest.mock("@/lib/api", () => ({ ...jest.requireActual("@/lib/api"), api: { get: jest.fn(), post: jest.fn() } }));
jest.mock("@/hooks/useCapabilities", () => ({
  useCapabilities: () => ({ has: () => false, capabilities: {}, loading: false }),
}));
jest.mock("@/hooks/useInfrastructureActions", () => ({
  useInfrastructureActions: () => ({
    allowed: () => true,
    stillAllowed: () => true,
    reason: () => null,
  }),
}));

import { ClusterStatusCard } from "./ClusterStatusCard";
import type { StackStatus } from "./infrastructureTypes";

function status(cluster_unavailable: StackStatus["cluster_unavailable"]): StackStatus {
  return {
    compute_stack: "kubernetes",
    compute_deployed: true,
    storage_deployed: true,
    pubsub_configured: true,
    cluster: null,
    has_orphaned_clusters: false,
    cluster_unavailable,
  };
}

const configRead = { loading: false, failed: false, stale: false, retry: jest.fn() };

test.each([
  [
    "cluster_location_unknown",
    "bioAF cannot find this cluster in Google Cloud, so its status is not shown. Ask an administrator to re-apply the compute stack.",
  ],
  [
    "provider_denied",
    "Google Cloud refused bioAF's request for this cluster's status. Ask an administrator to review bioAF's cloud access.",
  ],
  [
    "provider_unavailable",
    "bioAF could not reach Google Cloud for this cluster's status. Try again in a moment.",
  ],
] as const)("%s keeps the card with a plain reason", (reason, message) => {
  render(
    <ClusterStatusCard stackStatus={status(reason)} clusterConfig={null} configRead={configRead} onApplied={jest.fn()} />,
  );
  expect(screen.getByRole("heading", { name: "Cluster Status" })).toBeInTheDocument();
  expect(screen.getByTestId("cluster-unavailable")).toHaveTextContent(message);
});

test("no reason, no message", () => {
  render(
    <ClusterStatusCard stackStatus={status(null)} clusterConfig={null} configRead={configRead} onApplied={jest.fn()} />,
  );
  expect(screen.queryByTestId("cluster-unavailable")).not.toBeInTheDocument();
});
