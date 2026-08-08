"use client";

import { useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [sent, setSent] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    try {
      await api.post("/api/auth/request-reset", { email });
      // The backend always returns a generic success, so we never reveal whether
      // the email exists.
      setSent(true);
    } catch {
      // Treat any error the same way to avoid leaking account existence.
      setSent(true);
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
          {sent ? (
            <>
              <h2 className="text-xl font-semibold mb-4">Check your email</h2>
              <p className="text-sm text-gray-600 mb-6">
                If an account exists for that address, we&apos;ve sent a password reset
                link. It is valid for 60 minutes. Follow the link and enter the
                reset code from the email to choose a new password.
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
              <h2 className="text-xl font-semibold mb-2">Reset your password</h2>
              <p className="text-sm text-gray-600 mb-6">
                Enter your email address and we&apos;ll send you a link to reset your
                password.
              </p>

              <div className="mb-6">
                <label htmlFor="forgot-email" className="block text-sm font-medium text-gray-700 mb-1">
                  Email
                </label>
                <input
                  id="forgot-email"
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 rounded focus:outline-none focus:ring-2 focus:ring-bioaf-500"
                  required
                />
              </div>

              <button
                type="submit"
                disabled={submitting}
                className="w-full bg-bioaf-600 text-white py-2 rounded hover:bg-bioaf-700 disabled:opacity-50"
              >
                {submitting ? "Sending..." : "Send reset link"}
              </button>

              <div className="mt-4 text-center">
                <Link href="/login" className="text-sm text-bioaf-600 hover:text-bioaf-700">
                  Back to sign in
                </Link>
              </div>
            </form>
          )}
        </div>
      </div>
    </div>
  );
}
