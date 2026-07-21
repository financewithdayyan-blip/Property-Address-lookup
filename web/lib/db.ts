import { createClient, type Client } from "@libsql/client";

let client: Client | null = null;

export function getDb(): Client {
  if (!client) {
    // Deliberately NOT process.env.TURSO_DATABASE_URL/TURSO_AUTH_TOKEN:
    // those are managed by Vercel's Turso marketplace integration, which
    // silently provisions a brand-new isolated database branch on every
    // single deployment (visible as a "dpl-<id>-..." hostname) and resets
    // the env vars to point at it on every build - so an external worker
    // pinned to one stable connection string would never see data written
    // by whatever the *current* deployment happens to be. These
    // APP_-prefixed vars are plain, manually-set values (not integration-
    // managed) pointing at one fixed database that Vercel, local dev, and
    // the worker all share.
    const url = process.env.APP_TURSO_DATABASE_URL;
    const authToken = process.env.APP_TURSO_AUTH_TOKEN;
    if (!url || !authToken) {
      throw new Error("APP_TURSO_DATABASE_URL / APP_TURSO_AUTH_TOKEN environment variables are not set.");
    }
    client = createClient({ url, authToken });
  }
  return client;
}

export interface JobRecord {
  id: string;
  created_at: string;
  status: "pending" | "running" | "done";
  total_rows: number;
  processed_rows: number;
}
