import { createRef, useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Field } from "./Field";
import { Input, Select, Textarea } from "./Input";

describe("Input, Select and Textarea", () => {
  test("forward refs to the native element", () => {
    const input = createRef<HTMLInputElement>();
    const select = createRef<HTMLSelectElement>();
    const textarea = createRef<HTMLTextAreaElement>();
    render(
      <>
        <Input ref={input} aria-label="a" />
        <Select ref={select} aria-label="b">
          <option>x</option>
        </Select>
        <Textarea ref={textarea} aria-label="c" />
      </>,
    );
    expect(input.current).toBeInstanceOf(HTMLInputElement);
    expect(select.current).toBeInstanceOf(HTMLSelectElement);
    expect(textarea.current).toBeInstanceOf(HTMLTextAreaElement);
  });

  test("pass native attributes and constraints through", () => {
    render(
      <form id="f">
        <Input
          aria-label="n"
          id="given"
          name="count"
          form="f"
          type="number"
          min={0}
          max={10}
          step={2}
          required
          readOnly
          autoComplete="off"
          aria-describedby="elsewhere"
          data-testid="n"
        />
        <Select aria-label="s" disabled required name="pick">
          <option value="">None</option>
        </Select>
        <Textarea aria-label="t" rows={4} maxLength={20} placeholder="Say more" />
      </form>,
    );
    const n = screen.getByTestId("n");
    expect(n).toHaveAttribute("id", "given");
    expect(n).toHaveAttribute("name", "count");
    expect(n).toHaveAttribute("form", "f");
    expect(n).toHaveAttribute("type", "number");
    expect(n).toHaveAttribute("min", "0");
    expect(n).toHaveAttribute("max", "10");
    expect(n).toHaveAttribute("step", "2");
    expect(n).toBeRequired();
    expect(n).toHaveAttribute("readonly");
    expect(n).toHaveAttribute("autocomplete", "off");
    expect(n).toHaveAttribute("aria-describedby", "elsewhere");
    expect(screen.getByLabelText("s")).toBeDisabled();
    expect(screen.getByLabelText("s")).toBeRequired();
    expect(screen.getByLabelText("t")).toHaveAttribute("rows", "4");
    expect(screen.getByLabelText("t")).toHaveAttribute("maxlength", "20");
    expect(screen.getByLabelText("t")).toHaveAttribute("placeholder", "Say more");
  });

  test("work controlled and uncontrolled", async () => {
    function Controlled() {
      const [v, setV] = useState("a");
      return (
        <>
          <Input aria-label="controlled" value={v} onChange={(e) => setV(e.target.value.toUpperCase())} />
          <span data-testid="echo">{v}</span>
        </>
      );
    }
    render(
      <>
        <Controlled />
        <Input aria-label="uncontrolled" defaultValue="x" />
      </>,
    );
    await userEvent.type(screen.getByLabelText("controlled"), "b");
    expect(screen.getByTestId("echo")).toHaveTextContent("AB");
    await userEvent.type(screen.getByLabelText("uncontrolled"), "y");
    expect(screen.getByLabelText("uncontrolled")).toHaveValue("xy");
  });

  test("a caller's class is added, not substituted", () => {
    render(<Input aria-label="w" className="font-mono" />);
    expect(screen.getByLabelText("w").className).toMatch(/font-mono/);
    expect(screen.getByLabelText("w").className).toMatch(/border/);
  });
});

describe("Field", () => {
  test("labels its control and describes it with the hint", () => {
    render(
      <Field label="Name" hint="Shown to collaborators">
        {(control) => <Input {...control} />}
      </Field>,
    );
    const input = screen.getByLabelText("Name");
    expect(input).toHaveAccessibleDescription("Shown to collaborators");
    expect(input).not.toHaveAttribute("aria-invalid");
  });

  test("an error marks the control invalid and is part of its description", () => {
    render(
      <Field label="Name" hint="Required" error="Name is taken">
        {(control) => <Input {...control} />}
      </Field>,
    );
    const input = screen.getByLabelText("Name");
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(input).toHaveAccessibleDescription("Required Name is taken");
    expect(screen.getByText("Name is taken")).toBeInTheDocument();
  });

  test("merges a caller's own description ids", () => {
    render(
      <>
        <p id="extra">Extra context</p>
        <Field label="Name" hint="Hint" describedBy="extra">
          {(control) => <Input {...control} />}
        </Field>
      </>,
    );
    expect(screen.getByLabelText("Name")).toHaveAccessibleDescription("Extra context Hint");
  });

  test("ids stay unique with several fields and dialogs mounted", () => {
    render(
      <>
        {[1, 2, 3].map((i) => (
          <Field key={i} label="Name" hint="Hint" error="Bad">
            {(control) => <Input {...control} />}
          </Field>
        ))}
      </>,
    );
    const inputs = screen.getAllByLabelText("Name");
    expect(new Set(inputs.map((i) => i.id)).size).toBe(3);
    const described = inputs.map((i) => i.getAttribute("aria-describedby"));
    expect(new Set(described).size).toBe(3);
  });

  test("a caller-supplied id is kept", () => {
    render(
      <Field label="Name" id="project-name">
        {(control) => <Input {...control} />}
      </Field>,
    );
    expect(screen.getByLabelText("Name")).toHaveAttribute("id", "project-name");
  });

  test("works with Select and Textarea", () => {
    render(
      <>
        <Field label="Scope">
          {(control) => (
            <Select {...control}>
              <option>internal</option>
            </Select>
          )}
        </Field>
        <Field label="Notes">{(control) => <Textarea {...control} />}</Field>
      </>,
    );
    expect(screen.getByLabelText("Scope").tagName).toBe("SELECT");
    expect(screen.getByLabelText("Notes").tagName).toBe("TEXTAREA");
  });
});
