"use client";

import { useState } from "react";
import { AWS_REGIONS, DEFAULT_AWS_REGION } from "@/lib/aws-regions";

// AWS settings panel (stage 8d), the structural parallel of GcpSettingsContent.
//
// The AWS account substrate (S3, EKS, IAM roles, EC2) is provisioned by
// install-aws.sh (stage 7) and the credentials are the EC2 instance profile, so
// this panel does NOT save/validate against a backend yet: the AWS settings +
// validation endpoints land with the AWS provider/install work (stages 6c/7).
// Until then it renders the configuration an AWS install runs on, read from the
// values the installer persisted, with no calls to not-yet-existent endpoints.
interface AwsSettingsContentProps {
  // Injected by the page once the AWS settings endpoint exists; absent today.
  initialConfig?: {
    aws_account_id?: string;
    aws_region?: string;
    aws_bootstrap_role_arn?: string;
    aws_app_role_arn?: string;
    org_slug?: string;
  };
}

export function AwsSettingsContent({ initialConfig }: AwsSettingsContentProps = {}) {
  const [accountId, setAccountId] = useState(initialConfig?.aws_account_id ?? "");
  const [region, setRegion] = useState(initialConfig?.aws_region ?? DEFAULT_AWS_REGION);
  const [bootstrapRoleArn, setBootstrapRoleArn] = useState(initialConfig?.aws_bootstrap_role_arn ?? "");
  const [appRoleArn, setAppRoleArn] = useState(initialConfig?.aws_app_role_arn ?? "");
  const [orgSlug, setOrgSlug] = useState(initialConfig?.org_slug ?? "");

  return (
    <div data-testid="aws-settings" className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-gray-900">AWS Settings</h1>
        <p className="text-sm text-gray-500 mt-1">
          The account, S3 storage, EKS, and IAM roles for this install are provisioned by{" "}
          <code className="bg-gray-100 px-1 py-0.5 rounded">install-aws.sh</code>; the app
          authenticates via the EC2 instance profile.
        </p>
      </div>

      <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 text-sm text-amber-800 max-w-2xl">
        Live save and credential validation arrive with AWS support (Cost Explorer / STS /
        Secrets Manager). The fields below show what this AWS install runs on.
      </div>

      {/* Configuration form */}
      <div className="bg-white rounded-lg shadow p-6 max-w-2xl space-y-5">
        {/* AWS Account ID */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">AWS Account ID</label>
          <input
            data-testid="aws-account-id-input"
            type="text"
            value={accountId}
            onChange={(e) => setAccountId(e.target.value)}
            className="w-full px-3 py-2 border rounded"
            placeholder="123456789012"
          />
        </div>

        {/* Region */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Default Region</label>
          <select
            data-testid="aws-region-select"
            value={region}
            onChange={(e) => setRegion(e.target.value)}
            className="w-full px-3 py-2 border rounded"
          >
            {AWS_REGIONS.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        </div>

        {/* Bootstrap role ARN */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Bootstrap Role ARN
            <span className="ml-1 text-gray-400 font-normal text-xs">(provisioning / Terraform)</span>
          </label>
          <input
            data-testid="aws-bootstrap-role-arn-input"
            type="text"
            value={bootstrapRoleArn}
            onChange={(e) => setBootstrapRoleArn(e.target.value)}
            className="w-full px-3 py-2 border rounded font-mono text-xs"
            placeholder="arn:aws:iam::123456789012:role/bioaf-bootstrap"
          />
        </div>

        {/* App role ARN */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            App Role ARN
            <span className="ml-1 text-gray-400 font-normal text-xs">(runtime / EC2 instance profile)</span>
          </label>
          <input
            data-testid="aws-app-role-arn-input"
            type="text"
            value={appRoleArn}
            onChange={(e) => setAppRoleArn(e.target.value)}
            className="w-full px-3 py-2 border rounded font-mono text-xs"
            placeholder="arn:aws:iam::123456789012:role/bioaf-app"
          />
        </div>

        {/* Org Slug */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Organization Slug
            <span className="ml-1 text-gray-400 font-normal text-xs">(used for S3 bucket names)</span>
          </label>
          <input
            data-testid="aws-org-slug-input"
            type="text"
            value={orgSlug}
            onChange={(e) => setOrgSlug(e.target.value)}
            className="w-full px-3 py-2 border rounded"
            placeholder="my-bioaf-org"
          />
        </div>

        <button
          type="button"
          disabled
          title="AWS validation arrives with AWS support"
          className="px-4 py-2 bg-gray-200 text-gray-500 rounded cursor-not-allowed"
        >
          Validate (coming with AWS support)
        </button>
      </div>
    </div>
  );
}
