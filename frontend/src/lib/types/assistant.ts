// The pipeline-run assistant.
// Re-exported by the compatibility barrel `@/lib/types`; import siblings directly, never the barrel.

export interface AssistantAvailability {
  enabled: boolean;
  reason?: string | null;
}

export interface AssistantConversationResponse {
  id: number;
  status: string;
  provider: string | null;
  model: string | null;
}

export interface AssistantPlanStep {
  tool: string;
  args: Record<string, unknown>;
  // read_only | mutating | spend - drives the cost warning on the confirm card.
  consequence_class?: string;
  // Set on a confirmed plan's steps: the output of a step that ran, or why it did not.
  result?: Record<string, unknown>;
  outcome?: "failed" | "not_run";
}

export type AssistantTurnStatus =
  | "answered"
  | "awaiting_confirmation"
  | "step_cap_exceeded"
  | "unavailable";

export interface AssistantMessageResponse {
  status: AssistantTurnStatus;
  text: string | null;
  action_plan_id: number | null;
  plan_steps: AssistantPlanStep[] | null;
  reason: string | null;
}

export interface AssistantConfirmResponse {
  status: string;
  plan_id: number;
  executed: boolean;
  result: Record<string, unknown> | null;
  // Set when a confirmed launch actually started a run (org launch toggle on); else null.
  run_id?: number | null;
}

export interface AssistantConversationSummary {
  id: number;
  title: string | null;
  preview: string | null;
  status: string;
  message_count: number;
  created_at: string;
  updated_at: string;
}

export interface AssistantConversationListResponse {
  conversations: AssistantConversationSummary[];
  total: number;
}

export interface AssistantTranscriptMessage {
  id: number;
  role: string;
  content: string | null;
  tool_calls: AssistantPlanStep[] | null;
  created_at: string;
}

export interface AssistantTranscriptPlan {
  id: number;
  steps: AssistantPlanStep[] | null;
  status: string;
  created_at: string;
}

export interface AssistantConversationTranscript {
  id: number;
  title: string | null;
  messages: AssistantTranscriptMessage[];
  plans: AssistantTranscriptPlan[];
}
