/**
 * The shared filing follower reports an upload's explicit outcome. Leaving the pending list does
 * not mean filed: a cancelled or failed upload leaves it too.
 */
import { act, renderHook } from "@testing-library/react";
import { useState } from "react";
import {
  pendingFrom,
  useUploadFiling,
  type FilingItem,
  type ReceivedNotFiled,
} from "@/components/files/uploadFiling";

jest.mock("@/lib/api", () => ({ api: { get: jest.fn(), post: jest.fn() } }));
import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;

const receipt: ReceivedNotFiled = {
  code: "upload_received_not_filed",
  detail: "Received, not yet filed.",
  details: { upload_id: "u-1", reference: "UPL-7F3A21" },
};

function outcome(value: Record<string, unknown>) {
  mockGet.mockImplementation((path: string) =>
    path === "/api/files/uploads/u-1"
      ? Promise.resolve({
          upload_id: "u-1",
          reference: "UPL-7F3A21",
          stage: "received",
          ...value,
        })
      : Promise.resolve({ uploads: [] }),
  );
}

function follow(onFiled = jest.fn()) {
  const { result } = renderHook(() => {
    const [items, setItems] = useState<(FilingItem & { error?: string })[]>([
      pendingFrom(receipt),
    ]);
    useUploadFiling(items, setItems, onFiled);
    return items;
  });
  return { items: () => result.current, onFiled };
}

async function poll() {
  await act(async () => {
    jest.advanceTimersByTime(30_000);
  });
}

beforeEach(() => {
  jest.useFakeTimers();
  mockGet.mockReset();
});
afterEach(() => jest.useRealTimers());

test("an upload cancelled elsewhere is shown as cancelled, never as filed", async () => {
  outcome({ outcome: "cancelled", stage: "cancelled" });
  const { items, onFiled } = follow();
  await poll();
  expect(items()[0].status).toBe("cancelled");
  expect(onFiled).not.toHaveBeenCalled();
});

test("an upload whose filing failed is shown as an error, never as filed", async () => {
  outcome({ outcome: "failed", stage: "failed" });
  const { items, onFiled } = follow();
  await poll();
  expect(items()[0].status).toBe("error");
  expect(items()[0].error).toBe(
    "Could not be filed. Upload the file again. Reference UPL-7F3A21.",
  );
  expect(onFiled).not.toHaveBeenCalled();
});

test("only a filed outcome turns the upload done", async () => {
  outcome({ outcome: "filed", stage: "filed", file_id: 9 });
  const { items, onFiled } = follow();
  await poll();
  expect(items()[0].status).toBe("complete");
  expect(onFiled).toHaveBeenCalledTimes(1);
});

test("a pending upload stays pending, and offers Retry now once retries run out", async () => {
  outcome({ outcome: "pending", state: "retrying" });
  const { items } = follow();
  await poll();
  expect(items()[0].status).toBe("pending");

  outcome({ outcome: "pending", state: "needs_attention" });
  await poll();
  expect(items()[0].status).toBe("needs_attention");
});

test("an unreadable outcome keeps what is shown", async () => {
  mockGet.mockRejectedValue(new Error("offline"));
  const { items } = follow();
  await poll();
  expect(items()[0].status).toBe("pending");
});
