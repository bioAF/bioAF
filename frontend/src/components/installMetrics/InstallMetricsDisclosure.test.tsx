import { render, screen } from "@/testing/renderWithProviders";
import userEvent from "@testing-library/user-event";
import { InstallMetricsDisclosure } from "./InstallMetricsDisclosure";
import { INSTALL_METRICS_NOTICE_URL } from "@/lib/installMetrics";

describe("InstallMetricsDisclosure", () => {
  it("offers both choices with neither selected", () => {
    render(<InstallMetricsDisclosure value={null} onChange={jest.fn()} alreadyCompleted={false} />);

    const share = screen.getByRole("radio", { name: "Share installation metrics" });
    const decline = screen.getByRole("radio", { name: "Do not share" });
    expect(share).not.toBeChecked();
    expect(decline).not.toBeChecked();
  });

  it("explains what is and is not reported, where it goes, and how to change it", () => {
    render(<InstallMetricsDisclosure value={null} onChange={jest.fn()} alreadyCompleted={false} />);

    expect(screen.getByText(/Reports use a random installation ID to connect your installation/)).toBeInTheDocument();
    expect(screen.getByText(/do not include names, email addresses, organization details, user activity, or scientific data/)).toBeInTheDocument();
    expect(screen.getByText(/Reports go to PostHog for the bioAF team/)).toBeInTheDocument();
    expect(screen.getByText(/Choosing Do not share sends nothing, including the initial report/)).toBeInTheDocument();
    expect(screen.getByText(/Settings > System Settings/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /installation metrics notice/i })).toHaveAttribute(
      "href",
      INSTALL_METRICS_NOTICE_URL,
    );
    expect(screen.queryByText(/first observed today/)).not.toBeInTheDocument();
  });

  it("lists the exact fields sent in the expanded details", () => {
    render(<InstallMetricsDisclosure value={null} onChange={jest.fn()} alreadyCompleted={false} />);

    expect(screen.getByText("What a report contains")).toBeInTheDocument();
    for (const field of ["distinct_id", "timestamp", "bioaf_version", "from_version", "$geoip_disable", "registration_kind"]) {
      expect(screen.getByText(field)).toBeInTheDocument();
    }
  });

  it("says only the current date is reported for an installation already set up", () => {
    render(<InstallMetricsDisclosure value={null} onChange={jest.fn()} alreadyCompleted />);

    expect(
      screen.getByText(
        "If you enable reporting now, we record that this installation was first observed today. We do not send earlier installation or upgrade history.",
      ),
    ).toBeInTheDocument();
  });

  it("reports the selected choice", async () => {
    const user = userEvent.setup();
    const onChange = jest.fn();
    render(<InstallMetricsDisclosure value={null} onChange={onChange} alreadyCompleted={false} />);

    await user.click(screen.getByRole("radio", { name: "Do not share" }));
    expect(onChange).toHaveBeenLastCalledWith("disabled");
    await user.click(screen.getByRole("radio", { name: "Share installation metrics" }));
    expect(onChange).toHaveBeenLastCalledWith("enabled");
  });

  it("shows a controlled selection", () => {
    render(<InstallMetricsDisclosure value="enabled" onChange={jest.fn()} alreadyCompleted={false} />);

    expect(screen.getByRole("radio", { name: "Share installation metrics" })).toBeChecked();
  });
});
