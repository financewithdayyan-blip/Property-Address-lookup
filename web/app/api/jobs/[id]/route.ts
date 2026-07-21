import { NextRequest, NextResponse } from "next/server";
import { getDb } from "@/lib/db";

export const runtime = "nodejs";

export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  const db = getDb();

  const jobResult = await db.execute({
    sql: "SELECT id, created_at, status, total_rows, processed_rows FROM jobs WHERE id = ?",
    args: [id],
  });
  if (jobResult.rows.length === 0) {
    return NextResponse.json({ error: "Job not found." }, { status: 404 });
  }
  const job = jobResult.rows[0];

  const statusCounts = await db.execute({
    sql: `SELECT result_status, COUNT(*) AS count
          FROM job_rows
          WHERE job_id = ? AND processing_status = 'done'
          GROUP BY result_status`,
    args: [id],
  });

  const errors = await db.execute({
    sql: `SELECT owner_name_input, county, state, error_message
          FROM job_rows
          WHERE job_id = ? AND result_status = 'ERROR'
          ORDER BY row_index`,
    args: [id],
  });

  return NextResponse.json({
    job,
    statusCounts: Object.fromEntries(
      statusCounts.rows.map((r) => [r.result_status as string, Number(r.count)])
    ),
    errors: errors.rows,
  });
}
