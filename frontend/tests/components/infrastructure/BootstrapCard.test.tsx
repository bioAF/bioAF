/**
 * Tests 30-32: BootstrapCard component (Step 12 - Phase 17).
 *
 * 30: Shows Initialize button when not initialized and GCP configured
 * 31: Hidden when terraform_initialized = true
 * 32: Button disabled with GCP settings link when GCP not configured
 */

import { render, screen, waitFor } from "@testing-library/react";
import { BootstrapCard } from "@/components/infrastructure/BootstrapCard";

// These controls need infrastructure/pipelines permissions; the suite is about their behavior, so
// the role has every permission.
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => true, loading: false, failed: false }),
}));

// ---------------------------------------------------------------------------
// Test 30: Shows Initialize button when GCP configured + not initialized
// ---------------------------------------------------------------------------

test("BootstrapCard shows Initialize button when GCP configured and not initialized", () => {
  render(
    <BootstrapCard
      terraformInitialized={false}
      gcpCredentialsConfigured={true}
      onBootstrapStart={jest.fn()}
    />
  );

  expect(screen.getByTestId("bootstrap-card")).toBeInTheDocument();
  const btn = screen.getByTestId("bootstrap-btn");
  expect(btn).not.toBeDisabled();
});

// ---------------------------------------------------------------------------
// Test 31: Hidden when already initialized
// ---------------------------------------------------------------------------

test("BootstrapCard is not rendered when terraform_initialized is true", () => {
  const { container } = render(
    <BootstrapCard
      terraformInitialized={true}
      gcpCredentialsConfigured={true}
      onBootstrapStart={jest.fn()}
    />
  );

  expect(container.firstChild).toBeNull();
});

// ---------------------------------------------------------------------------
// Test 32: Disabled button with GCP settings link when GCP not configured
// ---------------------------------------------------------------------------

test("BootstrapCard shows disabled button and GCP link when GCP not configured", () => {
  render(
    <BootstrapCard
      terraformInitialized={false}
      gcpCredentialsConfigured={false}
      onBootstrapStart={jest.fn()}
    />
  );

  expect(screen.getByTestId("bootstrap-card")).toBeInTheDocument();
  const btn = screen.getByTestId("bootstrap-btn");
  expect(btn).toBeDisabled();
  expect(screen.getByTestId("gcp-settings-link")).toBeInTheDocument();
});
