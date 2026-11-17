/**
 * The webhook screen speaks plainly: a refused address is explained beside the address field inside
 * the open dialog (each kind of refusal in its own words), events and delivery states have readable
 * names with their technical identifiers still available, and a delivery is sent again with
 * "Send again".
 */
import { render, screen, fireEvent, waitFor, within } from "@/testing/renderWithProviders";
import { WebhooksTab } from "./WebhooksTab";

jest.mock("@/lib/api", () => {
  class ApiError extends Error {
    constructor(
      public status: number,
      message: string,
      public code?: string,
      public details?: Record<string, unknown>,
    ) {
      super(message);
    }
  }
  return { api: { get: jest.fn(), post: jest.fn(), patch: jest.fn(), delete: jest.fn() }, ApiError };
});
jest.mock("@/lib/integrationsApi", () => ({
  integrationsApi: {
    listWebhooks: jest.fn(),
    createWebhook: jest.fn(),
    updateWebhook: jest.fn(),
    disableWebhook: jest.fn(),
    rotateWebhookSecret: jest.fn(),
    listWebhookDeliveries: jest.fn(),
    replayWebhookDelivery: jest.fn(),
    fireTestWebhook: jest.fn(),
  },
}));

import { ApiError } from "@/lib/api";
import { integrationsApi } from "@/lib/integrationsApi";
const api = integrationsApi as unknown as Record<string, jest.Mock>;

const DENIED =
  "bioAF isn’t allowed to connect to this address. Check the address with the administrator of the system you want to connect.";

const sub = {
  id: 7,
  name: "LIMS bridge",
  url: "https://lims.example.com/hooks",
  events: ["experiment.status_changed"],
  is_active: true,
  created_at: "2026-11-16T00:00:00Z",
};

function refused(reason: string, message: string) {
  // The client builds this from the {detail, code, details} envelope.
  return new (ApiError as unknown as new (...a: unknown[]) => Error)(422, message, "webhook_destination_refused", {
    reason,
  });
}

beforeEach(() => {
  Object.values(api).forEach((m) => m.mockReset());
  api.listWebhooks.mockResolvedValue([sub]);
  api.listWebhookDeliveries.mockResolvedValue([]);
});

async function openCreate() {
  render(<WebhooksTab />);
  fireEvent.click(await screen.findByRole("button", { name: "Create Webhook" }));
  const dialog = await screen.findByRole("dialog");
  fireEvent.change(within(dialog).getByLabelText("Name"), { target: { value: "x" } });
  fireEvent.change(within(dialog).getByLabelText("URL"), { target: { value: "https://10.0.0.5/h" } });
  fireEvent.click(within(dialog).getByRole("checkbox", { name: /Experiment created/ }));
  return dialog;
}

test("a refused address is explained beside the address field inside the dialog", async () => {
  api.createWebhook.mockRejectedValue(refused("destination_denied", DENIED));
  const dialog = await openCreate();
  fireEvent.click(within(dialog).getByRole("button", { name: "Create" }));

  const message = await within(dialog).findByText(DENIED);
  const url = within(dialog).getByLabelText("URL");
  expect(url).toHaveAttribute("aria-invalid", "true");
  expect(url.getAttribute("aria-describedby")).toBe(message.id);
  expect(screen.queryByText(/destination_denied|refused:/)).not.toBeInTheDocument();

  // Editing the address clears the explanation.
  fireEvent.change(url, { target: { value: "https://lims.example.com/h" } });
  expect(within(dialog).queryByText(DENIED)).not.toBeInTheDocument();
});

test.each([
  ["invalid_destination", "This isn’t a valid webhook address."],
  ["dns_failed", "bioAF couldn’t find this address."],
])("a %s refusal shows its own explanation", async (reason, text) => {
  api.createWebhook.mockRejectedValue(refused(reason, `${text} More.`));
  const dialog = await openCreate();
  fireEvent.click(within(dialog).getByRole("button", { name: "Create" }));
  expect(await within(dialog).findByText(`${text} More.`)).toBeInTheDocument();
});

