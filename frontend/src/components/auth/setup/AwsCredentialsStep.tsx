"use client";

import { Button } from "@/components/ui/Button";
import { AWS_REGIONS } from "@/lib/aws-regions";
import type { AwsCredentials } from "./useCloudCredentials";

/** The AWS credentials step's view; the fields and save-and-validate are the wizard's. */
export function AwsCredentialsStep({
  aws,
  onSave,
  onSkip,
}: {
  aws: AwsCredentials;
  onSave: () => void;
  onSkip: () => void;
}) {
  const {
    awsAccountId,
    setAwsAccountId,
    awsRegion,
    setAwsRegion,
    awsAppRoleArn,
    setAwsAppRoleArn,
    awsOrgSlug,
    setAwsOrgSlug,
    awsCredentialSource,
    awsPrefilled,
    awsSaving,
    awsValidation,
  } = aws;
  return (
    <div className="space-y-4">
      <p className="text-sm text-gray-600 mb-2">
        Confirm the AWS account this install runs on. The app authenticates through
        the EC2 instance profile, so there is nothing to paste here: we validate that
        the ambient credentials resolve before deploying a compute stack.
      </p>

      {awsPrefilled && (
        <div className="bg-blue-50 border border-blue-200 rounded p-3 text-xs text-blue-900">
          <p className="font-medium mb-1">Pre-populated from install-aws.sh</p>
          <p>
            We&apos;ve filled in the account, region, and role from the AWS installer
            you just ran. You can change anything here, but the defaults match what
            we just created.
          </p>
        </div>
      )}

      <div>
        <label htmlFor="aws-account-id" className="block text-sm font-medium text-gray-700 mb-1">AWS Account ID</label>
        <input id="aws-account-id" type="text" value={awsAccountId}
          onChange={(e) => setAwsAccountId(e.target.value)} placeholder="123456789012"
          className="w-full px-3 py-2 border rounded focus:ring-2 focus:ring-bioaf-500" />
      </div>

      <div>
        <label htmlFor="aws-region" className="block text-sm font-medium text-gray-700 mb-1">Region</label>
        <select id="aws-region" value={awsRegion} onChange={(e) => setAwsRegion(e.target.value)}
          className="w-full px-3 py-2 border rounded focus:ring-2 focus:ring-bioaf-500">
          {AWS_REGIONS.map((r) => <option key={r} value={r}>{r}</option>)}
        </select>
      </div>

      <div>
        <label htmlFor="aws-org-slug" className="block text-sm font-medium text-gray-700 mb-1">
          Organization Slug
          <span className="ml-1 text-gray-500 font-normal text-xs">(3-30 chars, lowercase, hyphens allowed)</span>
        </label>
        <input id="aws-org-slug" type="text" value={awsOrgSlug} onChange={(e) => setAwsOrgSlug(e.target.value)}
          placeholder="my-bioaf-org" className="w-full px-3 py-2 border rounded focus:ring-2 focus:ring-bioaf-500" />
      </div>

      <div>
        <label htmlFor="aws-app-role-arn" className="block text-sm font-medium text-gray-700 mb-1">
          App Role ARN
          <span className="ml-1 text-gray-500 font-normal text-xs">(runtime / EC2 instance profile)</span>
        </label>
        <input id="aws-app-role-arn" type="text" value={awsAppRoleArn}
          onChange={(e) => setAwsAppRoleArn(e.target.value)}
          placeholder="arn:aws:iam::123456789012:role/bioaf-app"
          className="w-full px-3 py-2 border rounded font-mono text-xs focus:ring-2 focus:ring-bioaf-500" />
      </div>

      <div className="bg-gray-50 border rounded p-3 text-xs text-gray-600">
        Authentication: <code className="bg-white px-1 rounded">{awsCredentialSource}</code>. The app reads the
        EC2 instance profile via IMDS; no access key is stored. install-aws.sh provisioned the role.
      </div>

      <Button className="w-full" onClick={onSave} disabled={awsSaving}>
        {awsSaving ? "Validating..." : "Save & Validate"}
      </Button>

      {awsValidation && !awsValidation.passed && (
        <div className="border rounded divide-y text-sm">
          <div className="p-3 bg-red-50">
            <h4 className="font-semibold text-red-800">Validation Failed</h4>
          </div>
          <div className="p-3 space-y-1.5">
            {awsValidation.checks.map((c) => (
              <div key={c.name} className="flex items-start gap-2 text-xs">
                <span className={c.passed ? "text-green-600" : "text-red-600"}>
                  {c.passed ? "✓" : "✗"}
                </span>
                <span>
                  <span className="font-medium">{c.name}</span>{" "}
                  <span className="text-gray-500">{c.message}</span>
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      <button onClick={onSkip} className="w-full text-gray-500 text-sm hover:text-gray-700">
        Do this later
      </button>
    </div>
  );
}
