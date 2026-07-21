/**
 * Repository provider. Single real data source — every page/hook imports
 * `repositories` from here and talks only to the live FastAPI backend. There is
 * no mock/sample data in the app.
 */
import type { Repositories } from "./repositories";
import { liveRepositories } from "./live/repositories";

export const repositories: Repositories = liveRepositories;

export type { Repositories } from "./repositories";
export * from "./types";
