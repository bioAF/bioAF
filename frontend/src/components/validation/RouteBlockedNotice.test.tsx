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
