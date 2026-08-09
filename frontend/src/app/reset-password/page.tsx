"use client";

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import { logError } from "@/lib/errorReporting";

function ResetPasswordInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const token = searchParams.get("token") ?? "";

  const [checking, setChecking] = useState(true);
  const [valid, setValid] = useState(false);
  // A link that could not be CHECKED is not a link that has EXPIRED. The
  // backend answers `{valid: false}` for a genuinely dead token and never
  // raises for one, so every rejection here is an outage.
  const [checkFailed, setCheckFailed] = useState(false);
  const [recheckKey, setRecheckKey] = useState(0);

  const [code, setCode] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);

  useEffect(() => {
    async function validate() {
      if (!token) {
        setValid(false);
        setChecking(false);
        return;
      }
      try {
        const res = await api.get<{ valid: boolean }>(
          `/api/auth/reset-password/validate?token=${encodeURIComponent(token)}`,
        );
        setValid(res.valid);
        // A retry that succeeds must leave the failure state behind it.
        setCheckFailed(false);
      } catch (e) {
        logError("checking whether a password reset link is still valid", e);
        setValid(false);
        setCheckFailed(true);
      } finally {
        setChecking(false);
      }
    }
    validate();
  }, [token, recheckKey]);

  // Redirect to login shortly after a successful reset.
  useEffect(() => {
    if (!done) return;
    const t = setTimeout(() => router.push("/login"), 2000);
    return () => clearTimeout(t);
  }, [done, router]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");

    if (!code.trim()) {
      setError("Enter the reset code from your email");
      return;
    }
    if (newPassword.length < 8) {
      setError("New password must be at least 8 characters");
      return;
    }
    if (newPassword !== confirmPassword) {
      setError("Passwords do not match");
      return;
    }

    setSubmitting(true);
    try {
      await api.post("/api/auth/reset-password", {
        token,
        code: code.trim(),
        new_password: newPassword,
      });
      setDone(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to reset password");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-canvas">
      <div className="max-w-md w-full">
        <div className="text-center mb-8">
          <h1 className="text-3xl font-bold text-bioaf-700">bioAF</h1>
          <p className="text-gray-500 mt-2">Computational Biology Platform</p>
        </div>

        <div className="bg-white shadow rounded-lg p-8">
          {checking ? (
            <p className="text-sm text-gray-500">Checking your reset link...</p>
          ) : done ? (
            <>
              <h2 className="text-xl font-semibold mb-4">Password reset</h2>
              <p className="text-sm text-gray-600 mb-6">
                Your password has been reset. Redirecting you to sign in...
              </p>
              <Link
                href="/login"
                className="block w-full text-center bg-bioaf-600 text-white py-2 rounded hover:bg-bioaf-700"
              >
                Go to sign in
              </Link>
            </>
          ) : checkFailed ? (
            <div data-testid="reset-check-failed">
              <h2 className="text-xl font-semibold mb-4">We could not check your link</h2>
              <p className="text-sm text-gray-600 mb-6">
                Something went wrong while checking whether this reset link is
                still valid, so we cannot tell you either way yet. Your link has
                not been used up. Try again in a moment.
              </p>
              <button
                type="button"
                data-testid="reset-check-retry"
                onClick={() => {
                  setChecking(true);
                  setRecheckKey((k) => k + 1);
                }}
                className="block w-full text-center bg-bioaf-600 text-white py-2 rounded hover:bg-bioaf-700"
              >
                Try again
              </button>
              <div className="mt-4 text-center">
                <Link href="/login" className="text-sm text-bioaf-600 hover:text-bioaf-700">
                  Back to sign in
                </Link>
              </div>
            </div>
          ) : !valid ? (
            <>
              <h2 className="text-xl font-semibold mb-4">Link expired or invalid</h2>
              <p className="text-sm text-gray-600 mb-6">
                This password reset link has expired or is no longer valid. Reset
                links are valid for 60 minutes. You can request a new one.
              </p>
              <Link
                href="/forgot-password"
                className="block w-full text-center bg-bioaf-600 text-white py-2 rounded hover:bg-bioaf-700"
              >
                Request a new link
              </Link>
              <div className="mt-4 text-center">
                <Link href="/login" className="text-sm text-bioaf-600 hover:text-bioaf-700">
                  Back to sign in
                </Link>
              </div>
            </>
          ) : (
            <form onSubmit={handleSubmit}>
              <h2 className="text-xl font-semibold mb-2">Choose a new password</h2>
              <p className="text-sm text-gray-600 mb-6">
                Enter the 6-digit reset code from your email and set a new password.
              </p>

              {error && (
                <div className="mb-4 p-3 bg-red-50 border border-red-200 text-red-700 rounded text-sm">
                  {error}
                </div>
              )}

              <div className="mb-4">
                <label htmlFor="reset-code" className="block text-sm font-medium text-gray-700 mb-1">
                  Reset code
                </label>
                <input
                  id="reset-code"
                  type="text"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 rounded focus:outline-none focus:ring-2 focus:ring-bioaf-500 tracking-widest"
                  required
                />
              </div>

              <div className="mb-4">
                <label htmlFor="reset-new-password" className="block text-sm font-medium text-gray-700 mb-1">
                  New password
                </label>
                <input
                  id="reset-new-password"
                  type="password"
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 rounded focus:outline-none focus:ring-2 focus:ring-bioaf-500"
                  required
                />
              </div>

              <div className="mb-6">
                <label htmlFor="reset-confirm-password" className="block text-sm font-medium text-gray-700 mb-1">
                  Confirm new password
                </label>
                <input
                  id="reset-confirm-password"
                  type="password"
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 rounded focus:outline-none focus:ring-2 focus:ring-bioaf-500"
                  required
                />
              </div>

              <button
                type="submit"
                disabled={submitting}
                className="w-full bg-bioaf-600 text-white py-2 rounded hover:bg-bioaf-700 disabled:opacity-50"
              >
                {submitting ? "Resetting..." : "Reset password"}
              </button>
            </form>
          )}
        </div>
      </div>
    </div>
  );
}

export default function ResetPasswordPage() {
  return (
    <Suspense fallback={null}>
      <ResetPasswordInner />
    </Suspense>
  );
}
