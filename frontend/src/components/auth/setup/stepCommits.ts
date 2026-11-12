/**
 * Which setup steps were submitted, and with what. A step re-submitted with the values it was
 * last committed with is skipped (no request, no confirmation); one with changed values asks
 * before replacing what the backend already has.
 */
export type StepValues = Record<string, string>;

export interface StepCommits {
  completed: Set<number>;
  values: Record<number, StepValues>;
}

export const NO_COMMITS: StepCommits = { completed: new Set(), values: {} };

export function commitStep(commits: StepCommits, step: number, values: StepValues): StepCommits {
  const completed = new Set(commits.completed);
  completed.add(step);
  return { completed, values: { ...commits.values, [step]: values } };
}

/** True when the step was committed and every current value matches its commit. */
export function unchangedSinceCommit(commits: StepCommits, step: number, values: StepValues): boolean {
  if (!commits.completed.has(step)) return false;
  const committed = commits.values[step] ?? {};
  return Object.keys(values).every((k) => (committed[k] ?? "") === (values[k] ?? ""));
}

/** What re-submitting the admin step would change, one line per field; the password is not shown. */
export function adminChangeSummary(committed: StepValues, current: { email: string; name: string; password: string }): string {
  return [
    committed.email !== current.email ? `Email: ${committed.email || "(empty)"} -> ${current.email}` : null,
    committed.name !== current.name ? `Name: ${committed.name || "(empty)"} -> ${current.name || "(empty)"}` : null,
    committed.password !== current.password ? "Password updated" : null,
  ]
    .filter(Boolean)
    .join("\n");
}
