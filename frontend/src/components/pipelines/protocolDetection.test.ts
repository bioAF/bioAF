import { detectProtocol, pipelineAcceptsProtocol } from "./protocolDetection";
import type { ParameterSchema, SampleBrief } from "@/lib/types";

/**
 * The launch page auto-detected a 10x Chromium protocol from sample chemistry
 * and wrote it into the launch parameters for EVERY pipeline. Confirmed in the
 * browser on all 20 of the most popular ones: sarek's parameter box arrived
 * pre-filled with {"protocol": "10XV3"}.
 *
 * Worse than cosmetic on nf-core/nanoseq, which has a real `protocol` parameter
 * meaning something else entirely: "Input sample type. Valid options: 'DNA',
 * 'cDNA', and 'directRNA'." The click-through captured its field set to 10XV3.
 *
 * The gate is the pipeline's own schema, not its name: a detected value is only
 * offered when the pipeline declares that parameter AND accepts that value.
 */

const tenXSamples = [
  { id: 1, chemistry_version: "v3" },
  { id: 2, chemistry_version: "v3" },
] as SampleBrief[];

function schemaWithProtocol(values: string[] | undefined): ParameterSchema {
  return {
    $defs: {
      options: {
        title: "Options",
        properties: { protocol: { type: "string", ...(values ? { enum: values } : {}) } },
      },
    },
  };
}

describe("detectProtocol", () => {
  it("maps a shared 10x chemistry version to its protocol", () => {
    expect(detectProtocol(tenXSamples)).toBe("10XV3");
  });

  it("returns null when samples disagree, rather than guessing", () => {
    const mixed = [
      { id: 1, chemistry_version: "v2" },
      { id: 2, chemistry_version: "v3" },
    ] as SampleBrief[];
    expect(detectProtocol(mixed)).toBeNull();
  });

  it("returns null when no chemistry is recorded", () => {
    expect(detectProtocol([{ id: 1 }] as SampleBrief[])).toBeNull();
  });
});

describe("pipelineAcceptsProtocol", () => {
  it("accepts a 10x value when the pipeline declares it", () => {
    const schema = schemaWithProtocol(["10XV1", "10XV2", "10XV3"]);
    expect(pipelineAcceptsProtocol(schema, "10XV3")).toBe(true);
  });

  it("rejects a pipeline that declares no protocol parameter at all", () => {
    // sarek: whole-genome variant calling, no such parameter.
    const schema: ParameterSchema = { $defs: { opts: { title: "o", properties: { genome: { type: "string" } } } } };
    expect(pipelineAcceptsProtocol(schema, "10XV3")).toBe(false);
  });

  it("rejects a protocol parameter whose values are something else entirely", () => {
    // nanoseq: 'DNA' | 'cDNA' | 'directRNA'. Same name, different meaning.
    expect(pipelineAcceptsProtocol(schemaWithProtocol(["DNA", "cDNA", "directRNA"]), "10XV3")).toBe(false);
  });

  it("rejects when there is no schema to consult", () => {
    expect(pipelineAcceptsProtocol(null, "10XV3")).toBe(false);
  });

  it("rejects when nothing was detected", () => {
    expect(pipelineAcceptsProtocol(schemaWithProtocol(["10XV3"]), null)).toBe(false);
  });

  it("accepts an unconstrained protocol parameter only when it looks like a 10x one", () => {
    // No enum to check against. Guessing would reintroduce the bug on any
    // pipeline that happens to call a free-text parameter "protocol", so the
    // conservative answer is no.
    expect(pipelineAcceptsProtocol(schemaWithProtocol(undefined), "10XV3")).toBe(false);
  });

  it("finds the parameter under the legacy definitions spelling too", () => {
    const legacy = {
      definitions: { opts: { title: "o", properties: { protocol: { type: "string", enum: ["10XV3"] } } } },
    } as ParameterSchema;
    expect(pipelineAcceptsProtocol(legacy, "10XV3")).toBe(true);
  });
});
