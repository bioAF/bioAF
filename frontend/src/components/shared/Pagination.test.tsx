import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Pagination } from "./Pagination";

function setup(props: Partial<React.ComponentProps<typeof Pagination>>) {
  const onPageChange = jest.fn();
  const onPageSizeChange = jest.fn();
  render(<Pagination page={1} pageSize={25} total={0} onPageChange={onPageChange} {...props} />);
  return { onPageChange, onPageSizeChange };
}

test("no rows: no page controls and no range", () => {
  setup({ total: 0 });
  expect(screen.queryByRole("button", { name: "Previous" })).toBeNull();
  expect(screen.queryByText(/Showing/)).toBeNull();
});

test("a single page: no page controls", () => {
  setup({ total: 25 });
  expect(screen.queryByRole("button", { name: "Next" })).toBeNull();
});

test("first of several pages", async () => {
  const { onPageChange } = setup({ page: 1, total: 60 });
  expect(screen.getByText("Showing 1–25 of 60")).toBeInTheDocument();
  expect(screen.getByText("Page 1 of 3")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();
  await userEvent.click(screen.getByRole("button", { name: "Next" }));
  expect(onPageChange).toHaveBeenCalledWith(2);
});

test("final partial page", async () => {
  const { onPageChange } = setup({ page: 3, total: 60 });
  expect(screen.getByText("Showing 51–60 of 60")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
  await userEvent.click(screen.getByRole("button", { name: "Previous" }));
  expect(onPageChange).toHaveBeenCalledWith(2);
});

test("the size control is offered only where the list supports a choice", async () => {
  const onPageSizeChange = jest.fn();
  const { rerender } = render(<Pagination page={1} pageSize={25} total={10} onPageChange={() => {}} />);
  expect(screen.queryByLabelText("Rows per page")).toBeNull();
  rerender(<Pagination page={1} pageSize={25} total={10} onPageChange={() => {}} onPageSizeChange={onPageSizeChange} />);
  expect(screen.getByLabelText("Rows per page")).toHaveValue("25");
  expect(Array.from((screen.getByLabelText("Rows per page") as HTMLSelectElement).options).map((o) => o.value)).toEqual([
    "25",
    "50",
    "100",
  ]);
  await userEvent.selectOptions(screen.getByLabelText("Rows per page"), "100");
  expect(onPageSizeChange).toHaveBeenCalledWith(100);
});

test("the controls are a named navigation region", () => {
  setup({ total: 60 });
  expect(screen.getByRole("navigation", { name: "Pagination" })).toBeInTheDocument();
});
