"use client";

import { useEffect, useState, useRef } from "react";
import { api } from "@/lib/api";
import { logError } from "@/lib/errorReporting";
import { NotificationDropdown } from "./NotificationDropdown";

export function NotificationBell() {
  const [unreadCount, setUnreadCount] = useState(0);
  // A count that could not be read is not a count of zero. Measured on the
  // demo with 241 unread: the badge read "99+" healthy and rendered nothing at
  // all once the request failed, because the initial state and the failure
  // state are the same number.
  const [countFailed, setCountFailed] = useState(false);
  const [showDropdown, setShowDropdown] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  const fetchCount = async () => {
    try {
      const data = await api.get<{ count: number }>("/api/notifications/unread-count");
      setUnreadCount(data.count);
      setCountFailed(false);
    } catch (e) {
      logError("loading the unread notification count", e);
      setCountFailed(true);
    }
  };

  useEffect(() => {
    fetchCount();
    const interval = setInterval(fetchCount, 30000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setShowDropdown(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setShowDropdown(!showDropdown)}
        aria-label={
          countFailed
            ? "Notifications: the unread notification count is unavailable"
            : `Notifications: ${unreadCount} unread`
        }
        className="relative p-2 text-gray-500 hover:text-gray-700 focus:outline-none"
      >
        <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" />
        </svg>
        {countFailed ? (
          // Not a number, because there is no number to show. A neutral mark
          // says "unknown" where a 0 would say "none".
          <span
            data-testid="notification-count-unknown"
            title="The unread notification count is unavailable."
            className="absolute top-0 right-0 inline-flex items-center justify-center px-1.5 py-0.5 text-xs font-bold leading-none text-white bg-gray-500 rounded-full"
          >
            ?
          </span>
        ) : (
          unreadCount > 0 && (
            <span className="absolute top-0 right-0 inline-flex items-center justify-center px-1.5 py-0.5 text-xs font-bold leading-none text-white bg-red-600 rounded-full">
              {unreadCount > 99 ? "99+" : unreadCount}
            </span>
          )
        )}
      </button>
      {showDropdown && (
        <NotificationDropdown
          onClose={() => setShowDropdown(false)}
          onCountChange={setUnreadCount}
        />
      )}
    </div>
  );
}
