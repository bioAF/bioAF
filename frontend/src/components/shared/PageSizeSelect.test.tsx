/**
 * One page-size control, decided once.
 *
 * There was no per-page control anywhere in the app, and the hardcoded values
 * had already drifted: 100 in five places, plus "50", "25", "24" and "20". A
 * select per page would have made that worse, so this is the only one.
 */
import { render, screen, fireEvent } from "@/testing/renderWithProviders";
import { PageSizeSelect, PAGE_SIZES, DEFAULT_PAGE_SIZE } from "./PageSizeSelect";

test("the owner's options, in the owner's order", () => {
  expect(PAGE_SIZES).toEqual([25, 50, 100]);
  expect(DEFAULT_PAGE_SIZE).toBe(25);
});

test("it renders every option and marks the current one", () => {
  render(<PageSizeSelect value={50} onChange={jest.fn()} />);
  const select = screen.getByRole("combobox", { name: /rows per page/i });
  expect(select).toHaveValue("50");
  expect(
    Array.from(select.querySelectorAll("option")).map((o) => o.value),
  ).toEqual(["25", "50", "100"]);
});

test("it reports a number, not the string the DOM gives it", () => {
  // page_size goes into a URL and then into a LIMIT. "50" and 50 read the same
  // in a template string, so this is the kind of thing that only surfaces once
  // arithmetic is done on it.
  const onChange = jest.fn();
  render(<PageSizeSelect value={25} onChange={onChange} />);
  fireEvent.change(screen.getByRole("combobox", { name: /rows per page/i }), {
    target: { value: "100" },
  });
  expect(onChange).toHaveBeenCalledWith(100);
  expect(typeof onChange.mock.calls[0][0]).toBe("number");
});

test("the control is labelled for a screen reader, not just visually", () => {
  render(<PageSizeSelect value={25} onChange={jest.fn()} />);
  expect(screen.getByRole("combobox", { name: /rows per page/i })).toBeInTheDocument();
});
