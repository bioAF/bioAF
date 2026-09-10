import { render, screen } from "@testing-library/react";
import { RouteBlockedNotice } from "./RouteBlockedNotice";

describe("a route that turned out to be impossible", () => {
  it("renders nothing when the chosen route was fine", () => {
    const { container } = render(<RouteBlockedNotice blocked={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("names the route the reader picked", () => {
    render(
      <RouteBlockedNotice
        blocked={{ chosen: "deposit", reason: "This paper's GEO deposit holds no pre-processed data." }}
      />,
    );
    expect(screen.getByText(/Deposited data and available code/i)).toBeInTheDocument();
  });

  it("gives the reason in plain language, not a token", () => {
    render(
      <RouteBlockedNotice
        blocked={{ chosen: "deposit", reason: "This paper's GEO deposit holds no pre-processed data." }}
      />,
    );
    expect(screen.getByText(/holds no pre-processed data/i)).toBeInTheDocument();
  });

  it("says nothing was spent, because that is the reader's first question", () => {
    render(<RouteBlockedNotice blocked={{ chosen: "pipeline", reason: "No raw reads are published." }} />);
    expect(screen.getByText(/nothing has been spent/i)).toBeInTheDocument();
  });

  it("tells the reader they can choose a different route", () => {
    render(<RouteBlockedNotice blocked={{ chosen: "pipeline", reason: "No raw reads are published." }} />);
    expect(screen.getByText(/choose a different route below/i)).toBeInTheDocument();
  });
});

// change_7.2 section 1: a missing adapter, a missing input and a missing authorization are three
// different refusals with three different remedies. One wording for all three tells a reader nothing
// about which of them to act on, and an authorization failure misreported as a missing feature sends
// a lab to the wrong remedy.

test("a missing adapter is named as a limit of bioAF", () => {
  render(
    <RouteBlockedNotice
      blocked={{ chosen: "pipeline", action: "no_adapter", reason: "bioAF has no adapter for EGA." }}
    />,
  );
  expect(screen.getByText(/bioAF cannot read this archive/i)).toBeInTheDocument();
});

test("a missing input is named as a fact about the deposit", () => {
  render(
    <RouteBlockedNotice
      blocked={{ chosen: "deposit", action: "no_input", reason: "GSE1 publishes no pre-processed data." }}
    />,
  );
  expect(screen.getByText(/deposit holds nothing/i)).toBeInTheDocument();
});

test("an authorization failure is not described as a missing feature", () => {
  render(
    <RouteBlockedNotice
      blocked={{ chosen: "pipeline", action: "not_authorized", reason: "This organization is not authorized." }}
    />,
  );
  expect(screen.getByText(/not authorised to reach/i)).toBeInTheDocument();
  expect(screen.queryByText(/bioAF cannot read this archive/i)).not.toBeInTheDocument();
});

test("an unrecorded action leaves the notice exactly as it was", () => {
  render(<RouteBlockedNotice blocked={{ chosen: "deposit", reason: "some older reason" }} />);
  expect(screen.getByText(/some older reason/)).toBeInTheDocument();
});
