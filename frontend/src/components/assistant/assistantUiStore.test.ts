import { assistantUiStore } from "./assistantUiStore";

beforeEach(() => {
  assistantUiStore.close();
});

test("starts closed", () => {
  expect(assistantUiStore.getSnapshot()).toBe(false);
});

test("open/close/toggle flip the state", () => {
  assistantUiStore.open();
  expect(assistantUiStore.getSnapshot()).toBe(true);
  assistantUiStore.close();
  expect(assistantUiStore.getSnapshot()).toBe(false);
  assistantUiStore.toggle();
  expect(assistantUiStore.getSnapshot()).toBe(true);
  assistantUiStore.toggle();
  expect(assistantUiStore.getSnapshot()).toBe(false);
});

test("notifies subscribers on change and stops after unsubscribe", () => {
  const listener = jest.fn();
  const unsubscribe = assistantUiStore.subscribe(listener);
  assistantUiStore.open();
  expect(listener).toHaveBeenCalledTimes(1);
  unsubscribe();
  assistantUiStore.close();
  expect(listener).toHaveBeenCalledTimes(1);
});
