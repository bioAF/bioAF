"use client";

import { forwardRef, type InputHTMLAttributes, type SelectHTMLAttributes, type TextareaHTMLAttributes } from "react";

/**
 * The app's text input, select and textarea.
 *
 * Thin wrappers: every native attribute, event and ref reaches the element
 * unchanged, so `required`, `readOnly`, `min`/`max`, `form`, `autoComplete`
 * and the browser's own validation behave exactly as on a bare element. They
 * add one appearance, including a visible focus ring and an invalid state
 * driven by `aria-invalid`. Labels, hints and errors come from `Field`.
 *
 * A caller's `className` is appended for layout (width, margin, font); it is
 * not a way to restyle the control.
 */
const CONTROL =
  "block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 " +
  "focus:border-bioaf-500 focus:outline-none focus:ring-1 focus:ring-bioaf-500 " +
  "aria-[invalid=true]:border-red-500 " +
  "disabled:cursor-not-allowed disabled:bg-gray-50 disabled:text-gray-600 " +
  "read-only:bg-gray-50 read-only:text-gray-600";

const withClass = (className?: string) => (className ? `${CONTROL} ${className}` : CONTROL);

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function Input(
  { className, ...rest },
  ref,
) {
  return <input ref={ref} className={withClass(className)} {...rest} />;
});

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(function Select(
  { className, ...rest },
  ref,
) {
  return <select ref={ref} className={withClass(className)} {...rest} />;
});

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(
  function Textarea({ className, ...rest }, ref) {
    return <textarea ref={ref} className={withClass(className)} {...rest} />;
  },
);
