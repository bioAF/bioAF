# Frontend dependencies

`package.json` declares the frontend's dependencies and `package-lock.json` pins every resolved version. Every
installer uses the lock strictly:

| Consumer | Command |
| --- | --- |
| Frontend image (`docker/Dockerfile.frontend`) | `npm ci` on `node:20-alpine` |
| CI lint, contracts, types and tests (`.github/workflows/ci.yml`) | `npm ci` on Node 20 |
| Manual development | `npm ci` on Node 20 |

`npm ci` fails when the lock does not match `package.json`; it never rewrites the lock. Use Node 20 locally too: a
newer npm can rewrite the lock's format without changing any selection.

## Changing dependencies

From `frontend/`:

```bash
# Change a declaration or an override in package.json, then re-resolve without upgrading unrelated packages:
npm install --package-lock-only
npm ci

# Before pushing:
npm run lint && npm run contracts:check && npx tsc --noEmit && npx jest && npm run build
npm audit
```

Review the lock diff, not just the declaration: list every package whose version moved and why. Never use
`npm audit fix --force`, a global override across a major version, or an unreviewed fork.

## Overrides

`package.json` cannot carry comments, so the reason for each entry in `overrides` is recorded here. An override is a
security floor for a package that some parent's declared range still lets resolve to a vulnerable version. A fresh
`npm install` (or a lock refresh) would otherwise be free to select that version again.

Remove a floor only when every parent's declared range excludes the vulnerable versions; check with
`npm ls <package>` and the parents' `package.json` ranges, not with the current lock.

| Override | Advisories it closes | Parent whose range permits the vulnerable version |
| --- | --- | --- |
| `handlebars` `^4.7.10` | GHSA-8r5x-fm3f-whwj, GHSA-p8wg-vrv2-v86f, GHSA-xw65-4hp5-5hc7 and the 4.7.9 set | `ts-jest` (development) |
| `flatted` `^3.4.2` | GHSA-rf6f-7fwh-wjgh | `eslint` via `flat-cache` (development) |
| `picomatch@^2.0.0` `^2.3.2`, `picomatch@^4.0.0` `^4.0.4` | GHSA-c2c7-rcm5-vvqj, GHSA-3v7f-55p6-f55p | `jest`, `micromatch`, `fdir` (development) |
| `glob@^10.0.0` `^10.5.0` | GHSA-5j98-mcp5-4vw2 | `jest` (development) |
| `postcss@<8.5.23` `^8.5.23` | GHSA-fxqj-rqcc-2cmp | `next` pins `postcss` 8.4.31 exactly (runtime build) |
| `dompurify` `^3.4.18` | GHSA-cg2g-xh64-v4q7, GHSA-mrv2-3pqr-j5jr, GHSA-c6gr-qvv4-5v3f, GHSA-6688-9rhm-gjv2, GHSA-55q2-fjhq-7xh7 | `jspdf` optional `^3.3.1` (runtime) |
| `fflate` `^0.8.3` | GHSA-px8p-9vwx-vf98 | `jspdf` `^0.8.1` (runtime) |
| `sharp` `^0.35.5` | GHSA-wq5f-xc86-pv6w, GHSA-rgj7-g3m4-5g8c | `next` optional `^0.34.3 \|\| ^0.35.5` (runtime) |
| `source-map-js` `^1.2.2` | GHSA-68fv-2mgg-jv7q | `postcss` `^1.2.0` on older 8.5 releases, which the `postcss` floor still admits (runtime build) |
| `ws` `^8.21.0` | GHSA-96hv-2xvq-fx4p, GHSA-58qx-3vcg-4xpx | `jsdom` (development) |
| `brace-expansion@^1.1.7` `^1.1.16`, `@^2.0.2` `^2.1.2`, `@^5.0.2` `^5.0.7` | earlier brace-expansion advisories | `minimatch` 3.x, 5.x/9.x, 10.x (development) |
| `js-yaml@^3.13.1` `^3.15.0`, `js-yaml@^4.1.0` `^4.3.0` | earlier js-yaml advisories | `@istanbuljs/load-nyc-config`, `@eslint/eslintrc` (development) |

The direct `next` and `eslint-config-next` declarations are held at `^15.5.29` (GHSA-2464-fhr4-w9xw,
GHSA-p3m5-59mp-jv5r, GHSA-2xp9-vwfh-vxw4, GHSA-p293-qw3h-jr36, GHSA-4jqv-mc3x-m676, GHSA-mcj8-r9mp-w47p), and the direct
`postcss` declaration at `^8.5.33`, whose own range requires `source-map-js` `^1.2.2`.
