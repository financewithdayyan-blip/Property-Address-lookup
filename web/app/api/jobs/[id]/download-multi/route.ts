import { NextRequest, NextResponse } from "next/server";
import { getDb } from "@/lib/db";
import { MULTI_MATCH_FIELDS, toCsv } from "@/lib/csvOutput";

export const runtime = "nodejs";

export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  const db = getDb();

  const result = await db.execute({
    sql: `SELECT jr.owner_name_input, jr.county, jr.state,
                 c.owner_name_found, c.property_address, c.mailing_address,
                 c.parcel_id, c.match_score, c.source_url
          FROM job_row_candidates c
          JOIN job_rows jr ON jr.id = c.job_row_id
          WHERE jr.job_id = ?
          ORDER BY jr.row_index, c.id`,
    args: [id],
  });

  const csv = toCsv(MULTI_MATCH_FIELDS, result.rows as unknown as Record<string, unknown>[]);
  return new NextResponse(csv, {
    headers: {
      "Content-Type": "text/csv; charset=utf-8",
      "Content-Disposition": `attachment; filename="results_${id}_multiple_matches.csv"`,
    },
  });
}
