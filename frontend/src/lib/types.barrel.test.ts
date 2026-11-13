/**
 * `@/lib/types` is a compatibility barrel over domain modules in `lib/types/`.
 *
 * It must keep every name existing callers import, keep the two runtime values unchanged,
 * declare nothing itself, and stay the only barrel: a `lib/types/index.ts` would compete
 * with it for the bare `@/lib/types` path. Domain modules import each other directly,
 * never through the barrel, so module initialization cannot cycle through it.
 */
import fs from "fs";
import path from "path";
import ts from "typescript";
import { RESOURCE_PROFILES, SAMPLE_ASSAY_OPTIONS } from "@/lib/types";

const LIB = path.join(__dirname);
const BARREL = path.join(LIB, "types.ts");
const DOMAIN_DIR = path.join(LIB, "types");

// Every name `lib/types.ts` exported when plan 20 started, after batch A's two renames
// (ExperimentSummary -> ProjectExperimentSummary / ExperimentReference) and the removal of the
// unused ProvenanceChain.
const EXPORTED_NAMES = `
  AnalysisSnapshot AnalysisSnapshotDetail AssayValue AssistantAvailability AssistantConfirmResponse
  AssistantConversationListResponse AssistantConversationResponse AssistantConversationSummary
  AssistantConversationTranscript AssistantMessageResponse AssistantPlanStep AssistantTranscriptMessage
  AssistantTranscriptPlan AssistantTurnStatus AuditLogEntry AuditLogResponse AutoRunConfig AutoRunConfigCreate
  AutoRunConfigUpdate BindingSource BootstrapStatus BucketStats BudgetStatus BuildLogsResponse CellCountPoint
  CellxgeneFileInspection CellxgenePublicationResponse CellxgenePublishableFile ClusteringDiff CommandDiff
  ComponentState ComputeSessionRef ControlledVocabularyFieldsResponse ControlledVocabularyResponse
  ControlledVocabularyValue CostEstimate CustomFieldResponse CustomFieldValue CustomPipeline
  CustomPipelineCodeSource CustomPipelineCreateRequest CustomPipelineDetail CustomPipelineEnvironmentSummary
  CustomPipelineRunOverview CustomPipelineUpdateRequest CustomPipelineVariable CustomPipelineVariableDefinition
  CustomPipelineVariableType CustomPipelineVersion CustomPipelineVersionCreateRequest
  CustomPipelineVersionStatus CustomPipelineVersionTrigger DataMount DatasetExperimentSummary
  DatasetSearchResult DeclaredColumn DependencyNode DependencyTree DocumentResponse DocumentSearchResponse
  EmbeddingDiff EnvironmentCreateRequest EnvironmentDetailResponse EnvironmentListResponse EnvironmentResponse
  EnvironmentVersionResponse EnvironmentVersionSummary Experiment ExperimentCreateRequest ExperimentDetail
  ExperimentListResponse ExperimentReference ExperimentStatus ExperimentTemplate ExperimentUpdateRequest
  FieldDefaultResponse FieldDefaultValue FileListResponse FileProvenance FileResponse
  FileUploadInitiateResponse GeoSampleValidation GeoValidationField GeoValidationReport GeoValidationSummary
  GitCommit GitCommitDetail GitCommitListResponse GitHubRepo GitHubRepoListResponse GitOpsRepoStatus
  HealthStatus ImpactPipelineRun ImpactSummary IngestEvent InstalledPackage LifecyclePolicyStatus LoginResponse
  MachineType ManifestEntry NamingProfile NamingProfileDelimiter NamingProfileTestResult NavItem
  NotebookSession PackageSearchResponse PackageSearchResult ParameterDiff ParameterGroup ParameterSchema
  PendingAutoRun PerSampleInputSpec PermissionEntry PipelineAddRequest PipelineCatalog
  PipelineCatalogListResponse PipelineProcess PipelineProcessRetry PipelineProcessStatus PipelineProgress
  PipelineRun PipelineRunCompareResponse PipelineRunDetail PipelineRunLaunchRequest PipelineRunListResponse
  PipelineRunPreflight PipelineRunRef PipelineRunReview PipelineRunReviewCreate PipelineRunReviewListResponse
  PipelineRunStatus PipelineRunSummary PipelineTrigger PlotArchiveListResponse PlotArchiveResponse Project
  ProjectDetailResponse ProjectExperimentSummary ProjectListResponse ProjectSampleGroup ProjectSampleResponse
  ProjectSummary ProvenanceDAG ProvenanceEdge ProvenanceNode QCChartSpec QCDashboardConfig QCDashboardResponse
  QCDashboardSummary QCMetricSpec QCMetrics QCPlot QCPlotSpec QCSection QCStatus RESOURCE_PROFILES
  ReaderSACreateResponse ReaderSAStatus ReferenceDataset ReferenceDatasetDetail ReferenceDatasetFile
  ReferenceDatasetListResponse ReferenceImportRequest ReferenceImportStartResponse
  ReferenceImportStatusResponse ReferenceParameterCategory ReferenceUploadFileSpec ReferenceUploadInitRequest
  ReferenceUploadInitResponse ReferenceUploadSlot RegistryListResponse RegistryPipeline RegistryRefreshResponse
  RegistryVersion RegistryVersionsResponse ResourceProfile ReviewVerdict Role RoleListResponse
  RunSamplesheetDesign SAMPLE_ASSAY_OPTIONS Sample SampleBatch SampleBatchBrief SampleBatchCreateRequest
  SampleBatchSummary SampleBrief SampleBulkUpdateRequest SampleCreateRequest SampleCustomFieldResponse
  SampleCustomFieldValue SampleStatus SampleUpdateRequest SampleVerdict SampleVerdictEntry SamplesheetInputSpec
  SamplesheetPrefill SamplesheetPreview SearchHit SearchResult SegmentDateFormat SegmentDefinition
  SegmentFieldType SequencingBatch SequencingBatchDetail SequencingBatchStatus SessionLaunchRequest
  SessionListResponse SessionProvenance SessionStatus SessionType SheetPreviewResponse SheetRecognizedColumn
  SnapshotComparison StatedValue StorageDashboard TemplateCreateRequest TemplateNotebookListResponse
  TemplateNotebookResponse TerraformRun TriggerEvaluation UnclaimedEntity User UserSummary VersionCreateRequest
  VocabularyResponse VocabularyValue WorkNode WorkNodeLaunchRequest WorkNodeListResponse
`.split(/\s+/).filter(Boolean);

