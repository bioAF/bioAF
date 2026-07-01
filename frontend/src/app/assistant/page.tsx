"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

/**
 * The assistant is now a global floating bubble (FloatingAssistant, mounted in the root layout), not
 * a standalone page. This route is kept only so existing /assistant links don't 404: it redirects
 * home, where the bubble is available on every screen.
 */
export default function AssistantPage() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/");
  }, [router]);
  return null;
}
