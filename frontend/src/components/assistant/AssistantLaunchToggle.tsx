"use client";

interface AssistantLaunchToggleProps {
  enabled: boolean;
  // Whether the current user may change the setting (settings:configure). Non-admins see it read-only.
  canConfigure: boolean;
  saving?: boolean;
  onChange: (next: boolean) => void;
}

/**
 * Admin toggle for whether the assistant launches runs for real on confirm. When off (default), a
 * confirmed plan only builds the launch request; when on, confirming actually starts a pipeline run
 * (and spends compute). Non-admins see the current mode read-only.
 */
export function AssistantLaunchToggle({
  enabled,
  canConfigure,
  saving = false,
  onChange,
}: AssistantLaunchToggleProps) {
  return (
    <div className="flex items-center gap-2 text-sm" data-testid="assistant-launch-toggle">
      <label className="flex items-center gap-2">
        <input
          type="checkbox"
          checked={enabled}
          disabled={!canConfigure || saving}
          onChange={(e) => onChange(e.target.checked)}
          aria-label="Launch runs for real on confirm"
        />
        <span className={enabled ? "font-medium text-green-700" : "text-gray-600"}>
          {enabled ? "Live launch on" : "Live launch off"}
        </span>
      </label>
      <span className="text-xs text-gray-400">
        {canConfigure
          ? "When on, confirming a plan starts a real run."
          : "Only an admin can change this."}
      </span>
    </div>
  );
}
