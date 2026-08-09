"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/Button";

// The top creating jobs, startable from anywhere. New Sample points at the
// experiment registration flow, which is where samples are first added (a sample
// always belongs to an experiment).
const ITEMS: { label: string; href: string }[] = [
  { label: "New Project", href: "/projects?new=1" },
  { label: "New Experiment", href: "/experiments/new" },
  { label: "New Sample", href: "/experiments/new" },
];

export function QuickCreateMenu() {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onClickOutside = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, []);

  return (
    <div className="relative" ref={ref}>
      <Button size="sm"
        onClick={() => setOpen((o) => !o)}>
        + New
      </Button>
      {open && (
        <div className="absolute right-0 mt-1 w-48 bg-white rounded-md shadow-lg border border-gray-200 z-50 py-1">
          {ITEMS.map((item) => (
            <Link
              key={item.label}
              href={item.href}
              onClick={() => setOpen(false)}
              className="block px-4 py-2 text-sm text-gray-700 hover:bg-gray-50"
            >
              {item.label}
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
