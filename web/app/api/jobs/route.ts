import { randomUUID } from "crypto";
import { NextRequest, NextResponse } from "next/server";
import { getDb } from "@/lib/db";
import { InputCsvError, parseLeadsCsv } from "@/lib/parseInput";

export const runtime = "nodejs";

export async function POST(request: NextRequest) {
  const formData = await request.formData();
  const file = formData.get("file");

  if (!file || typeof file === "string") {
    return NextResponse.json({ error: "No file uploaded." }, { status: 400 });
  }

  const csvText = await file.text();

  let rows;
  try {
    rows = parseLeadsCsv(csvText);
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
        sql: `INSERT INTO job_rows (job_id, row_index, owner_name_input, county, state)
              VALUES (?, ?, ?, ?, ?)`,
        args: [jobId, index, row.owner_name, row.county, row.state],
      })),
    ],
    "write"
  );

  return NextResponse.json({ jobId }, { status: 201 });
}
