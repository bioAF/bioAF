import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AssociatePaperModal } from "./AssociatePaperModal";

jest.mock("@/lib/api", () => ({ api: { get: jest.fn() } }));

jest.mock("@/lib/literature", () => {
  const actual = jest.requireActual("@/lib/literature");
  return {
    ...actual,
    literature: { ...actual.literature, addAssociation: jest.fn() },
  };
});

import { api } from "@/lib/api";
import { literature } from "@/lib/literature";

const mockGet = api.get as jest.Mock;
const mockAssoc = literature.addAssociation as jest.Mock;

beforeEach(() => {
  mockGet.mockReset();
  mockAssoc.mockReset();
  mockGet.mockImplementation((url: string) => {
    if (url.startsWith("/api/projects")) {
      return Promise.resolve({ projects: [{ id: 11, name: "Atlas" }], total: 1 });
    }
    if (url.startsWith("/api/experiments")) {
      return Promise.resolve({
        experiments: [{ id: 101, name: "Exp One" }],
        total: 1,
        page: 1,
        page_size: 100,
      });
    }
    return Promise.resolve({});
  });
  mockAssoc.mockResolvedValue({});
});

test("renders nothing when there are no papers", () => {
  const { container } = render(
    <AssociatePaperModal paperIds={[]} onClose={jest.fn()} onAssociated={jest.fn()} />,
  );
  expect(container).toBeEmptyDOMElement();
});

test("associates at project scope when only a project is chosen", async () => {
  const onClose = jest.fn();
  const onAssociated = jest.fn();
  render(
    <AssociatePaperModal paperIds={[5]} onClose={onClose} onAssociated={onAssociated} />,
  );
  await waitFor(() => expect(mockGet).toHaveBeenCalled());
  expect(screen.getByText("Associate paper")).toBeInTheDocument();

  const selects = screen.getAllByRole("combobox");
  await userEvent.selectOptions(selects[0], "11");
  await userEvent.click(screen.getByRole("button", { name: /^Associate$/ }));

  await waitFor(() => expect(mockAssoc).toHaveBeenCalled());
  expect(mockAssoc).toHaveBeenCalledWith(5, { scope_type: "project", scope_id: 11 });
  expect(onAssociated).toHaveBeenCalled();
  expect(onClose).toHaveBeenCalled();
});

test("associates at experiment scope when an experiment is chosen", async () => {
  render(
    <AssociatePaperModal paperIds={[7]} onClose={jest.fn()} onAssociated={jest.fn()} />,
  );
  await waitFor(() => expect(mockGet).toHaveBeenCalled());

  const selects = screen.getAllByRole("combobox");
  await userEvent.selectOptions(selects[0], "11");
  await waitFor(() =>
    expect(screen.getByRole("option", { name: "Exp One" })).toBeInTheDocument(),
  );
  await userEvent.selectOptions(selects[1], "101");
  await userEvent.click(screen.getByRole("button", { name: /^Associate$/ }));

  await waitFor(() => expect(mockAssoc).toHaveBeenCalled());
  expect(mockAssoc).toHaveBeenCalledWith(7, {
    scope_type: "experiment",
    scope_id: 101,
  });
});
