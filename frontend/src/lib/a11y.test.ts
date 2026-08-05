import { clickableRow, clickableCard } from "./a11y";

type KeyArgs = {
  key: string;
  target?: object;
  currentTarget?: object;
  preventDefault?: () => void;
};

/** Minimal stand-in for the parts of a React KeyboardEvent these helpers read. */
function keyEvent({ key, target, currentTarget, preventDefault }: KeyArgs) {
  const el = {};
  return {
    key,
    target: target ?? currentTarget ?? el,
    currentTarget: currentTarget ?? el,
    preventDefault: preventDefault ?? jest.fn(),
    // Cast because these helpers read four fields of a React KeyboardEvent and
    // constructing a real one adds noise without adding coverage.
  } as unknown as Parameters<ReturnType<typeof clickableRow>["onKeyDown"]>[0];
}

describe("clickableRow", () => {
  test("keeps the click behaviour it is replacing", () => {
    const activate = jest.fn();
    clickableRow(activate).onClick();
    expect(activate).toHaveBeenCalledTimes(1);
  });

  test("puts the element in the tab order", () => {
    expect(clickableRow(jest.fn()).tabIndex).toBe(0);
  });

  test.each(["Enter", " "])("activates on %s", (key) => {
    const activate = jest.fn();
    clickableRow(activate).onKeyDown(keyEvent({ key }));
    expect(activate).toHaveBeenCalledTimes(1);
  });

  test("ignores other keys, so Tab and arrows still navigate", () => {
    const activate = jest.fn();
    const { onKeyDown } = clickableRow(activate);
    for (const key of ["Tab", "ArrowDown", "Escape", "a"]) {
      onKeyDown(keyEvent({ key }));
    }
    expect(activate).not.toHaveBeenCalled();
  });

  test("suppresses the default, so Space does not scroll the page", () => {
    const preventDefault = jest.fn();
    clickableRow(jest.fn()).onKeyDown(keyEvent({ key: " ", preventDefault }));
    expect(preventDefault).toHaveBeenCalled();
  });

  test("a keypress on a nested control does not also fire the row", () => {
    // Rows carry their own buttons (Delete, Stop, a checkbox). Without the
    // target check, pressing Enter on a row's Delete button would delete the
    // record AND navigate to it, because keydown bubbles to the row handler.
    const activate = jest.fn();
    const row = {};
    const innerButton = {};
    clickableRow(activate).onKeyDown(
      keyEvent({ key: "Enter", target: innerButton, currentTarget: row }),
    );
    expect(activate).not.toHaveBeenCalled();
  });

  test("adds no role, so a table row is still announced as a row", () => {
    // role="button" here would strip the row out of the table's accessibility
    // tree and undo the scope="col" association work.
    expect(clickableRow(jest.fn())).not.toHaveProperty("role");
  });
});

describe("clickableCard", () => {
  test("is announced as a button, since a div carries no semantics of its own", () => {
    expect(clickableCard(jest.fn()).role).toBe("button");
  });

  test("takes an accessible name when the visible content is not descriptive", () => {
    expect(clickableCard(jest.fn(), "Open notification")["aria-label"]).toBe(
      "Open notification",
    );
  });

  test("omits aria-label entirely when none is given, rather than setting undefined", () => {
    // An explicit aria-label={undefined} is harmless in React, but an empty
    // string would silently erase the name computed from the card's content.
    expect(clickableCard(jest.fn())).not.toHaveProperty("aria-label");
  });

  test("activates by keyboard the same way a row does", () => {
    const activate = jest.fn();
    clickableCard(activate).onKeyDown(keyEvent({ key: "Enter" }));
    expect(activate).toHaveBeenCalledTimes(1);
  });
});
