import { storageDisplay } from "./storageDisplay";

describe("storageDisplay", () => {
  it("returns GCS display for the gcs backend (GCP, behavior-preserving)", () => {
    expect(storageDisplay("gcs")).toEqual({
      label: "GCS",
      uriScheme: "gs://",
      cliCopy: "gsutil cp",
    });
  });

  it("returns S3 display for the s3 backend (AWS)", () => {
    expect(storageDisplay("s3")).toEqual({
      label: "S3",
      uriScheme: "s3://",
      cliCopy: "aws s3 cp",
    });
  });

  it("falls back to GCS for unknown/missing backends so GCP stays unchanged", () => {
    expect(storageDisplay("nfs").label).toBe("GCS");
    expect(storageDisplay(undefined).uriScheme).toBe("gs://");
  });
});
