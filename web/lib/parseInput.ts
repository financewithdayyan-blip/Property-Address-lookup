import { parse } from "csv-parse/sync";

export interface ParsedLeadRow {
  owner_name: string;
  county: string;
  state: string;
  property_description: string;
}

export interface ColumnMapping {
  owner_name: string; // CSV column name
  county: string; // fixed value applied to every row (chosen from a dropdown, not a column name)
  state: string; // fixed value applied to every row (chosen from a dropdown, not a column name)
  property_description?: string | null; // CSV column name, or null/omitted
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

/** Parses full CSV rows using a user-chosen owner-name column mapping plus
 * a fixed target county/state (the whole batch is searched against one
 * county, chosen from a dropdown - see SUPPORTED_COUNTIES - rather than
 * requiring the CSV to have its own county/state columns).
 * property_description is an optional column mapping and defaults to "".
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
  if (!mapping.owner_name || !columns.has(mapping.owner_name)) {
    throw new InputCsvError(`Column mapping for "owner_name" is missing or refers to a column not in the CSV.`);
  }
  if (!mapping.county || !mapping.state) {
    throw new InputCsvError("A target county must be selected.");
  }
  if (mapping.property_description && !columns.has(mapping.property_description)) {
    throw new InputCsvError(`Column mapping for "property_description" refers to a column not in the CSV.`);
  }

  const rows: ParsedLeadRow[] = [];
  for (const record of records) {
    const owner_name = (record[mapping.owner_name] ?? "").trim();
    const property_description = mapping.property_description
      ? (record[mapping.property_description] ?? "").trim()
      : "";
    if (!owner_name) continue; // skip fully-blank rows
    rows.push({ owner_name, county: mapping.county, state: mapping.state, property_description });
  }

  if (rows.length === 0) {
    throw new InputCsvError("CSV has no non-empty data rows.");
  }

  return rows;
}
