"use client";

interface PasswordResetActionsProps<T extends { status: string }> {
  user: T;
  smtpConfigured: boolean;
  onSendResetEmail: (user: T) => void;
  onSetManualPassword: (user: T) => void;
}

/**
 * Admin password-reset actions for a single user. The manual "set password"
 * option is always available for an active user; sending a reset email is only
 * offered when SMTP is configured (otherwise the email cannot be delivered).
 */
export function PasswordResetActions<T extends { status: string }>({
  user,
  smtpConfigured,
  onSendResetEmail,
  onSetManualPassword,
}: PasswordResetActionsProps<T>) {
  if (user.status === "deactivated") return null;

  return (
    <>
      {smtpConfigured && (
        <button
          onClick={() => onSendResetEmail(user)}
          className="px-3 py-1.5 text-sm bg-white border border-gray-300 text-gray-700 rounded hover:bg-gray-50"
        >
          Send Password Reset Link
        </button>
      )}
      <button
        onClick={() => onSetManualPassword(user)}
        className="px-3 py-1.5 text-sm bg-white border border-gray-300 text-gray-700 rounded hover:bg-gray-50"
      >
        Set Password Manually
      </button>
    </>
  );
}
