/**
 * Tests for route redirects defined in next.config.js.
 * These verify the redirect configuration is correct by importing and checking the config.
 */

import * as fs from "fs";
import * as path from "path";

// eslint-disable-next-line @typescript-eslint/no-var-requires
const nextConfig = require("../next.config.js");

interface Redirect {
  source: string;
  destination: string;
  permanent: boolean;
}

describe("Route Redirects", () => {
  let redirects: Redirect[];

  beforeAll(async () => {
    redirects = await nextConfig.redirects();
  });

  it("redirects /home to /dashboard", () => {
    const redirect = redirects.find((r: Redirect) => r.source === "/home");
    expect(redirect).toBeDefined();
    expect(redirect!.destination).toBe("/dashboard");
  });

  it("redirects /compute/pipelines/catalog to /pipelines/catalog", () => {
    const redirect = redirects.find(
      (r: Redirect) => r.source === "/compute/pipelines/catalog",
    );
    expect(redirect).toBeDefined();
    expect(redirect!.destination).toBe("/pipelines/catalog");
  });

  it("redirects /admin/users to /settings/users", () => {
    const redirect = redirects.find(
      (r: Redirect) => r.source === "/admin/users",
    );
    expect(redirect).toBeDefined();
    expect(redirect!.destination).toBe("/settings/users");
  });

  it("redirects /components to /infrastructure/components", () => {
    const redirect = redirects.find(
      (r: Redirect) => r.source === "/components",
    );
    expect(redirect).toBeDefined();
    expect(redirect!.destination).toBe("/infrastructure/components");
  });

  it("redirects /admin/settings to /settings/info", () => {
    const redirect = redirects.find(
      (r: Redirect) => r.source === "/admin/settings",
    );
    expect(redirect).toBeDefined();
    expect(redirect!.destination).toBe("/settings/info");
  });

  it("redirects /experiments/templates to /projects/experiment-templates", () => {
    const redirect = redirects.find(
      (r: Redirect) => r.source === "/experiments/templates",
    );
    expect(redirect).toBeDefined();
    expect(redirect!.destination).toBe("/projects/experiment-templates");
    expect(redirect!.permanent).toBe(true);
  });

  it("redirects /experiments to /projects/experiments", () => {
    const redirect = redirects.find(
      (r: Redirect) => r.source === "/experiments",
    );
    expect(redirect).toBeDefined();
    expect(redirect!.destination).toBe("/projects/experiments");
    expect(redirect!.permanent).toBe(true);
  });

  it("redirects /experiments/:id to /projects/experiments/:id", () => {
    const redirect = redirects.find(
      (r: Redirect) => r.source === "/experiments/:id",
    );
    expect(redirect).toBeDefined();
    expect(redirect!.destination).toBe("/projects/experiments/:id");
    expect(redirect!.permanent).toBe(true);
  });

  it("routes /experiments/new to /projects/experiments/new via the :id pattern", () => {
    // The /experiments/:id redirect catches /experiments/new and sends it to
    // /projects/experiments/new. That page must exist or Next.js falls through
    // to [id]/page.tsx with id="new", showing "Experiment not found".
    const idRedirect = redirects.find(
      (r: Redirect) => r.source === "/experiments/:id",
    );
    expect(idRedirect).toBeDefined();
    expect(idRedirect!.destination).toBe("/projects/experiments/:id");

    const newPagePath = path.resolve(
      __dirname,
      "../src/app/projects/experiments/new/page.tsx",
    );
    expect(fs.existsSync(newPagePath)).toBe(true);
  });

  it("contains all expected redirect sources", () => {
    const sources = redirects.map((r: Redirect) => r.source);
    expect(sources).toContain("/home");
    expect(sources).toContain("/compute");
    expect(sources).toContain("/admin/costs");
    expect(sources).toContain("/admin/backups");
    expect(sources).toContain("/admin/access-logs");
    expect(sources).toContain("/references");
    expect(sources).toContain("/environments");
    expect(sources).toContain("/packages");
    expect(sources).toContain("/experiments");
    expect(sources).toContain("/experiments/templates");
    expect(sources).toContain("/experiments/:id");
  });
});
