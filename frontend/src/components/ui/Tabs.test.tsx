import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TabList, TabPanel, useTabIds } from "./Tabs";

type Key = "a" | "b" | "c";

function Harness({ onSelect }: { onSelect?: (k: Key) => void }) {
  const [selected, setSelected] = useState<Key>("b");
  const ids = useTabIds();
  return (
    <>
      <TabList
        label="Sections"
        ids={ids}
        tabs={[
          { key: "a", label: "Alpha" },
          { key: "b", label: "Beta" },
          { key: "c", label: "Gamma" },
        ]}
        selected={selected}
        onSelect={(k) => {
          onSelect?.(k);
          setSelected(k);
        }}
      />
      <TabPanel ids={ids} tabKey={selected}>
        <p>Body {selected}</p>
      </TabPanel>
    </>
  );
}

test("selected state, roving tabindex and the panel's label", () => {
  render(<Harness />);
  expect(screen.getByRole("tablist", { name: "Sections" })).toBeInTheDocument();
  const beta = screen.getByRole("tab", { name: "Beta" });
  expect(beta).toHaveAttribute("aria-selected", "true");
  expect(beta).toHaveAttribute("tabindex", "0");
  expect(screen.getByRole("tab", { name: "Alpha" })).toHaveAttribute("tabindex", "-1");
  expect(screen.getByRole("tab", { name: "Alpha" })).not.toHaveAttribute("aria-controls");
  const panel = screen.getByRole("tabpanel", { name: "Beta" });
  expect(beta).toHaveAttribute("aria-controls", panel.id);
  expect(panel).toHaveTextContent("Body b");
  expect(panel).toHaveAttribute("tabindex", "0");
});

test("tabs are buttons that never submit a surrounding form", () => {
  render(<Harness />);
  for (const t of screen.getAllByRole("tab")) expect(t).toHaveAttribute("type", "button");
});

test("arrow keys move focus without selecting; Enter and Space select", async () => {
  const onSelect = jest.fn();
  render(<Harness onSelect={onSelect} />);
  screen.getByRole("tab", { name: "Beta" }).focus();
  await userEvent.keyboard("{ArrowRight}");
  expect(screen.getByRole("tab", { name: "Gamma" })).toHaveFocus();
  expect(onSelect).not.toHaveBeenCalled();
  await userEvent.keyboard("{ArrowRight}");
  expect(screen.getByRole("tab", { name: "Alpha" })).toHaveFocus();
  await userEvent.keyboard("{ArrowLeft}");
  expect(screen.getByRole("tab", { name: "Gamma" })).toHaveFocus();
  await userEvent.keyboard("{Home}");
  expect(screen.getByRole("tab", { name: "Alpha" })).toHaveFocus();
  await userEvent.keyboard("{End}");
  expect(screen.getByRole("tab", { name: "Gamma" })).toHaveFocus();
  await userEvent.keyboard("{Enter}");
  expect(onSelect).toHaveBeenLastCalledWith("c");
  expect(screen.getByRole("tabpanel", { name: "Gamma" })).toHaveTextContent("Body c");
  await userEvent.keyboard("{Home}[Space]");
  expect(onSelect).toHaveBeenLastCalledWith("a");
});

test("pointer selection", async () => {
  render(<Harness />);
  await userEvent.click(screen.getByRole("tab", { name: "Alpha" }));
  expect(screen.getByRole("tab", { name: "Alpha" })).toHaveAttribute("aria-selected", "true");
});

test("two tab sets on one page have distinct ids", () => {
  render(
    <>
      <Harness />
      <Harness />
    </>,
  );
  const ids = screen.getAllByRole("tab").map((t) => t.id);
  expect(new Set(ids).size).toBe(ids.length);
});
