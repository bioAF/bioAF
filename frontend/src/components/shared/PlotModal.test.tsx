import { render, screen } from "@testing-library/react";
import { PlotModal } from "./PlotModal";

jest.mock("@/hooks/useContentUrl", () => ({
  useFileContentUrl: () => "blob:fake",
  usePlotThumbnailContentUrl: () => "blob:fake-thumb",
}));

/**
 * Measured on the demo before this was changed: a plot thumbnail rendered
 * 510x139 inline, and "Expand" showed the same 2200x600 source at 512x140. The
 * panel was max-w-lg, which is about the width of the grid cell the thumbnail
 * already sits in, so expanding gained nothing.
 *
 * These assertions name the two constraints that caused it. They check classes,
 * which is usually a change-detector smell, but here the class IS the decision:
 * there is no other observable in jsdom, which has no layout.
 */
describe("PlotModal sizing", () => {
  it("opens wide enough to be an expansion, not a second thumbnail", () => {
    render(<PlotModal url="/plot.png" title="STAR Alignment" onClose={() => {}} />);
    const panel = screen.getByRole("dialog");
    expect(panel.className).toContain("max-w-4xl");
    expect(panel.className).not.toContain("max-w-lg");
  });

  it("does not cap the image at thumbnail height", () => {
    render(<PlotModal url="/plot.png" title="STAR Alignment" onClose={() => {}} />);
    const img = screen.getByAltText("STAR Alignment");
    // max-h-64 is 16rem. A tall plot was being crushed to it regardless of the
    // space available.
    expect(img.className).not.toContain("max-h-64");
    expect(img.className).toMatch(/max-h-\[\d+vh\]/);
  });

  it("still names the dialog after the plot", () => {
    render(<PlotModal url="/plot.png" title="Per-Base Sequence Quality" onClose={() => {}} />);
    expect(screen.getByRole("dialog")).toHaveAccessibleName("Per-Base Sequence Quality");
  });
});
