/**
 * Typed client wrappers for the LIMS integration admin endpoints. The UI
 * uses these on the Settings > Users and Accounts page. The public
 * /api/v1/integrations/* surface is for external systems only and the UI
 * never talks to it directly.
 */

import { api } from "./api";

export interface ServiceAccount {
  id: number;
  display_name: string | null;
  email: string;
  role_id: number;
  status: string;
  created_at: string;
  last_login: string | null;
}

export interface ApiKey {
  id: number;
  name: string;
  key_prefix: string;
  scopes: string[];
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
  service_account_user_id: number;
}

export interface ApiKeyMintResponse {
  api_key: ApiKey;
  secret: string;
}

export interface WebhookSubscription {
  id: number;
  name: string;
  url: string;
  events: string[];
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface WebhookSubscriptionCreateResponse {
  subscription: WebhookSubscription;
  secret: string;
}

export interface WebhookDelivery {
  id: number;
  subscription_id: number;
  event_id: string;
  event_type: string;
  status: string;
  attempt_count: number;
  next_attempt_at: string | null;
  last_response_status: number | null;
  last_attempted_at: string | null;
  created_at: string;
  delivered_at: string | null;
}

export interface ApiActivityRow {
  id: number;
  timestamp: string;
  user_id: number | null;
  api_key_id: number | null;
  entity_type: string;
  entity_id: number;
  action: string;
  details_json: Record<string, unknown> | null;
}

export const integrationsApi = {
  listServiceAccounts: () => api.get<ServiceAccount[]>("/api/admin/service-accounts"),
  createServiceAccount: (display_name: string, role_id: number) =>
    api.post<ServiceAccount>("/api/admin/service-accounts", { display_name, role_id }),
  updateServiceAccount: (id: number, body: { display_name?: string; role_id?: number }) =>
    api.patch<ServiceAccount>(`/api/admin/service-accounts/${id}`, body),
  disableServiceAccount: (id: number) =>
    api.post<ServiceAccount>(`/api/admin/service-accounts/${id}/disable`),

  listApiKeys: (saId: number) =>
    api.get<ApiKey[]>(`/api/admin/service-accounts/${saId}/api-keys`),
  mintApiKey: (saId: number, name: string, scopes: string[]) =>
    api.post<ApiKeyMintResponse>(`/api/admin/service-accounts/${saId}/api-keys`, {
      name,
      scopes,
    }),
  revokeApiKey: (keyId: number) => api.post<ApiKey>(`/api/admin/api-keys/${keyId}/revoke`),
  listScopeAlphabet: () => api.get<{ scopes: string[] }>("/api/admin/api-keys/scope-alphabet"),

  listWebhooks: () => api.get<WebhookSubscription[]>("/api/admin/webhooks"),
  createWebhook: (body: { name: string; url: string; events: string[] }) =>
    api.post<WebhookSubscriptionCreateResponse>("/api/admin/webhooks", body),
  updateWebhook: (
    id: number,
    body: { name?: string; url?: string; events?: string[]; is_active?: boolean },
  ) => api.patch<WebhookSubscription>(`/api/admin/webhooks/${id}`, body),
  disableWebhook: (id: number) => api.delete<WebhookSubscription>(`/api/admin/webhooks/${id}`),
  rotateWebhookSecret: (id: number) =>
    api.post<WebhookSubscriptionCreateResponse>(`/api/admin/webhooks/${id}/rotate-secret`),
  listWebhookDeliveries: (id: number, params: { status?: string; limit?: number } = {}) => {
    const search = new URLSearchParams();
    if (params.status) search.set("status", params.status);
    if (params.limit) search.set("limit", String(params.limit));
    const qs = search.toString();
    return api.get<WebhookDelivery[]>(
      `/api/admin/webhooks/${id}/deliveries${qs ? `?${qs}` : ""}`,
    );
  },
  replayWebhookDelivery: (deliveryId: number) =>
    api.post<WebhookDelivery>(`/api/admin/webhooks/deliveries/${deliveryId}/replay`),
  fireTestWebhook: (id: number) => api.post<WebhookDelivery>(`/api/admin/webhooks/${id}/test`),

  listApiActivity: (params: { limit?: number; cursor?: number } = {}) => {
    const search = new URLSearchParams();
    if (params.limit) search.set("limit", String(params.limit));
    if (params.cursor) search.set("cursor", String(params.cursor));
    const qs = search.toString();
    return api.get<ApiActivityRow[]>(
      `/api/admin/audit-log/api-activity${qs ? `?${qs}` : ""}`,
    );
  },
};
