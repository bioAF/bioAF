import { render, screen } from "@testing-library/react";
import { LitValidationGate, LitValidationDisabledNotice } from "./LitValidationGate";

let beta: { available: boolean; flags: Record<string, boolean>; loading: boolean };
jest.mock("@/hooks/useBetaFeatures", () => ({ useBetaFeatures: () => beta }));

beforeEach(() => {
  beta = { available: true, flags: { lit_validation: true }, loading: false };
});

test("renders children when the lit_validation flag is on", () => {
  render(
    <LitValidationGate>
      <p>studies content</p>
    </LitValidationGate>,
  );
  expect(screen.getByText("studies content")).toBeInTheDocument();
});

test("renders the disabled notice, not the content, when the flag is off", () => {
  beta = { available: true, flags: {}, loading: false };
  render(
    <LitValidationGate>
      <p>studies content</p>
    </LitValidationGate>,
  );
  expect(screen.queryByText("studies content")).not.toBeInTheDocument();
  expect(screen.getByText(/isn't enabled/i)).toBeInTheDocument();
});

test("shows neither the content nor the notice while the flag state is loading", () => {
  beta = { available: false, flags: {}, loading: true };
  render(
    <LitValidationGate>
      <p>studies content</p>
    </LitValidationGate>,
  );
  expect(screen.queryByText("studies content")).not.toBeInTheDocument();
  expect(screen.queryByText(/isn't enabled/i)).not.toBeInTheDocument();
});

test("the notice explains the feature is a disabled beta", () => {
  render(<LitValidationDisabledNotice />);
  expect(screen.getByText(/isn't enabled/i)).toBeInTheDocument();
  expect(screen.getByText(/beta feature/i)).toBeInTheDocument();
});
