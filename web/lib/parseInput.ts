import { parse } from "csv-parse/sync";

export interface ParsedLeadRow {
  owner_name: string;
  county: string;
  state: string;
  property_description: string;
}

export interface ColumnMapping {
  owner_name: string;
  county: string;
  state: string;
  property_description?: string | null;
}

export class InputCsvError extends Error {}

/** Parses just the header row (and a sample first row for preview), so
 * the upload UI can show the user their CSV's actual column names to map
 * before committing to a job. Headers are kept exactly as they appear in
 * the file - no normalization - since the mapping step references them
 * verbatim.
 */
export function parseCsvHeaders(csvText: string): { headers: string[]; sampleRow: Record<string, string> | null } {
  let records: Record<string, string>[];
  try {
    records = parse(csvText, { columns: true, skip_empty_lines: true, trim: true });
  } catch (exc) {
    throw new InputCsvError(`Could not parse CSV: ${(exc as Error).message}`);
  }
  if (records.length === 0) {
    throw new InputCsvError("CSV has no data rows.");
  }
  return { headers: Object.keys(records[0]), sampleRow: records[0] };
}

/** Parses full CSV rows using a user-chosen column mapping (rather than
 * assuming fixed header names) - owner_name/county/state are required
 * mappings, property_description is optional and defaults to "".
 */
export function parseLeadsCsv(csvText: string, mapping: ColumnMapping): ParsedLeadRow[] {
  let records: Record<string, string>[];
  try {
    records = parse(csvText, { columns: true, skip_empty_lines: true, trim: true });
  } catch (exc) {
    throw new InputCsvError(`Could not parse CSV: ${(exc as Error).message}`);
  }

  if (records.length === 0) {
    throw new InputCsvError("CSV has no data rows.");
  }

  const columns = new Set(Object.keys(records[0]));
  for (const field of ["owner_name", "county", "state"] as const) {
    const source = mapping[field];
    if (!source || !columns.has(source)) {
      throw new InputCsvError(`Column mapping for "${field}" is missing or refers to a column not in the CSV.`);
    }
  }
  if (mapping.property_description && !columns.has(mapping.property_description)) {
    throw new InputCsvError(`Column mapping for "property_description" refers to a column not in the CSV.`);
  }

  const rows: ParsedLeadRow[] = [];
  for (const record of records) {
    const owner_name = (record[mapping.owner_name] ?? "").trim();
    const county = (record[mapping.county] ?? "").trim();
    const state = (record[mapping.state] ?? "").trim();
    const property_description = mapping.property_description
      ? (record[mapping.property_description] ?? "").trim()
      : "";
    if (!owner_name && !county && !state) continue; // skip fully-blank rows
    rows.push({ owner_name, county, state, property_description });
  }

  if (rows.length === 0) {
    throw new InputCsvError("CSV has no non-empty data rows.");
  }

  return rows;
}
