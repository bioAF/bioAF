"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { LoginForm } from "@/components/auth/LoginForm";
import { api } from "@/lib/api";
import { setToken, isAuthenticated } from "@/lib/auth";
import type { LoginResponse, BootstrapStatus } from "@/lib/types";

export default function LoginPage() {
  const router = useRouter();
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function checkSetup() {
      if (isAuthenticated()) {
        router.push("/");
        return;
      }
      try {
        const status = await api.get<BootstrapStatus>("/api/bootstrap/status");
        if (!status.setup_complete) {
          router.push("/setup");
          return;
        }
      } catch {
        // Backend may not be available
      }
      setLoading(false);
    }
    checkSetup();
  }, [router]);

  const handleLogin = async (email: string, password: string) => {
    setError("");
    try {
      const response = await api.post<LoginResponse>("/api/auth/login", {
        email,
        password,
      });
      setToken(response.access_token);
      router.push("/");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Login failed");
    }
  };

  if (loading) return null;

  return (
    <div className="min-h-screen flex items-center justify-center bg-canvas">
      <div className="max-w-md w-full">
        <div className="text-center mb-8">
          <h1 className="text-3xl font-bold text-bioaf-700">bioAF</h1>
          <p className="text-gray-500 mt-2">Computational Biology Platform</p>
        </div>
        <LoginForm onSubmit={handleLogin} error={error} />
      </div>
    </div>
  );
}
