import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AwsSettingsContent } from "./AwsSettingsContent";

const mockApiGet = jest.fn();
const mockApiPut = jest.fn();
const mockApiPost = jest.fn();
jest.mock("@/lib/api", () => ({
  api: {
    get: (...args: unknown[]) => mockApiGet(...args),
    put: (...args: unknown[]) => mockApiPut(...args),
    post: (...args: unknown[]) => mockApiPost(...args),
  },
}));

const AWS_CONFIG = {
  aws_account_id: "043671579834",
  aws_region: "us-west-1",
  aws_app_role_arn: "arn:aws:iam::043671579834:role/bioaf-app",
  aws_bootstrap_role_arn: null,
  org_slug: null,
  aws_credential_source: "instance_profile",
  aws_credentials_configured: false,
  aws_validation_status: null,
};

beforeEach(() => {
  mockApiGet.mockReset();
  mockApiPut.mockReset();
  mockApiPost.mockReset();
  mockApiGet.mockResolvedValue(AWS_CONFIG);
  mockApiPut.mockResolvedValue({});
  mockApiPost.mockResolvedValue({ passed: true, checks: [], account_id: "043671579834" });
});

describe("AwsSettingsContent", () => {
  it("renders the AWS configuration fields (parallel to the GCP panel)", async () => {
    render(<AwsSettingsContent />);
    await waitFor(() => expect(mockApiGet).toHaveBeenCalledWith("/api/v1/settings/aws"));

    expect(screen.getByTestId("aws-settings")).toBeInTheDocument();
    expect(screen.getByTestId("aws-account-id-input")).toBeInTheDocument();
    expect(screen.getByTestId("aws-region-select")).toBeInTheDocument();
    expect(screen.getByTestId("aws-bootstrap-role-arn-input")).toBeInTheDocument();
    expect(screen.getByTestId("aws-app-role-arn-input")).toBeInTheDocument();
    expect(screen.getByTestId("aws-org-slug-input")).toBeInTheDocument();
  });

  it("fetches and populates the config from /api/v1/settings/aws on mount", async () => {
    render(<AwsSettingsContent />);
    await waitFor(() => expect(screen.getByTestId("aws-account-id-input")).toHaveValue("043671579834"));
    expect(screen.getByTestId("aws-region-select")).toHaveValue("us-west-1");
    expect(screen.getByTestId("aws-app-role-arn-input")).toHaveValue("arn:aws:iam::043671579834:role/bioaf-app");
  });

  it("populates fields from injected initialConfig without a fetch", () => {
    render(
      <AwsSettingsContent
        initialConfig={{
          aws_account_id: "123456789012",
          aws_region: "eu-west-1",
          aws_app_role_arn: "arn:aws:iam::123456789012:role/bioaf-app",
        }}
      />,
    );
    expect(screen.getByTestId("aws-account-id-input")).toHaveValue("123456789012");
    expect(screen.getByTestId("aws-region-select")).toHaveValue("eu-west-1");
    expect(mockApiGet).not.toHaveBeenCalled();
  });

  it("Save & Validate saves config then validates against the backend", async () => {
    const user = userEvent.setup();
    render(<AwsSettingsContent />);
    await waitFor(() => expect(screen.getByTestId("aws-account-id-input")).toHaveValue("043671579834"));

    await user.type(screen.getByTestId("aws-org-slug-input"), "my-bioaf-org");
    await user.click(screen.getByTestId("save-aws-config-btn"));

    await waitFor(() => expect(mockApiPost).toHaveBeenCalledWith("/api/v1/settings/aws/validate"));
    expect(mockApiPut).toHaveBeenCalledWith(
      "/api/v1/settings/aws",
      expect.objectContaining({ org_slug: "my-bioaf-org", aws_region: "us-west-1" }),
    );
    expect(await screen.findByTestId("aws-validation-results")).toHaveTextContent(/Passed/i);
  });

  it("blocks save on an invalid org slug (client-side)", async () => {
    const user = userEvent.setup();
    render(<AwsSettingsContent />);
    await waitFor(() => expect(screen.getByTestId("aws-account-id-input")).toHaveValue("043671579834"));

    await user.type(screen.getByTestId("aws-org-slug-input"), "Bad_Slug");
    await user.click(screen.getByTestId("save-aws-config-btn"));

    expect(await screen.findByTestId("aws-org-slug-error")).toBeInTheDocument();
    expect(mockApiPut).not.toHaveBeenCalled();
  });
});
