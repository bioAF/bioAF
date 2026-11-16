import { render, screen } from "@/testing/renderWithProviders";
import userEvent from "@testing-library/user-event";
import { InstallMetricsDisclosure, InstallMetricsSummary } from "./InstallMetricsDisclosure";
import { INSTALL_METRICS_NOTICE_URL } from "@/lib/installMetrics";

describe("InstallMetricsSummary", () => {
  it("says briefly what is shared, what never is, and that nothing is sent without agreement", () => {
    render(<InstallMetricsSummary />);

    expect(
      screen.getByText("Share basic installation and update information to help guide bioAF’s development."),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Reports include a random installation ID, reporting dates, and bioAF version numbers. Names, lab details, user activity, and research data are never included.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Nothing is sent unless you agree. You can change your choice anytime in Settings > System Settings.",
      ),
    ).toBeInTheDocument();
  });

  it("keeps the explanation and the full notice behind Learn more, with no field schema", () => {
    render(<InstallMetricsSummary />);

    expect(screen.getByText("Learn more about installation metrics")).toBeInTheDocument();
    expect(screen.getByText(/tracks installs, updates, and activated features/)).toBeInTheDocument();
    expect(screen.getByText(/how many installations exist in the wild/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Read the full installation metrics notice" })).toHaveAttribute(
      "href",
      INSTALL_METRICS_NOTICE_URL,
    );
    for (const gone of ["distinct_id", "$geoip_disable", "registration_kind", "What a report contains", "PostHog"]) {
      expect(screen.queryByText(new RegExp(gone.replace("$", "\\$")))).not.toBeInTheDocument();
    }
  });
});

describe("InstallMetricsDisclosure", () => {
  it("offers both choices with neither selected", () => {
    render(<InstallMetricsDisclosure value={null} onChange={jest.fn()} />);

    expect(screen.getByRole("radio", { name: "Share installation metrics" })).not.toBeChecked();
    expect(screen.getByRole("radio", { name: "Do not share" })).not.toBeChecked();
    expect(screen.getByText(/Nothing is sent unless you agree/)).toBeInTheDocument();
  });

  it("reports the selected choice", async () => {
    const user = userEvent.setup();
    const onChange = jest.fn();
    render(<InstallMetricsDisclosure value={null} onChange={onChange} />);

    await user.click(screen.getByRole("radio", { name: "Do not share" }));
    expect(onChange).toHaveBeenLastCalledWith("disabled");
    await user.click(screen.getByRole("radio", { name: "Share installation metrics" }));
    expect(onChange).toHaveBeenLastCalledWith("enabled");
  });

  it("shows a controlled selection", () => {
    render(<InstallMetricsDisclosure value="enabled" onChange={jest.fn()} />);

    expect(screen.getByRole("radio", { name: "Share installation metrics" })).toBeChecked();
  });
});
