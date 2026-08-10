import { readFileSync, readdirSync } from "fs";
import { join, relative } from "path";
import { STATUS_STYLES, statusBadgeClass } from "@/lib/statusStyles";

// lib/statusStyles.ts is the single source of truth for status presentation.
// The failure mode it exists to prevent is not ugliness, it is DRIFT: the same
// status rendered two different colours on two screens, so colour stops being
// learnable.
//
// This guard covers the drift that was actually measured, not every colour in
// the app:
//   1. severity (info/warning/critical) was mapped in three files, and `warning`
//      was yellow in the notification list and amber in the activity feed.
//   2. the environments page hand-rolled the four environmentVersion colours in
//      a ternary chain while importing statusLabel() for the label from the
//      registry, so half of that badge came from the registry and half did not.
//   3. TONE_CLASSES was copied verbatim into two files.
//
// A tone map that is defined once and used once (Toast, the networking pill) is
// not drift and is deliberately not caught here.

const SRC = join(__dirname, "..");
const REGISTRY = join(SRC, "lib", "statusStyles.ts");

function sourceFiles(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === "node_modules" || entry.name === ".next") continue;
      out.push(...sourceFiles(path));
    } else if (
      (entry.name.endsWith(".tsx") || entry.name.endsWith(".ts")) &&
      !entry.name.includes(".test.")
    ) {
      out.push(path);
    }
  }
  return out;
}

const FILES = sourceFiles(SRC).filter((f) => f !== REGISTRY);

const COLOR = String.raw`(?:bg|text)-(?:red|orange|amber|yellow|green|emerald|teal|cyan|sky|blue|indigo|purple|rose)-\d{2,3}`;

test("severity colours are not redefined outside the registry", () => {
  // A file re-decides the severity scale when it colours `warning` AND
  // `critical`. Requiring both keeps Toast out of it: its tones are
  // error/success/info, a different scale that is defined once and used once.
  const keyed = (key: string) =>
    new RegExp(String.raw`["']?${key}["']?\s*[:?]\s*["'][^"']*${COLOR}`);
  const offenders = FILES.filter((f) => {
    const src = readFileSync(f, "utf8");
    return keyed("warning").test(src) && keyed("critical").test(src);
  }).map((f) => relative(SRC, f));
  expect(offenders).toEqual([]);
});

test("the registry carries severity, so those files have somewhere to go", () => {
  expect(Object.keys(STATUS_STYLES.severity)).toEqual(
    expect.arrayContaining(["info", "warning", "critical"]),
  );
});

test("environment version badges take their colour from the registry", () => {
  const page = readFileSync(
    join(SRC, "app", "(app)", "environments", "page.tsx"),
    "utf8",
  );
  // The four registry colours must not be spelled out by hand in the page.
  for (const status of Object.keys(STATUS_STYLES.environmentVersion)) {
    const badge = statusBadgeClass("environmentVersion", status);
    expect(page).not.toContain(`"${badge}"`);
  }
  expect(page).toContain('statusBadgeClass("environmentVersion"');
});

test("the custom pipeline version change badge is decided in one place", () => {
  // changeLabel() and its TONE_CLASSES map were byte-identical copies in the
  // launch dialog and the detail page, so a change to one silently disagreed
  // with the other.
  const copies = FILES.filter((f) => {
    const src = readFileSync(f, "utf8");
    return /const TONE_CLASSES\s*[:=]/.test(src) || /function changeLabel\b/.test(src);
  }).map((f) => relative(SRC, f));
  expect(copies).toEqual([]);

  const definers = FILES.filter((f) =>
    /export function versionChangeKind\b/.test(readFileSync(f, "utf8")),
  ).map((f) => relative(SRC, f));
  expect(definers).toEqual(["lib/customPipelineVersions.ts"]);
});
