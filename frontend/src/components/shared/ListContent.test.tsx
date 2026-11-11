import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ListContent } from "./ListContent";
import { EmptyState } from "./EmptyState";

const empty = <EmptyState title="No things found" message="Create one to begin." action={<button type="button">New thing</button>} />;

function renderList(props: Partial<React.ComponentProps<typeof ListContent>>) {
  return render(
    <ListContent loading={false} error={null} onRetry={() => {}} empty={false} emptyState={empty} {...props}>
      <table>
        <tbody>
          <tr>
            <td>row</td>
          </tr>
        </tbody>
      </table>
    </ListContent>,
  );
}

test("initial loading shows the table skeleton and a status line, not rows", () => {
  renderList({ loading: true });
  expect(screen.getByRole("status")).toHaveTextContent("Loading");
  expect(screen.getAllByTestId("skeleton-row").length).toBeGreaterThan(0);
  expect(screen.queryByText("row")).toBeNull();
});

test("an initial failure offers a retry and never says the list is empty", async () => {
  const onRetry = jest.fn();
  renderList({ error: "Things could not be loaded.", empty: true, onRetry });
  expect(screen.getByTestId("error-message")).toHaveTextContent("Things could not be loaded.");
  expect(screen.queryByText("No things found")).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(onRetry).toHaveBeenCalledTimes(1);
});

test("a successful empty answer shows the empty state", () => {
  renderList({ empty: true });
  expect(screen.getByRole("heading", { name: "No things found" })).toBeInTheDocument();
  expect(screen.getByText("Create one to begin.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "New thing" })).toBeInTheDocument();
  expect(screen.queryByText("row")).toBeNull();
});

test("rows render when there is data", () => {
  renderList({});
  expect(screen.getByText("row")).toBeInTheDocument();
});

test("an empty state without an action renders no button", () => {
  render(<EmptyState title="Nothing here" message="Nothing yet." />);
  expect(screen.queryByRole("button")).toBeNull();
});
