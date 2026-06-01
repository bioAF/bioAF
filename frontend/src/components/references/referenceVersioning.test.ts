import { extractModeForUrl, predictNextVersion } from "./referenceVersioning";

describe("predictNextVersion", () => {
  it("returns 'v1' when no existing versions are passed", () => {
    expect(predictNextVersion([])).toBe("v1");
  });

  it("increments the highest integer-suffixed 'vN' version", () => {
    expect(predictNextVersion(["v1", "v2", "v3"])).toBe("v4");
  });

  it("handles bare numeric versions ('1', '2')", () => {
    expect(predictNextVersion(["1", "2"])).toBe("v3");
  });

  it("picks the max, not the count, so v2 + v45 -> v46", () => {
    expect(predictNextVersion(["v2", "v45", "v7"])).toBe("v46");
  });

  it("falls back to 'v1' when no version parses as an integer", () => {
    // "rev-A", "beta", etc. The user can still type their own value.
    expect(predictNextVersion(["rev-A", "beta", "GRCh38.p14"])).toBe("v1");
  });

  it("ignores non-integer versions when computing the max", () => {
    expect(predictNextVersion(["v3", "rev-A", "v5"])).toBe("v6");
  });
});

describe("extractModeForUrl", () => {
  it("returns 'tar.gz' for .tar.gz", () => {
    expect(extractModeForUrl("https://example.com/file.tar.gz")).toBe("tar.gz");
  });

  it("returns 'tar.gz' for .tgz", () => {
    expect(extractModeForUrl("https://example.com/file.tgz")).toBe("tar.gz");
  });

  it("returns 'tar' for .tar", () => {
    expect(extractModeForUrl("https://example.com/archive.tar")).toBe("tar");
  });

  it("returns 'gzip' for .gz (non-tar)", () => {
    expect(extractModeForUrl("https://example.com/gencode.gtf.gz")).toBe("gzip");
  });

  it("returns 'none' for unrecognized extensions", () => {
    expect(extractModeForUrl("https://example.com/refs.fa")).toBe("none");
    expect(extractModeForUrl("https://example.com/refs")).toBe("none");
  });

  it("is case-insensitive", () => {
    expect(extractModeForUrl("https://example.com/REFS.TAR.GZ")).toBe("tar.gz");
    expect(extractModeForUrl("https://example.com/REFS.GZ")).toBe("gzip");
  });

  it("ignores query strings and fragments when matching the extension", () => {
    expect(
      extractModeForUrl("https://example.com/file.tar.gz?token=abc&v=2#part"),
    ).toBe("tar.gz");
  });

  it("returns 'none' for an empty URL", () => {
    expect(extractModeForUrl("")).toBe("none");
  });
});
