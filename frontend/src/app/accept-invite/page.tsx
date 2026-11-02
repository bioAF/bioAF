"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import { logError } from "@/lib/errorReporting";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";

function AcceptInviteInner() {
  const searchParams = useSearchParams();
  const token = searchParams.get("token") ?? "";

  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [dead, setDead] = useState(false);
  const [done, setDone] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");

    if (password.length < 8) {
      setError("Password must be at least 8 characters");
      return;
    }
    if (password !== confirmPassword) {
      setError("Passwords do not match");
      return;
    }

    setSubmitting(true);
    try {
      const trimmedName = name.trim();
      await api.post("/api/users/accept-invite", {
        token,
        password,
        ...(trimmedName ? { name: trimmedName } : {}),
      });
      setDone(true);
    } catch (err) {
      if (err instanceof ApiError && err.status === 400) {
        setDead(true);
      } else {
        logError("accepting an invitation", err);
        setError("Your invitation could not be accepted right now. Try again in a moment.");
      }
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

        <Card>
          {done ? (
            <>
              <h2 className="text-xl font-semibold mb-4">Invitation accepted</h2>
              <p className="text-sm text-gray-600 mb-6">Your account is ready. Sign in with your new password.</p>
              <Link
                href="/login"
                className="block w-full text-center bg-bioaf-600 text-white py-2 rounded hover:bg-bioaf-700"
              >
                Go to sign in
              </Link>
            </>
          ) : !token || dead ? (
            <>
              <h2 className="text-xl font-semibold mb-4">Invitation link invalid</h2>
              <p className="text-sm text-gray-600 mb-6">
                This invitation has expired or has already been used. Ask your administrator to resend it.
              </p>
              <Link
                href="/login"
                className="block w-full text-center bg-bioaf-600 text-white py-2 rounded hover:bg-bioaf-700"
              >
                Back to sign in
              </Link>
            </>
          ) : (
            <form onSubmit={handleSubmit}>
              <h2 className="text-xl font-semibold mb-2">Accept your invitation</h2>
              <p className="text-sm text-gray-600 mb-6">Choose a password to finish setting up your account.</p>

              {error && (
                <div className="mb-4 p-3 bg-red-50 border border-red-200 text-red-700 rounded text-sm">{error}</div>
              )}

              <div className="mb-4">
                <label htmlFor="invite-name" className="block text-sm font-medium text-gray-700 mb-1">
                  Name (optional)
                </label>
                <input
                  id="invite-name"
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  className="w-full border border-gray-300 rounded px-3 py-2"
                  autoComplete="name"
                />
              </div>
              <div className="mb-4">
                <label htmlFor="invite-password" className="block text-sm font-medium text-gray-700 mb-1">
                  Password
                </label>
                <input
                  id="invite-password"
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="w-full border border-gray-300 rounded px-3 py-2"
                  autoComplete="new-password"
                  required
                />
              </div>
              <div className="mb-6">
                <label htmlFor="invite-confirm-password" className="block text-sm font-medium text-gray-700 mb-1">
                  Confirm password
                </label>
                <input
                  id="invite-confirm-password"
                  type="password"
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  className="w-full border border-gray-300 rounded px-3 py-2"
                  autoComplete="new-password"
                  required
                />
              </div>
              <Button type="submit" className="w-full" busy={submitting} busyLabel="Accepting...">
                Accept invitation
              </Button>
            </form>
          )}
        </Card>
      </div>
    </div>
  );
}

export default function AcceptInvitePage() {
  return (
    <Suspense fallback={null}>
      <AcceptInviteInner />
    </Suspense>
  );
}
