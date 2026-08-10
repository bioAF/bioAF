"use client";

import { useState } from "react";
import { Button } from "@/components/ui/Button";

interface ErrorStateProps {
  message: string;
  onRetry?: () => void;
  details?: string;
}

export function ErrorState({ message, onRetry, details }: ErrorStateProps) {
  const [showDetails, setShowDetails] = useState(false);

  return (
    <div
      data-testid="error-state"
      className="flex flex-col items-center justify-center py-12 px-4 text-center"
    >
      <svg
        className="w-12 h-12 text-red-400"
        fill="none"
        viewBox="0 0 24 24"
        stroke="currentColor"
        strokeWidth={1.5}
      >
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z"
        />
      </svg>
      <p
        data-testid="error-message"
        className="mt-4 text-sm font-medium text-red-600"
      >
        {message}
      </p>
      {details && (
        <>
          <button
            data-testid="error-details-toggle"
            onClick={() => setShowDetails(!showDetails)}
            className="mt-2 text-xs text-gray-500 underline hover:text-gray-700"
          >
            {showDetails ? "Hide details" : "Show details"}
          </button>
          {showDetails && (
            <pre
              data-testid="error-details"
              className="mt-2 max-w-md text-left text-xs text-gray-600 bg-gray-50 rounded p-3 overflow-auto"
            >
              {details}
            </pre>
          )}
        </>
      )}
      {onRetry && (
        <Button variant="danger" className="mt-4"
          data-testid="error-retry"
          onClick={onRetry}>
          Retry
        </Button>
      )}
    </div>
  );
}
