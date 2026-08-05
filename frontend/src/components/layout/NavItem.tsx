"use client";

import Link from "next/link";

interface NavItemProps {
  label: string;
  href: string;
  active: boolean;
  phase?: string;
  isCurrentPage: boolean;
}

export function NavItem({ label, href, active, phase, isCurrentPage }: NavItemProps) {
  if (!active) {
    return (
      <div className="flex items-center justify-between px-3 py-2 rounded-md text-gray-500 cursor-not-allowed">
        <span>{label}</span>
        {phase && (
          <span className="text-xs bg-gray-700 text-gray-500 px-2 py-0.5 rounded">
            {phase}
          </span>
        )}
      </div>
    );
  }

  return (
    <Link
      href={href}
      className={`flex items-center px-3 py-2 rounded-md transition-colors ${
        isCurrentPage
          ? "bg-bioaf-700 text-white"
          : "text-gray-300 hover:bg-gray-800 hover:text-white"
      }`}
    >
      {label}
    </Link>
  );
}
