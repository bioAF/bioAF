import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { PipelineInstallNotice } from "./PipelineInstallNotice";

const canAccess = jest.fn();

jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess, roleName: "admin", loading: false, permissions: new Set() }),
}));

jest.mock("@/lib/api", () => ({
  api: { post: jest.fn(), get: jest.fn() },
}));

import { api } from "@/lib/api";

const mockPost = api.post as jest.Mock;

beforeEach(() => {
  mockPost.mockReset();
  mockPost.mockResolvedValue({ pipeline_key: "nf-core/ampliseq" });
  canAccess.mockReset();
  canAccess.mockReturnValue(true);
});

test("says nothing at all when the pipeline is installed", () => {
  const { container } = render(
    <PipelineInstallNotice
      pipelineKey="nf-core/rnaseq"
      pipelineVersion="3.14.0"
      registryName="rnaseq"
      installed={true}
      onInstalled={jest.fn()}
    />,
  );
  expect(container).toBeEmptyDOMElement();
});

test("says nothing when the plan names no pipeline", () => {
  const { container } = render(
    <PipelineInstallNotice
      pipelineKey={null}
      pipelineVersion={null}
      registryName={null}
      installed={null}
      onInstalled={jest.fn()}
    />,
  );
  expect(container).toBeEmptyDOMElement();
});

test("names the missing pipeline before any compute is approved", () => {
  render(
    <PipelineInstallNotice
      pipelineKey="nf-core/ampliseq"
      pipelineVersion="2.9.0"
      registryName="ampliseq"
      installed={false}
      onInstalled={jest.fn()}
    />,
  );
  expect(screen.getByText(/nf-core\/ampliseq 2\.9\.0 is not installed on this bioAF/i)).toBeInTheDocument();
});

test("installing posts the pipeline's own version to the registry install endpoint", async () => {
  const onInstalled = jest.fn();
  render(
    <PipelineInstallNotice
      pipelineKey="nf-core/ampliseq"
      pipelineVersion="2.9.0"
      registryName="ampliseq"
      installed={false}
      onInstalled={onInstalled}
    />,
  );

  await userEvent.click(screen.getByRole("button", { name: /install nf-core\/ampliseq/i }));

  await waitFor(() => expect(mockPost).toHaveBeenCalledTimes(1));
  expect(mockPost).toHaveBeenCalledWith("/api/pipelines/registry/ampliseq/install", { version: "2.9.0" });
  await waitFor(() => expect(onInstalled).toHaveBeenCalled());
});

test("a user who cannot create pipelines is told who can, not given a button that 403s", () => {
  canAccess.mockImplementation((resource: string, action: string) => !(resource === "pipelines" && action === "create"));
  render(
    <PipelineInstallNotice
      pipelineKey="nf-core/ampliseq"
      pipelineVersion="2.9.0"
      registryName="ampliseq"
      installed={false}
      onInstalled={jest.fn()}
    />,
  );
  expect(screen.queryByRole("button", { name: /install/i })).not.toBeInTheDocument();
  expect(screen.getByText(/administrator/i)).toBeInTheDocument();
});

test("a failed install says so in plain words and leaves the button usable", async () => {
  mockPost.mockRejectedValue(new Error("Pipeline 'nf-core/ampliseq' is already installed"));
  render(
    <PipelineInstallNotice
      pipelineKey="nf-core/ampliseq"
      pipelineVersion="2.9.0"
      registryName="ampliseq"
      installed={false}
      onInstalled={jest.fn()}
    />,
  );

  await userEvent.click(screen.getByRole("button", { name: /install nf-core\/ampliseq/i }));

  expect(await screen.findByText(/already installed/i)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /install nf-core\/ampliseq/i })).toBeEnabled();
});

test("a pipeline with no registry name offers no install, only what to do", () => {
  render(
    <PipelineInstallNotice
      pipelineKey="local/custom-thing"
      pipelineVersion="1.0.0"
      registryName={null}
      installed={false}
      onInstalled={jest.fn()}
    />,
  );
  expect(screen.queryByRole("button", { name: /install/i })).not.toBeInTheDocument();
  expect(screen.getByText(/local\/custom-thing 1\.0\.0 is not installed on this bioAF/i)).toBeInTheDocument();
});
