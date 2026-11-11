"use client";

import { useId, type ReactNode } from "react";

/** What a Field hands its control: spread these onto Input, Select or Textarea. */
export interface FieldControlProps {
  id: string;
  "aria-describedby"?: string;
  "aria-invalid"?: true;
}

export interface FieldProps {
  label: ReactNode;
  /** Help that stays on screen. A placeholder is not a substitute: it vanishes on input. */
  hint?: ReactNode;
  /** The field's current error. Marks the control invalid and becomes part of its description. */
  error?: ReactNode;
  /** A caller-chosen control id; otherwise one unique to this instance. */
  id?: string;
  /** Ids of other elements that also describe the control, kept ahead of the hint and error. */
  describedBy?: string;
  className?: string;
  children: (control: FieldControlProps) => ReactNode;
}

/**
 * One label, hint and error for one native control, associated by id.
 *
 * Ids come from `useId`, so a page with several dialogs or repeated rows open
 * never has two controls answering to the same label. Validation and parsing
 * stay with the feature: a Field only shows what it is given.
 */
export function Field({ label, hint, error, id, describedBy, className, children }: FieldProps) {
  const generated = useId();
  const controlId = id ?? `${generated}control`;
  const hintId = `${generated}hint`;
  const errorId = `${generated}error`;
  const describers = [describedBy, hint ? hintId : null, error ? errorId : null].filter(Boolean).join(" ");

  return (
    <div className={className}>
      <label htmlFor={controlId} className="mb-1 block text-sm font-medium text-gray-700">
        {label}
      </label>
      {children({
        id: controlId,
        ...(describers ? { "aria-describedby": describers } : {}),
        ...(error ? { "aria-invalid": true as const } : {}),
      })}
      {hint && (
        <p id={hintId} className="mt-1 text-xs text-gray-500">
          {hint}
        </p>
      )}
      {error && (
        <p id={errorId} className="mt-1 text-sm text-red-700">
          {error}
        </p>
      )}
    </div>
  );
}
