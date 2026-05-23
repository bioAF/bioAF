import { render, screen, fireEvent } from "@testing-library/react";
import { DashboardWidgetPicker } from "./DashboardWidgetPicker";
import type { WidgetDefinition } from "./registry";

function def(key: string, title: string): WidgetDefinition {
  return {
    key,
    title,
    description: `${key} description`,
    component: () => null,
    permissions: [],
    defaultForRoles: [],
  };
}

const available = [def("a", "Alpha"), def("b", "Bravo"), def("c", "Charlie")];

test("lists each available widget and reflects the enabled state", () => {
  render(
    <DashboardWidgetPicker
      available={available}
      enabledKeys={["a"]}
      onClose={jest.fn()}
      onSave={jest.fn()}
    />,
  );
  expect(screen.getByText("Alpha")).toBeInTheDocument();
  expect(screen.getByTestId("picker-toggle-a")).toBeChecked();
  expect(screen.getByTestId("picker-toggle-b")).not.toBeChecked();
});

test("save emits the selected keys in catalog order", () => {
  const onSave = jest.fn();
  render(
    <DashboardWidgetPicker
      available={available}
      enabledKeys={["a"]}
      onClose={jest.fn()}
      onSave={onSave}
    />,
  );
  fireEvent.click(screen.getByTestId("picker-toggle-c")); // add c
  fireEvent.click(screen.getByTestId("picker-toggle-a")); // remove a
  fireEvent.click(screen.getByTestId("picker-save"));
  expect(onSave).toHaveBeenCalledWith(["c"]);
});

test("shows a message when no widgets are available", () => {
  render(
    <DashboardWidgetPicker
      available={[]}
      enabledKeys={[]}
      onClose={jest.fn()}
      onSave={jest.fn()}
    />,
  );
  expect(screen.getByTestId("picker-empty")).toBeInTheDocument();
});

test("the close button calls onClose", () => {
  const onClose = jest.fn();
  render(
    <DashboardWidgetPicker
      available={available}
      enabledKeys={[]}
      onClose={onClose}
      onSave={jest.fn()}
    />,
  );
  fireEvent.click(screen.getByLabelText("Close"));
  expect(onClose).toHaveBeenCalled();
});
