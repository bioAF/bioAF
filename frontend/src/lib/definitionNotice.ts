/** The post-update infrastructure notice (mockup M17). */

export interface DefinitionNotice {
  pending: boolean;
  notice: { id: number; version: string; unknown_baseline: boolean; modules: string[] } | null;
  seen: boolean;
  destructive_only_modules: string[];
}

export const DEFINITION_NOTICE_PATH = "/api/v1/infrastructure/definition-notice";

export function displayVersion(version: string): string {
  return version.startsWith("v") ? version : `v${version}`;
}

export function isDefinitionNotice(value: unknown): value is DefinitionNotice {
  return Boolean(value && typeof (value as DefinitionNotice).pending === "boolean");
}
