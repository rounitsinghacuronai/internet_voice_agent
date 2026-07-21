/**
 * Repository provider — the single place the app resolves which data source to
 * use. Everything else imports `repositories` from here and never touches HTTP
 * or mock details directly.
 */
import { config } from "@/lib/config";
import type { Repositories } from "./repositories";
import { liveRepositories } from "./live/repositories";
import { mockRepositories } from "./mock/repositories";

export const repositories: Repositories =
  config.dataSource === "mock" ? mockRepositories : liveRepositories;

export type { Repositories } from "./repositories";
export * from "./types";
