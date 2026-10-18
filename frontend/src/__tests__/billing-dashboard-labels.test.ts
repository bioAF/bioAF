import { readFileSync } from "fs";
import { join } from "path";

// The dashboard's cost widgets say "Please enable the billing dashboard" and
// link to the Cost Center. The control they send the user to has to use the
// same name, or the user arrives looking for something the page never offers.
// "Billing Export" is the BigQuery mechanism underneath; the Google Cloud
// Console's own "Billing Export page" keeps its real name in the setup steps.

const SRC = join(__dirname, "..");
const read = (rel: string) => readFileSync(join(SRC, rel), "utf8");

const COST_CENTER = read("app/(app)/infrastructure/cost-center/page.tsx");
const SETUP_MODAL = read("components/infrastructure/BillingSetupModal.tsx");

describe("billing dashboard controls are named for what the widgets ask for", () => {
  it("the Cost Center offers Set Up Billing Dashboard", () => {
    expect(COST_CENTER).toMatch(/>\s*Set Up Billing Dashboard\s*</);
  });

  it("teardown is Teardown Billing Dashboard, on the button and the dialogs", () => {
    expect(COST_CENTER.match(/Teardown Billing Dashboard/g)?.length).toBe(4);
  });

  it("the Cost Center has no Billing Export control left", () => {
    expect(COST_CENTER).not.toMatch(/(Set Up|Teardown) Billing Export/);
  });

  it("the setup dialog is titled Set Up Billing Dashboard", () => {
    expect(SETUP_MODAL).toMatch(/title="Set Up Billing Dashboard"/);
    expect(SETUP_MODAL).not.toMatch(/title="Set Up Billing Export"/);
  });
});
