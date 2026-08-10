import { readFileSync } from "fs";
import { join } from "path";

/**
 * Confirmation has to correlate with blast radius.
 *
 * The 2026-08-04 review raised this, it was marked done, and the 2026-08-07
 * review found it still true with a fresh list. The sharpest instance: on
 * /infrastructure/components, destroying object storage was gated by a checkbox
 * plus a typed "delete my data", while the SAME act on an orphaned bucket was one
 * red button about 500 lines away. A user trained by the strong gate reasonably
 * reads the weak one as safe, which makes the inconsistency itself the defect.
 *
 * This is a curated list rather than a clever heuristic. Every entry was verified
 * by reading the handler, and a curated list that is true beats a general rule
 * that quietly matches nothing.
 */

const SRC = join(__dirname, "..");

/** Comments stripped, so a comment describing a defect cannot trip its own guard. */
function code(path: string): string {
  return readFileSync(join(SRC, path), "utf8")
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/(^|\n)(\s*)\/\/[^\n]*/g, "$1$2");
}

/**
 * The handler body, from its declaration to the start of the NEXT top-level
 * declaration in the same component. A fixed-size slice is not good enough: at
 * 2600 chars the window around `runCheck` swallowed `handleStartFresh`, and the
 * guard then "passed" on a neighbour's code.
 */
function handler(src: string, name: string): string {
  const start = src.indexOf(name);
  if (start === -1) return "";
  const rest = src.slice(start + name.length);
  const next = rest.search(/\n {2}(const|async function|function) \w/);
  return name + (next === -1 ? rest : rest.slice(0, next));
}

interface Gated {
  file: string;
  handler: string;
  what: string;
  /** Also require a typed-phrase gate: this destroys stored data outright. */
  phrase?: boolean;
}

const MUST_CONFIRM: Gated[] = [
  {
    file: "components/infrastructure/OrphanedResourcesCard.tsx",
    handler: "const handleCleanup",
    what: "deletes a live cloud resource, including GCS/S3 buckets and their contents",
    phrase: true,
  },
  {
    file: "components/infrastructure/DeployRecoveryModal.tsx",
    handler: "const handleStartFresh",
    what: "deletes every orphaned resource from the previous deployment, behind the CAUTIOUS-looking secondary button",
  },
  {
    file: "app/(app)/infrastructure/backup/page.tsx",
    handler: "const handleConfigRestore",
    what: "overwrites cloud credentials, LLM providers, integrations and networking config",
    phrase: true,
  },
  {
    file: "components/settings/NetworkingSettingsContent.tsx",
    handler: "async function applyHttps",
    what: "restarts backend and frontend, logging out every signed-in user",
  },
  {
    file: "components/settings/LlmSettingsContent.tsx",
    handler: "async function handleDeactivateAll",
    what: "turns off every AI feature for the whole organisation",
  },
  {
    file: "app/(app)/infrastructure/components/page.tsx",
    handler: "async function handleComponentToggle",
    what: "tears down a deployed add-on with running sessions on it (enabling already confirmed; disabling did not)",
  },
  {
    file: "components/lab-knowledge/LabGlossaryBrowser.tsx",
    handler: "const commit = async",
    what: "bulk-accepts AI proposals over existing definitions, or bulk-rejects them permanently",
  },
  {
    file: "components/settings/SlackSettingsContent.tsx",
    handler: "const handleDeleteMapping",
    what: "silently stops org-wide notifications to a Slack channel",
  },
  {
    file: "app/(app)/infrastructure/cost-center/page.tsx",
    handler: "const handleSaveBudget",
    what: "arms an automatic compute shutdown at 100% of budget",
  },
  {
    file: "components/auth/SetupWizard.tsx",
    handler: "const handleSelectStack",
    what: "provisions real cloud infrastructure and starts incurring charges",
  },
];

describe.each(MUST_CONFIRM)("$what", ({ file, handler: name, phrase }) => {
  const body = handler(code(file), name);

  it(`is gated by a confirmation in ${file}`, () => {
    expect(body).not.toBe("");
    // Two mechanisms are legitimate. Most handlers use the promise-returning
    // `useConfirm` drop-in; a few pages drive `ConfirmDialog` from local state
    // and put the action in `onConfirm`, which is equally gated. Requiring only
    // the first would have failed a handler that IS confirmed.
    const viaHook = /await confirm\(/.test(body);
    const viaDialog = /setConfirmDialog\(\{/.test(body) && /onConfirm:/.test(body);
    expect(viaHook || viaDialog).toBe(true);

    // The answer has to be acted on. An `await confirm(...)` whose value is
    // discarded is worse than no dialog: it looks gated and is not.
    if (viaHook) {
      expect(body).toMatch(/if \(!ok\) return|if \(!\(await confirm/);
    }
  });

  if (phrase) {
    it("requires a typed phrase, matching how the same act is gated elsewhere", () => {
      expect(body).toMatch(/requirePhrase/);
    });
  }
});

/**
 * A destructive cloud call fired as a side effect of a component rendering, with
 * no user action and no disclosure, and its failure swallowed. A render does not
 * get to delete things.
 */
test("the deploy recovery modal deletes nothing just by opening", () => {
  const src = code("components/infrastructure/DeployRecoveryModal.tsx");
  const runCheck = handler(src, "const runCheck");
  expect(runCheck).not.toBe("");
  expect(runCheck).not.toMatch(/cleanup-all/);
});

/**
 * The wizard used to wrap its deploy POST in a bare catch and advance regardless,
 * so the next step asserted "Infrastructure deployment has started" whether or
 * not it had.
 */
test("the setup wizard does not claim a deployment started when it failed", () => {
  const body = handler(code("components/auth/SetupWizard.tsx"), "const handleSelectStack");
  expect(body).toMatch(/stack\/deploy-background/);
  // No empty catch between the POST and the step advance.
  const postIdx = body.indexOf("stack/deploy-background");
  const advanceIdx = body.indexOf("setStep(7)");
  expect(advanceIdx).toBeGreaterThan(postIdx);
  expect(body.slice(postIdx, advanceIdx)).not.toMatch(/catch\s*\{\s*\}/);
});

/**
 * `ConfirmDialog` grew `requirePhrase` so the strongest gate in the app stopped
 * being a one-off hand-rolled on a single page. If the confirm button can be
 * pressed while the phrase is unsatisfied, the gate is decoration.
 */
test("a typed-phrase confirm cannot be confirmed until the phrase matches", () => {
  const src = code("components/shared/ConfirmDialog.tsx");
  expect(src).toMatch(/phraseSatisfied/);
  expect(src).toMatch(/disabled=\{busy \|\| !phraseSatisfied\}/);
  expect(src).toMatch(/if \(!busy && phraseSatisfied\) onConfirm\(\)/);
});
