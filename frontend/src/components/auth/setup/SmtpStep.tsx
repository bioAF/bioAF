"use client";

import { Button } from "@/components/ui/Button";

export interface SmtpFields {
  smtpHost: string;
  smtpPort: string;
  smtpUsername: string;
  smtpPassword: string;
  smtpFrom: string;
}

/** The SMTP step's view. The values (the password among them) are the wizard's, in memory only. */
export function SmtpStep({
  fields,
  onChange,
  onSave,
  onSkip,
}: {
  fields: SmtpFields;
  onChange: (patch: Partial<SmtpFields>) => void;
  onSave: () => void;
  onSkip: () => void;
}) {
  const { smtpHost, smtpPort, smtpUsername, smtpPassword, smtpFrom } = fields;
  const setSmtpHost = (smtpHost: string) => onChange({ smtpHost });
  const setSmtpPort = (smtpPort: string) => onChange({ smtpPort });
  const setSmtpUsername = (smtpUsername: string) => onChange({ smtpUsername });
  const setSmtpPassword = (smtpPassword: string) => onChange({ smtpPassword });
  const setSmtpFrom = (smtpFrom: string) => onChange({ smtpFrom });
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-4">
        <div>
          <label htmlFor="smtp-host" className="block text-sm font-medium text-gray-700 mb-1">SMTP Host</label>
          <input id="smtp-host" type="text" value={smtpHost} onChange={(e) => setSmtpHost(e.target.value)}
            className="w-full px-3 py-2 border rounded focus:ring-2 focus:ring-bioaf-500" />
        </div>
        <div>
          <label htmlFor="port" className="block text-sm font-medium text-gray-700 mb-1">Port</label>
          <input id="port" type="number" value={smtpPort} onChange={(e) => setSmtpPort(e.target.value)}
            className="w-full px-3 py-2 border rounded focus:ring-2 focus:ring-bioaf-500" />
        </div>
      </div>
      <div>
        <label htmlFor="username" className="block text-sm font-medium text-gray-700 mb-1">Username</label>
        <input id="username" type="text" value={smtpUsername} onChange={(e) => setSmtpUsername(e.target.value)}
          className="w-full px-3 py-2 border rounded focus:ring-2 focus:ring-bioaf-500" />
      </div>
      <div>
        <label htmlFor="password" className="block text-sm font-medium text-gray-700 mb-1">Password</label>
        <input id="password" type="password" value={smtpPassword} onChange={(e) => setSmtpPassword(e.target.value)}
          className="w-full px-3 py-2 border rounded focus:ring-2 focus:ring-bioaf-500" />
      </div>
      <div>
        <label htmlFor="from-address" className="block text-sm font-medium text-gray-700 mb-1">From Address</label>
        <input id="from-address" type="email" value={smtpFrom} onChange={(e) => setSmtpFrom(e.target.value)}
          className="w-full px-3 py-2 border rounded focus:ring-2 focus:ring-bioaf-500" />
      </div>
      <Button className="w-full" onClick={onSave}>
        Save SMTP Configuration
      </Button>
      <button onClick={onSkip} className="w-full text-gray-500 text-sm hover:text-gray-700">
        Do this later
      </button>
    </div>
  );
}
