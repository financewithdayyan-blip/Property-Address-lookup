import { parse } from "csv-parse/sync";

export interface ParsedLeadRow {
  owner_name: string;
  county: string;
  state: string;
}

export class InputCsvError extends Error {}

/**
 * Mirrors load_input_rows() in main.py: normalize headers
 * (trim/lowercase/underscore), require owner_name/county/state, trim
 * every value, and skip rows that end up fully blank.
 */
export function parseLeadsCsv(csvText: string): ParsedLeadRow[] {
  let records: Record<string, string>[];
  try {
    records = parse(csvText, {
      columns: (header: string[]) =>
        header.map((h) => h.trim().toLowerCase().replace(/ /g, "_")),
      skip_empty_lines: true,
      trim: true,
    });
  } catch (exc) {
    throw new InputCsvError(`Could not parse CSV: ${(exc as Error).message}`);
  }

  if (records.length === 0) {
    throw new InputCsvError("CSV has no data rows.");
  }

  const columns = new Set(Object.keys(records[0]));
  const required = ["owner_name", "county", "state"];
  const missing = required.filter((c) => !columns.has(c));
  if (missing.length > 0) {
    throw new InputCsvError(
      `Input CSV is missing required column(s): ${missing.join(", ")}. ` +
        `Found columns: ${Array.from(columns).join(", ")}`
    );
  }

  const rows: ParsedLeadRow[] = [];
  for (const record of records) {
    const owner_name = (record.owner_name ?? "").trim();
    const county = (record.county ?? "").trim();
    const state = (record.state ?? "").trim();
    if (!owner_name && !county && !state) continue; // skip fully-blank rows
    rows.push({ owner_name, county, state });
  }

  if (rows.length === 0) {
    throw new InputCsvError("CSV has no non-empty data rows.");
  }

  return rows;
}
