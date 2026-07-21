import { createClient, type Client } from "@libsql/client";

let client: Client | null = null;

export function getDb(): Client {
  if (!client) {
    const url = process.env.TURSO_DATABASE_URL;
    const authToken = process.env.TURSO_AUTH_TOKEN;
    if (!url || !authToken) {
      throw new Error("TURSO_DATABASE_URL / TURSO_AUTH_TOKEN environment variables are not set.");
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
