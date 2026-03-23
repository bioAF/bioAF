"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { getCurrentUser, isAuthenticated } from "@/lib/auth";

export function useAuth(requiredRole?: string) {
  const router = useRouter();
  const [user, setUser] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!isAuthenticated()) {
      router.push("/login");
      return;
    }

    const currentUser = getCurrentUser();
    if (requiredRole && currentUser?.role_name !== requiredRole) {
      router.push("/");
      return;
    }

    setUser(currentUser);
    setLoading(false);
  }, [router, requiredRole]);

  return { user, loading };
}