test("the edit dialog explains a refused address the same way", async () => {
  api.updateWebhook.mockRejectedValue(refused("destination_denied", DENIED));
  render(<WebhooksTab />);
  fireEvent.click(await screen.findByText("LIMS bridge"));
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  const dialog = await screen.findByRole("dialog", { name: /Edit LIMS bridge/ });
  fireEvent.click(within(dialog).getByRole("button", { name: "Save Changes" }));
  expect(await within(dialog).findByText(DENIED)).toBeInTheDocument();
});

test("events and delivery states have readable names, with identifiers still available", async () => {
  api.listWebhookDeliveries.mockResolvedValue([
    { id: 1, subscription_id: 7, event_id: "e1", event_type: "experiment.status_changed", status: "dead_letter",
      attempt_count: 8, next_attempt_at: null, last_response_status: 500, last_attempted_at: null,
      created_at: "2026-11-16T00:00:00Z", delivered_at: null },
    { id: 2, subscription_id: 7, event_id: "e2", event_type: "webhook.test", status: "delivered",
      attempt_count: 1, next_attempt_at: null, last_response_status: 200, last_attempted_at: null,
      created_at: "2026-11-16T00:00:00Z", delivered_at: "2026-11-16T00:00:01Z" },
  ]);
  render(<WebhooksTab />);
  fireEvent.click(await screen.findByText("LIMS bridge"));
  const dialog = await screen.findByRole("dialog");

  const event = await within(dialog).findByText("Experiment status changed");
  expect(event).toHaveAttribute("title", "experiment.status_changed");
  expect(within(dialog).getByText("Delivery stopped after repeated failures", { selector: "span" })).toHaveAttribute(
    "title",
    "dead_letter",
  );
  expect(within(dialog).getByText("Test event")).toBeInTheDocument();
  expect(within(dialog).queryByText("dead_letter")).not.toBeInTheDocument();
  expect(within(dialog).queryByRole("button", { name: "Replay" })).not.toBeInTheDocument();
  expect(within(dialog).getByRole("option", { name: "Delivery stopped after repeated failures" })).toHaveValue(
    "dead_letter",
  );

  api.replayWebhookDelivery.mockResolvedValue({});
  fireEvent.click(within(dialog).getAllByRole("button", { name: "Send again" })[0]);
  await waitFor(() => expect(api.replayWebhookDelivery).toHaveBeenCalledWith(1));
});

test("the create dialog lists events by name with the identifier beside each", async () => {
  render(<WebhooksTab />);
  fireEvent.click(await screen.findByRole("button", { name: "Create Webhook" }));
  const dialog = await screen.findByRole("dialog");
  const box = within(dialog).getByRole("checkbox", { name: /Sample QC changed/ });
  expect(box).toBeInTheDocument();
  expect(within(dialog).getByText("sample.qc_changed")).toBeInTheDocument();
});

test("a refused test event is explained inside the open subscription dialog", async () => {
  api.fireTestWebhook.mockRejectedValue(refused("destination_denied", DENIED));
  render(<WebhooksTab />);
  fireEvent.click(await screen.findByText("LIMS bridge"));
  const dialog = await screen.findByRole("dialog");
  fireEvent.click(within(dialog).getByRole("button", { name: "Send test event" }));
  expect(await within(dialog).findByText(DENIED)).toBeInTheDocument();
});

test("a delivery with no response yet shows the shared not-set dash", async () => {
  api.listWebhookDeliveries.mockResolvedValue([
    { id: 3, subscription_id: 7, event_id: "e3", event_type: "file.ready", status: "pending",
      attempt_count: 0, next_attempt_at: null, last_response_status: null, last_attempted_at: null,
      created_at: "2026-11-16T00:00:00Z", delivered_at: null },
  ]);
  render(<WebhooksTab />);
  fireEvent.click(await screen.findByText("LIMS bridge"));
  const dialog = await screen.findByRole("dialog");
  expect(await within(dialog).findByLabelText("Response not set")).toBeInTheDocument();
  expect(within(dialog).getByText("Waiting to send", { selector: "span" })).toBeInTheDocument();
});
