import { render, screen, waitFor } from "@testing-library/react";
import IntegrationsPage from "./page";

// The cloud tab is provider-specific (GCP vs AWS); drive it via useStackOptions.
let mockCloudProvider = "gcp";
jest.mock("@/hooks/useStackOptions", () => ({
  useStackOptions: () => ({
    cloudProvider: mockCloudProvider,
    options: [],
    kubernetesOption: null,
    loading: false,
  }),
}));

// Stub the heavy layout + the settings panels so the test isolates tab gating.
jest.mock("@/components/settings/GcpSettingsContent", () => ({
  GcpSettingsContent: () => <div data-testid="gcp-panel" />,
}));
jest.mock("@/components/settings/AwsSettingsContent", () => ({
  AwsSettingsContent: () => <div data-testid="aws-panel" />,
}));
jest.mock("@/components/settings/SmtpSettingsContent", () => ({
  SmtpSettingsContent: () => <div data-testid="smtp-panel" />,
}));
jest.mock("@/components/settings/SlackSettingsContent", () => ({
  SlackSettingsContent: () => <div data-testid="slack-panel" />,
}));
jest.mock("@/components/settings/LlmSettingsContent", () => ({
  LlmSettingsContent: () => <div data-testid="llm-panel" />,
}));

beforeEach(() => {
  mockCloudProvider = "gcp";
  window.history.pushState({}, "", "/settings/integrations");
});

describe("IntegrationsPage cloud tab gating", () => {
  it("shows the GCP tab + panel on a GCP install", async () => {
    render(<IntegrationsPage />);
    expect(screen.getByRole("button", { name: "GCP" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "AWS" })).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("gcp-panel")).toBeInTheDocument());
    expect(screen.queryByTestId("aws-panel")).not.toBeInTheDocument();
  });

  it("shows the AWS tab + panel on an AWS install (no GCP tab)", async () => {
    mockCloudProvider = "aws";
    render(<IntegrationsPage />);
    expect(screen.getByRole("button", { name: "AWS" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "GCP" })).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("aws-panel")).toBeInTheDocument());
    expect(screen.queryByTestId("gcp-panel")).not.toBeInTheDocument();
  });

  it("keeps the shared non-cloud tabs on both clouds", () => {
    render(<IntegrationsPage />);
    for (const label of ["SMTP", "Slack", "Seqera", "LLMs"]) {
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    }
  });
});
