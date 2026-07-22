import { randomUUID } from "crypto";
import { NextRequest, NextResponse } from "next/server";
import { getDb } from "@/lib/db";
import { ColumnMapping, InputCsvError, parseLeadsCsv } from "@/lib/parseInput";

export const runtime = "nodejs";

// History list for the "/" page - recent searches, optionally filtered by
// county/state and/or the calendar date they were created on. found_count
// is a single aggregate joined in here (rather than a per-row follow-up
// query) since that's what a user scanning past searches actually wants to
// see at a glance - how many addresses it actually turned up.
export async function GET(request: NextRequest) {
  const { searchParams } = new URL(request.url);
  const county = searchParams.get("county") ?? "";
  const state = searchParams.get("state") ?? "";
  const date = searchParams.get("date") ?? ""; // YYYY-MM-DD

  const db = getDb();
  const result = await db.execute({
    sql: `
      SELECT j.id, j.created_at, j.status, j.total_rows, j.processed_rows,
             j.county, j.state,
             COALESCE(SUM(CASE WHEN r.result_status = 'FOUND' THEN 1 ELSE 0 END), 0) AS found_count
      FROM jobs j
      LEFT JOIN job_rows r ON r.job_id = j.id
      WHERE (? = '' OR j.county = ?)
        AND (? = '' OR j.state = ?)
        AND (? = '' OR substr(j.created_at, 1, 10) = ?)
      GROUP BY j.id
      ORDER BY j.created_at DESC
      LIMIT 50
    `,
    args: [county, county, state, state, date, date],
  });

  return NextResponse.json({ jobs: result.rows });
}

export async function POST(request: NextRequest) {
  const formData = await request.formData();
  const file = formData.get("file");
  const mappingRaw = formData.get("mapping");

  if (!file || typeof file === "string") {
    return NextResponse.json({ error: "No file uploaded." }, { status: 400 });
  }
  if (!mappingRaw || typeof mappingRaw !== "string") {
    return NextResponse.json({ error: "No column mapping provided." }, { status: 400 });
  }

  let mapping: ColumnMapping;
  try {
    mapping = JSON.parse(mappingRaw);
  } catch {
    return NextResponse.json({ error: "Column mapping was not valid JSON." }, { status: 400 });
  }

  const csvText = await file.text();

  let rows;
  try {
    rows = parseLeadsCsv(csvText, mapping);
  } catch (exc) {
    if (exc instanceof InputCsvError) {
      return NextResponse.json({ error: exc.message }, { status: 400 });
    }
    throw exc;
  }

  const db = getDb();
  const jobId = randomUUID();

  await db.batch(
    [
      {
        sql: "INSERT INTO jobs (id, total_rows, county, state) VALUES (?, ?, ?, ?)",
        args: [jobId, rows.length, mapping.county, mapping.state],
      },
      ...rows.map((row, index) => ({
        sql: `INSERT INTO job_rows (job_id, row_index, owner_name_input, county, state, property_description)
              VALUES (?, ?, ?, ?, ?, ?)`,
        args: [jobId, index, row.owner_name, row.county, row.state, row.property_description],
      })),
    ],
    "write"
  );

  return NextResponse.json({ jobId }, { status: 201 });
}
