"use client";

import { Button } from "@/components/ui/Button";

/** Setup's account steps. Views only: the wizard owns the values and the requests. */

export function SetupCodeStep({
  setupCode,
  setSetupCode,
  onVerify,
}: {
  setupCode: string;
  setSetupCode: (value: string) => void;
  onVerify: () => void;
}) {
  return (
    <div className="space-y-4">
      <p className="text-sm text-gray-600">
        Enter the 6-character setup code shown in your terminal after running{" "}
        <code className="bg-gray-100 px-1 rounded">./bioaf setup</code>.
      </p>
      <div>
        <label htmlFor="setup-code" className="block text-sm font-medium text-gray-700 mb-1">
          Setup Code
        </label>
        <input
          id="setup-code"
          type="text"
          value={setupCode}
          onChange={(e) => setSetupCode(e.target.value.toUpperCase())}
          placeholder="Enter 6-character code"
          maxLength={6}
          className="w-full px-3 py-2 border rounded focus:ring-2 focus:ring-bioaf-500 font-mono text-lg tracking-widest text-center"
        />
      </div>
      <Button className="w-full"
        onClick={onVerify}
        disabled={setupCode.length !== 6}>
        Verify
      </Button>
    </div>
  );
}

export function AdminAccountStep({
  name,
  setName,
  email,
  setEmail,
  password,
  setPassword,
  confirmPassword,
  setConfirmPassword,
  onCreate,
}: {
  name: string;
  setName: (value: string) => void;
  email: string;
  setEmail: (value: string) => void;
  password: string;
  setPassword: (value: string) => void;
  confirmPassword: string;
  setConfirmPassword: (value: string) => void;
  onCreate: () => void;
}) {
  return (
    <div className="space-y-4">
      <div>
        <label htmlFor="setup-name" className="block text-sm font-medium text-gray-700 mb-1">Name</label>
        <input id="setup-name" type="text" value={name} onChange={(e) => setName(e.target.value)}
          className="w-full px-3 py-2 border rounded focus:ring-2 focus:ring-bioaf-500" />
      </div>
      <div>
        <label htmlFor="setup-email" className="block text-sm font-medium text-gray-700 mb-1">Email</label>
        <input id="setup-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)}
          className="w-full px-3 py-2 border rounded focus:ring-2 focus:ring-bioaf-500" required />
      </div>
      <div>
        <label htmlFor="setup-password" className="block text-sm font-medium text-gray-700 mb-1">Password</label>
        <input id="setup-password" type="password" value={password} onChange={(e) => setPassword(e.target.value)}
          className="w-full px-3 py-2 border rounded focus:ring-2 focus:ring-bioaf-500" required />
      </div>
      <div>
        <label htmlFor="setup-confirm-password" className="block text-sm font-medium text-gray-700 mb-1">Confirm Password</label>
        <input id="setup-confirm-password" type="password" value={confirmPassword} onChange={(e) => setConfirmPassword(e.target.value)}
          className="w-full px-3 py-2 border rounded focus:ring-2 focus:ring-bioaf-500" required />
      </div>
      <Button className="w-full" onClick={onCreate}>
        Create Admin Account
      </Button>
    </div>
  );
}

export function OrganizationStep({
  orgName,
  setOrgName,
  onSave,
}: {
  orgName: string;
  setOrgName: (value: string) => void;
  onSave: () => void;
}) {
  return (
    <div className="space-y-4">
      <div>
        <label htmlFor="setup-org-name" className="block text-sm font-medium text-gray-700 mb-1">Organization Name</label>
        <input id="setup-org-name" type="text" value={orgName} onChange={(e) => setOrgName(e.target.value)}
          placeholder="e.g., Acme Biotech"
          className="w-full px-3 py-2 border rounded focus:ring-2 focus:ring-bioaf-500" required />
      </div>
      <Button className="w-full" onClick={onSave}>
        Save Organization Name
      </Button>
    </div>
  );
}
