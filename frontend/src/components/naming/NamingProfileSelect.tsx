"use client";

import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import type { NamingProfile } from "@/lib/types";

interface Props {
  /**
   * Current selection. `null` means no profile is set on this entity.
   * `undefined` means the entity has not yet been loaded.
   */
  value: number | null;
  onChange: (value: number | null) => void;
  /**
   * If the parent already loaded profiles, pass them through to avoid a
   * duplicate fetch. Otherwise the component fetches on mount.
   */
  profiles?: NamingProfile[];
  /**
   * Label shown above the select.
   */
  label?: string;
  /**
   * Help text rendered below the select. Useful for inheritance hints.
   */
  hint?: string;
  /**
   * Label for the "no profile" option. Defaults to "No profile".
   */
  emptyLabel?: string;
  id?: string;
}

export function NamingProfileSelect({
  value,
  onChange,
  profiles: profilesProp,
  label = "Naming profile",
  hint,
  emptyLabel = "No profile",
  id = "np-select",
}: Props) {
  const [fetched, setFetched] = useState<NamingProfile[] | null>(null);

  useEffect(() => {
    if (profilesProp !== undefined) return;
    api
      .get<NamingProfile[]>("/api/naming-profiles?status=active")
      .then(setFetched)
      .catch(() => setFetched([]));
  }, [profilesProp]);

  const profiles = profilesProp ?? fetched ?? [];

  return (
    <div>
      <label htmlFor={id} className="block text-sm font-medium text-gray-700 mb-1">
        {label}
      </label>
      <select
        id={id}
        aria-label={label}
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)}
        className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
      >
        <option value="">{emptyLabel}</option>
        {profiles.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name}
          </option>
        ))}
      </select>
      {hint && <p className="mt-1 text-xs text-gray-500">{hint}</p>}
    </div>
  );
}
