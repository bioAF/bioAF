import { render, screen, fireEvent } from "@testing-library/react";
import { PasswordResetActions } from "./PasswordResetActions";

const active = { id: 1, email: "u@lab.org", status: "active" };
const deactivated = { id: 2, email: "d@lab.org", status: "deactivated" };

test("offers both the reset email and the manual option when SMTP is configured", () => {
  render(
    <PasswordResetActions
      user={active}
      smtpConfigured={true}
      onSendResetEmail={jest.fn()}
      onSetManualPassword={jest.fn()}
    />,
  );
  expect(screen.getByRole("button", { name: /send password reset link/i })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /set password manually/i })).toBeInTheDocument();
});

test("offers only the manual option when SMTP is not configured", () => {
  render(
    <PasswordResetActions
      user={active}
      smtpConfigured={false}
      onSendResetEmail={jest.fn()}
      onSetManualPassword={jest.fn()}
    />,
  );
  expect(screen.queryByRole("button", { name: /send password reset link/i })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: /set password manually/i })).toBeInTheDocument();
});

test("shows no password actions for a deactivated user", () => {
  render(
    <PasswordResetActions
      user={deactivated}
      smtpConfigured={true}
      onSendResetEmail={jest.fn()}
      onSetManualPassword={jest.fn()}
    />,
  );
  expect(screen.queryByRole("button", { name: /send password reset link/i })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /set password manually/i })).not.toBeInTheDocument();
});

test("invokes the right callback for each action", () => {
  const onSendResetEmail = jest.fn();
  const onSetManualPassword = jest.fn();
  render(
    <PasswordResetActions
      user={active}
      smtpConfigured={true}
      onSendResetEmail={onSendResetEmail}
      onSetManualPassword={onSetManualPassword}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: /send password reset link/i }));
  expect(onSendResetEmail).toHaveBeenCalledWith(active);

  fireEvent.click(screen.getByRole("button", { name: /set password manually/i }));
  expect(onSetManualPassword).toHaveBeenCalledWith(active);
});
