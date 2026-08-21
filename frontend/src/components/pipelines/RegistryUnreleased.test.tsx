import { render, screen } from "@testing-library/react";
import { RegistryInstallAction } from "./RegistryInstallAction";
import type { RegistryPipeline } from "@/lib/types";

/**
 * nf-core/scdownstream is the 19th most-starred pipeline and has only a `dev`
 * branch, so its version list is empty and it cannot be installed. The browse
 * list offered an Install button anyway; the user learned otherwise only after
 * clicking and waiting for the version fetch.
 */

function item(overrides: Partial<RegistryPipeline> = {}): RegistryPipeline {
  return {
    name: "scdownstream",
    full_name: "nf-core/scdownstream",
    description: "Single-cell downstream",
    topics: [],
    stars: 113,
    latest_release: null,
    archived: false,
    installed: false,
    installed_version: null,
    update_available: false,
    ...overrides,
  } as RegistryPipeline;
}

const noop = () => {};

it("does not offer Install for a pipeline with no release", () => {
  render(<RegistryInstallAction pipeline={item()} canInstall onInstall={noop} onUpdate={noop} />);

  expect(screen.queryByRole("button", { name: /install/i })).not.toBeInTheDocument();
});

it("says why it cannot be installed", () => {
  render(<RegistryInstallAction pipeline={item()} canInstall onInstall={noop} onUpdate={noop} />);

  expect(screen.getByText(/no release yet/i)).toBeInTheDocument();
});

it("still offers Install for a pipeline that has a release", () => {
  render(
    <RegistryInstallAction
      pipeline={item({ name: "sarek", latest_release: "3.9.0" })}
      canInstall
      onInstall={noop}
      onUpdate={noop}
    />,
  );

  expect(screen.getByRole("button", { name: /install/i })).toBeInTheDocument();
});

it("shows the update action when one is available", () => {
  render(
    <RegistryInstallAction
      pipeline={item({ installed: true, installed_version: "3.8.0", latest_release: "3.9.0", update_available: true })}
      canInstall
      onInstall={noop}
      onUpdate={noop}
    />,
  );

  expect(screen.getByRole("button", { name: /update to v3\.9\.0/i })).toBeInTheDocument();
});

it("reports an installed pipeline as up to date", () => {
  render(
    <RegistryInstallAction
      pipeline={item({ installed: true, latest_release: "3.9.0" })}
      canInstall
      onInstall={noop}
      onUpdate={noop}
    />,
  );

  expect(screen.getByText(/latest installed/i)).toBeInTheDocument();
});

it("offers nothing to a user without install permission", () => {
  render(<RegistryInstallAction pipeline={item({ latest_release: "1.0" })} canInstall={false} onInstall={noop} onUpdate={noop} />);

  expect(screen.queryByRole("button", { name: /install/i })).not.toBeInTheDocument();
});