function barrelExports(): string[] {
  const program = ts.createProgram([BARREL], {
    module: ts.ModuleKind.ESNext,
    moduleResolution: ts.ModuleResolutionKind.Bundler,
    target: ts.ScriptTarget.ES2015,
    strict: true,
    noEmit: true,
    baseUrl: path.join(LIB, "..", ".."),
    paths: { "@/*": ["./src/*"] },
  });
  const checker = program.getTypeChecker();
  const source = program.getSourceFile(BARREL)!;
  const moduleSymbol = checker.getSymbolAtLocation(source)!;
  return checker.getExportsOfModule(moduleSymbol).map((s) => s.getName());
}

test("the barrel still exports every existing name, and nothing else", () => {
  expect(barrelExports().sort()).toEqual([...EXPORTED_NAMES].sort());
});

test("the barrel only re-exports; declarations live in domain modules", () => {
  const text = fs.readFileSync(BARREL, "utf8");
  expect(text).not.toMatch(/^export (interface|type \w+ =|const|function|class|enum)\b/m);
  expect(fs.existsSync(DOMAIN_DIR)).toBe(true);
});

test("there is no second barrel competing for @/lib/types", () => {
  expect(fs.existsSync(path.join(DOMAIN_DIR, "index.ts"))).toBe(false);
  expect(fs.existsSync(path.join(DOMAIN_DIR, "index.tsx"))).toBe(false);
});

test("domain modules never import through the barrel", () => {
  const files = fs.existsSync(DOMAIN_DIR) ? fs.readdirSync(DOMAIN_DIR).filter((f) => f.endsWith(".ts")) : [];
  for (const file of files) {
    const text = fs.readFileSync(path.join(DOMAIN_DIR, file), "utf8");
    expect([file, text.match(/from\s+["'](@\/lib\/types|\.\.\/types|\.\.?)["']/)]).toEqual([file, null]);
  }
});

test("the runtime values keep their contents", () => {
  expect(SAMPLE_ASSAY_OPTIONS).toEqual([
    { value: "bulk_rna", label: "Bulk RNA-seq" },
    { value: "scrna", label: "Single-cell RNA-seq" },
    { value: "other", label: "Other" },
  ]);
  expect(RESOURCE_PROFILES).toEqual({
    small: { cpu: 2, memory: 8 },
    medium: { cpu: 4, memory: 16 },
    large: { cpu: 8, memory: 32 },
    xlarge: { cpu: 16, memory: 64 },
    "2xlarge": { cpu: 16, memory: 128 },
  });
});
