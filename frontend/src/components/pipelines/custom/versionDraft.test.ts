import { readQcConfig, versionRequest, type VersionDraft } from "./versionDraft";

const draft: VersionDraft = {
  code_source_type: "github_repo",
  github_repo_id: 3,
  code_content: "left over",
  entrypoint_command: "bash run.sh",
  environment_version_id: 5,
  cpu_request: "",
  memory_request: "",
  log_file_path: "",
  variables: [
    { _key: "a", variable_name: "ref", default_value: "", variable_type: "reference", reference_category: "genome", is_required: true },
    { _key: "b", variable_name: "n", default_value: "", variable_type: "number", reference_category: "genome", is_required: false },
  ],
  qc_template: "  ",
  qc_config_json_text: "null",
};

test("QC config: blank and null are no config; arrays and scalars are refused; bad JSON says why", () => {
  expect(readQcConfig("  ")).toEqual({ ok: true, value: null });
  expect(readQcConfig("null")).toEqual({ ok: true, value: null });
  expect(readQcConfig("[1]")).toEqual({ ok: false, reason: "not_object" });
  expect(readQcConfig("3")).toEqual({ ok: false, reason: "not_object" });
  expect(readQcConfig("{")).toMatchObject({ ok: false, reason: "invalid" });
  expect(readQcConfig('{"a":1}')).toEqual({ ok: true, value: { a: 1 } });
});

test("a repo version sends no code, default resources, and a category only for references", () => {
  const request = versionRequest(draft);
  expect(request).toMatchObject({
    ok: true,
    body: {
      code_content: null,
      github_repo_id: 3,
      cpu_request: "1",
      memory_request: "4Gi",
      log_file_path: null,
      qc_template: null,
      qc_config_json: null,
    },
  });
  if (request.ok) {
    expect(request.body.variables.map((v) => v.reference_category)).toEqual(["genome", null]);
    expect(request.body.variables.map((v) => v.default_value)).toEqual([null, null]);
  }
});

test("the first unmet requirement is the one reported", () => {
  expect(versionRequest({ ...draft, entrypoint_command: " " })).toEqual({ ok: false, error: "Entrypoint command is required." });
  expect(versionRequest({ ...draft, environment_version_id: null })).toEqual({ ok: false, error: "Select a template version." });
  expect(versionRequest({ ...draft, github_repo_id: null })).toEqual({ ok: false, error: "Select a GitHub repo." });
  expect(versionRequest({ ...draft, code_source_type: "inline", code_content: " " })).toEqual({
    ok: false,
    error: "Provide code content for the chosen source type.",
  });
});
