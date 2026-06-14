import { render, screen } from "@testing-library/react";
import { AwsSettingsContent } from "./AwsSettingsContent";

describe("AwsSettingsContent", () => {
  it("renders the AWS configuration fields (parallel to the GCP panel)", () => {
    render(<AwsSettingsContent />);

    expect(screen.getByTestId("aws-settings")).toBeInTheDocument();
    expect(screen.getByTestId("aws-account-id-input")).toBeInTheDocument();
    expect(screen.getByTestId("aws-region-select")).toBeInTheDocument();
    expect(screen.getByTestId("aws-bootstrap-role-arn-input")).toBeInTheDocument();
    expect(screen.getByTestId("aws-app-role-arn-input")).toBeInTheDocument();
    expect(screen.getByTestId("aws-org-slug-input")).toBeInTheDocument();
  });

  it("populates fields from the installer-persisted config when provided", () => {
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
    expect(screen.getByTestId("aws-app-role-arn-input")).toHaveValue(
      "arn:aws:iam::123456789012:role/bioaf-app",
    );
  });

  it("does not offer live validation yet (it arrives with AWS support)", () => {
    render(<AwsSettingsContent />);
    expect(screen.getByRole("button", { name: /coming with AWS support/i })).toBeDisabled();
  });
});
