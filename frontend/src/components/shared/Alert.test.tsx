import { render, screen } from "@testing-library/react";
import { Alert } from "./Alert";

describe("Alert", () => {
  test("an error is announced as an alert", () => {
    render(<Alert tone="error">Upload failed.</Alert>);
    expect(screen.getByRole("alert")).toHaveTextContent("Upload failed.");
  });

  test.each(["warning", "info", "success"] as const)("a %s is a polite status, not an interruption", (tone) => {
    render(<Alert tone={tone}>Heads up</Alert>);
    expect(screen.getByRole("status")).toHaveTextContent("Heads up");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  test("a title and an action render with the message", () => {
    render(
      <Alert tone="warning" title="Import paused" action={<button type="button">Retry</button>}>
        The source did not answer.
      </Alert>,
    );
    const alert = screen.getByRole("status");
    expect(alert).toHaveTextContent("Import paused");
    expect(alert).toHaveTextContent("The source did not answer.");
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  test("supplied text is rendered as text, never as markup", () => {
    render(<Alert tone="error">{'<img src=x onerror="alert(1)">'}</Alert>);
    expect(screen.getByRole("alert").querySelector("img")).toBeNull();
    expect(screen.getByRole("alert")).toHaveTextContent('<img src=x onerror="alert(1)">');
  });
});
