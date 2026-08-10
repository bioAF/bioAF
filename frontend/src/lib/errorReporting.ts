/**
 * One path for reporting a failure: the real error goes to the logs, and the
 * user reads a plain sentence.
 *
 * The rule this enforces: a user is not a debugger. "TypeError: Cannot read
 * properties of undefined (reading 'length')" tells them nothing they can act
 * on, and a raw backend string can leak internals into a screenshot. What they
 * need is what is missing from the screen, and where the detail lives.
 *
 * console.error is the log surface on the frontend: it is what the browser
 * console shows, what an error-boundary report picks up, and what a support
 * request can be asked to copy.
 */

/** Put the real error in the logs, tagged with what was being attempted. */
export function logError(context: string, error: unknown): void {
  console.error(`[bioAF] Failed while ${context}:`, error);
}

/**
 * The sentence shown in place of content that could not be loaded. `what`
 * names the missing content and starts the sentence, e.g. "Samples".
 */
export function loadFailureMessage(what: string): string {
  return `${what} could not be loaded, so nothing is shown here. The technical detail is in the application logs.`;
}
