import { NextRequest, NextResponse } from "next/server";
import { getDb } from "@/lib/db";

export const runtime = "nodejs";

export async function POST(
  _request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  const db = getDb();

  const jobResult = await db.execute({
    sql: "SELECT status FROM jobs WHERE id = ?",
    args: [id],
  });
  if (jobResult.rows.length === 0) {
    return NextResponse.json({ error: "Job not found." }, { status: 404 });
  }
  const status = jobResult.rows[0].status as string;
  if (status === "done" || status === "cancelled") {
    return NextResponse.json({ error: `Job is already ${status}.` }, { status: 400 });
  }

  // Only rows still 'pending' can be closed out here - a row the worker has
  // already 'claimed' is mid-request and finishes on its own shortly
  // regardless (see the guard in db.write_row_result()). Bulk-closing the
  // pending ones is enough to stop the worker from ever picking them up:
  // claim_next_pending_row() only looks at processing_status = 'pending'.
  const pendingResult = await db.execute({
    sql: "SELECT COUNT(*) AS count FROM job_rows WHERE job_id = ? AND processing_status = 'pending'",
    args: [id],
  });
  const cancelledRows = Number(pendingResult.rows[0].count);

  await db.batch(
    [
      {
        sql: `UPDATE job_rows
              SET processing_status = 'done', result_status = 'CANCELLED',
                  processed_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
              WHERE job_id = ? AND processing_status = 'pending'`,
        args: [id],
      },
      {
        sql: `UPDATE jobs
              SET status = 'cancelled', processed_rows = processed_rows + ?
              WHERE id = ?`,
        args: [cancelledRows, id],
      },
    ],
    "write"
  );

  return NextResponse.json({ ok: true, cancelledRows });
}
