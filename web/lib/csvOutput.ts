// Column order must match OUTPUT_FIELDS in matching.py so a download from
// the web app looks identical in shape to a CLI run's output CSV.
export const OUTPUT_FIELDS = [
  "owner_name_input",
  "owner_name_found",
  "property_address",
  "mailing_address",
  "parcel_id",
  "county",
  "state",
  "property_description",
  "status",
  "match_score",
  "source_url",
] as const;

export const MULTI_MATCH_FIELDS = [
  "owner_name_input",
  "county",
  "state",
  "owner_name_found",
  "property_address",
  "mailing_address",
  "parcel_id",
  "match_score",
  "source_url",
] as const;

function csvEscape(value: string): string {
  if (value === null || value === undefined) return "";
  const s = String(value);
  if (s.includes(",") || s.includes('"') || s.includes("\n") || s.includes("\r")) {
    return `"${s.replace(/"/g, '""')}"`;
  }
  return s;
}

export function toCsv(fields: readonly string[], rows: Record<string, unknown>[]): string {
  const lines = [fields.join(",")];
  for (const row of rows) {
    lines.push(fields.map((f) => csvEscape(String(row[f] ?? ""))).join(","));
  }
  return lines.join("\r\n") + "\r\n";
}
