import { randomUUID } from "crypto";
import { NextRequest, NextResponse } from "next/server";
import { getDb } from "@/lib/db";
import { ColumnMapping, InputCsvError, parseLeadsCsv } from "@/lib/parseInput";

export const runtime = "nodejs";

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
        sql: "INSERT INTO jobs (id, total_rows) VALUES (?, ?)",
        args: [jobId, rows.length],
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
