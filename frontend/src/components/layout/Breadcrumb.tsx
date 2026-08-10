"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { findNavMatch } from "@/lib/navConfig";

interface BreadcrumbProps {
  entityName?: string;
}

interface BreadcrumbSegment {
  label: string;
  href?: string;
}

export function Breadcrumb({ entityName }: BreadcrumbProps) {
  const pathname = usePathname();
  const match = findNavMatch(pathname);

  const segments: BreadcrumbSegment[] = [];

  if (match) {
    if (match.child) {
      // Child page: Section > Child
      segments.push({
        label: match.section.label,
        href: match.section.children?.[0]?.path,
      });
      if (entityName) {
        segments.push({ label: match.child.label, href: match.child.path });
        segments.push({ label: entityName });
      } else {
        segments.push({ label: match.child.label });
      }
    } else {
      // Top-level page
      if (entityName) {
        segments.push({ label: match.section.label, href: match.section.path });
        segments.push({ label: entityName });
      } else {
        segments.push({ label: match.section.label });
      }
    }
  } else {
    // Fallback: show pathname segments
    const parts = pathname.split("/").filter(Boolean);
    parts.forEach((part, i) => {
      const isLast = i === parts.length - 1;
      segments.push({
        label: part.charAt(0).toUpperCase() + part.slice(1).replace(/-/g, " "),
        href: isLast ? undefined : "/" + parts.slice(0, i + 1).join("/"),
      });
    });
  }

  if (segments.length === 0) return null;

  return (
    <nav aria-label="Breadcrumb" className="px-6 py-2 text-sm text-gray-500" data-testid="breadcrumb">
      <ol className="flex items-center space-x-2">
        {segments.map((segment, index) => (
          <li key={index} className="flex items-center">
            {index > 0 && (
              <span className="mx-2 text-gray-500">/</span>
            )}
            {segment.href ? (
              <Link
                href={segment.href}
                className="text-bioaf-600 hover:text-bioaf-700 hover:underline"
              >
                {segment.label}
              </Link>
            ) : (
              <span className="text-gray-700 font-medium" data-testid="breadcrumb-current">
                {segment.label}
              </span>
            )}
          </li>
        ))}
      </ol>
    </nav>
  );
}
