"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { SUPPORTED_COUNTIES } from "@/lib/counties";

type Step = "select" | "map";

interface Mapping {
  owner_name: string;
  property_description: string; // "" means not mapped (optional field)
}

const OWNER_NAME_ALIASES = ["ownername", "owner", "name", "fullname"];
const DESCRIPTION_ALIASES = ["propertydescription", "description", "desc", "notes", "propertynotes"];

// Splits into word tokens rather than one joined blob, so e.g. "Owner
// Name" matches via its token but a column like "Notesheet" doesn't
// false-positive-match "notes" the way naive substring matching would.
function tokenize(h: string): string[] {
  return h.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
}

function guessMapping(headers: string[]): Mapping {
  const parsed = headers.map((h) => {
    const tokens = tokenize(h);
    return { raw: h, tokens, joined: tokens.join("") };
  });
  const findMatch = (aliases: string[]) => {
    const exact = parsed.find((p) => aliases.includes(p.joined));
    if (exact) return exact.raw;
    const byToken = parsed.find((p) => p.tokens.some((t) => aliases.includes(t)));
    return byToken?.raw ?? "";
  };
  return {
    owner_name: findMatch(OWNER_NAME_ALIASES),
    property_description: findMatch(DESCRIPTION_ALIASES),
  };
}

function countyKey(county: string, state: string): string {
  return `${county}|${state}`;
}

export default function UploadPage() {
  const router = useRouter();
  const [step, setStep] = useState<Step>("select");
  const [file, setFile] = useState<File | null>(null);
  const [headers, setHeaders] = useState<string[]>([]);
  const [sampleRow, setSampleRow] = useState<Record<string, string> | null>(null);
  const [mapping, setMapping] = useState<Mapping>({ owner_name: "", property_description: "" });
  const [targetCounty, setTargetCounty] = useState("");
  const [loadingPreview, setLoadingPreview] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const chosen = e.target.files?.[0] ?? null;
    if (!chosen) return;
    setError(null);
    setFile(chosen);
    setLoadingPreview(true);

    const formData = new FormData();
    formData.append("file", chosen);

    try {
      const res = await fetch("/api/jobs/preview", { method: "POST", body: formData });
      const data = await res.json();
      if (!res.ok) {
        setError(data.error ?? "Could not read that CSV.");
        setLoadingPreview(false);
        return;
      }
      setHeaders(data.headers);
      setSampleRow(data.sampleRow ?? null);
      setMapping(guessMapping(data.headers));
      setStep("map");
    } catch {
      setError("Could not read that file - check it's a valid CSV and try again.");
    } finally {
      setLoadingPreview(false);
    }
  }

  function handleBack() {
    setStep("select");
    setFile(null);
    setHeaders([]);
    setSampleRow(null);
    setError(null);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return;
    const target = SUPPORTED_COUNTIES.find((c) => countyKey(c.county, c.state) === targetCounty);
    if (!target) return;

    setSubmitting(true);
    setError(null);

    const formData = new FormData();
    formData.append("file", file);
    formData.append(
      "mapping",
      JSON.stringify({
        owner_name: mapping.owner_name,
        county: target.county,
        state: target.state,
        property_description: mapping.property_description || null,
      })
    );

    try {
      const res = await fetch("/api/jobs", { method: "POST", body: formData });
      const data = await res.json();
      if (!res.ok) {
        setError(data.error ?? "Upload failed.");
        setSubmitting(false);
        return;
      }
      router.push(`/jobs/${data.jobId}`);
    } catch {
      setError("Upload failed - check your connection and try again.");
      setSubmitting(false);
    }
  }

  const canSubmit = mapping.owner_name && targetCounty;

  return (
    <main className="narrow">
      <div className="app-header">
        <div className="mark">PA</div>
        <div>
          <h1>Property Address Lookup</h1>
        </div>
      </div>
      <p className="subtitle">
        Upload a leads CSV. Currently supported: Pinellas, Hillsborough, Lee,
        Palm Beach, and Duval counties, FL.
      </p>
      <div className="card">
        {error && <div className="error">{error}</div>}

        {step === "select" && (
          <>
            <label className="file-input" htmlFor="csv-file">
              {file ? file.name : "Click to choose a CSV file, or drag one here"}
              <input
                id="csv-file"
                type="file"
                accept=".csv"
                onChange={handleFileChange}
                disabled={loadingPreview}
                style={{ display: "none" }}
              />
            </label>
            {loadingPreview && <p>Reading columns...</p>}
          </>
        )}

        {step === "map" && (
          <form onSubmit={handleSubmit}>
            <p className="subtitle">
              Pick which county to search, and which column in your CSV has
              the owner name. Property Description is optional - if it&apos;s a
              legal description (e.g. &quot;Lot 5 Block 2 Sunset Park&quot;), we&apos;ll use
              it to automatically pick the right property when a search turns up
              multiple matches for the same name.
            </p>

            <div className="field-row">
              <label>Target County *</label>
              <select value={targetCounty} onChange={(e) => setTargetCounty(e.target.value)} required>
                <option value="">-- Select a county --</option>
                {SUPPORTED_COUNTIES.map((c) => (
                  <option key={countyKey(c.county, c.state)} value={countyKey(c.county, c.state)}>
                    {c.label}
                  </option>
                ))}
              </select>
            </div>

            <div className="field-row">
              <label>Owner Name *</label>
              <select
                value={mapping.owner_name}
                onChange={(e) => setMapping({ ...mapping, owner_name: e.target.value })}
                required
              >
                <option value="">-- Select a column --</option>
                {headers.map((h) => (
                  <option key={h} value={h}>
                    {h}
                    {sampleRow?.[h] ? ` (e.g. "${sampleRow[h]}")` : ""}
                  </option>
                ))}
              </select>
            </div>

            <div className="field-row">
              <label>Property Description (optional)</label>
              <select
                value={mapping.property_description}
                onChange={(e) => setMapping({ ...mapping, property_description: e.target.value })}
              >
                <option value="">-- None --</option>
                {headers.map((h) => (
                  <option key={h} value={h}>
                    {h}
                    {sampleRow?.[h] ? ` (e.g. "${sampleRow[h]}")` : ""}
                  </option>
                ))}
              </select>
            </div>

            <div className="form-actions">
              <button type="button" className="secondary-btn" onClick={handleBack} disabled={submitting}>
                Back
              </button>
              <button type="submit" disabled={submitting || !canSubmit}>
                {submitting ? "Uploading..." : "Start lookup"}
              </button>
            </div>
          </form>
        )}
      </div>
    </main>
  );
}
