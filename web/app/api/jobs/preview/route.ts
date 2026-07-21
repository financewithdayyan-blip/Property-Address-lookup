import { NextRequest, NextResponse } from "next/server";
import { InputCsvError, parseCsvHeaders } from "@/lib/parseInput";

export const runtime = "nodejs";

/** Returns a CSV's column headers (plus a sample first row) so the
 * upload page can show a field-mapping step before the file is
 * actually committed to a job.
 */
export async function POST(request: NextRequest) {
  const formData = await request.formData();
  const file = formData.get("file");

  if (!file || typeof file === "string") {
    return NextResponse.json({ error: "No file uploaded." }, { status: 400 });
  }

  const csvText = await file.text();

  try {
    const { headers, sampleRow } = parseCsvHeaders(csvText);
    return NextResponse.json({ headers, sampleRow });
  } catch (exc) {
    if (exc instanceof InputCsvError) {
      return NextResponse.json({ error: exc.message }, { status: 400 });
    }
    throw exc;
  }
}
