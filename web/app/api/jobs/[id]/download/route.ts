import { NextRequest, NextResponse } from "next/server";
import { getDb } from "@/lib/db";
import { OUTPUT_FIELDS, toCsv } from "@/lib/csvOutput";

export const runtime = "nodejs";

export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  const db = getDb();

  const result = await db.execute({
    sql: `SELECT owner_name_input, owner_name_found, property_address,
                 mailing_address, parcel_id, county, state, property_description,
                 result_status AS status, match_score, source_url
          FROM job_rows
          WHERE job_id = ?
          ORDER BY row_index`,
    args: [id],
  });

  if (result.rows.length === 0) {
    return NextResponse.json({ error: "Job not found or has no rows." }, { status: 404 });
  }

  const csv = toCsv(OUTPUT_FIELDS, result.rows as unknown as Record<string, unknown>[]);
  return new NextResponse(csv, {
    headers: {
      "Content-Type": "text/csv; charset=utf-8",
      "Content-Disposition": `attachment; filename="results_${id}.csv"`,
    },
  });
}
