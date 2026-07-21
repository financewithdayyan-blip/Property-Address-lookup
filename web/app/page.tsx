"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

type Step = "select" | "map";

interface Mapping {
  owner_name: string;
  county: string;
  state: string;
  property_description: string; // "" means not mapped (optional field)
}

const REQUIRED_FIELDS: { key: keyof Mapping; label: string; aliases: string[] }[] = [
  { key: "owner_name", label: "Owner Name", aliases: ["ownername", "owner", "name", "fullname"] },
  { key: "county", label: "County", aliases: ["county"] },
  { key: "state", label: "State", aliases: ["state", "st"] },
];

const DESCRIPTION_ALIASES = ["propertydescription", "description", "desc", "notes", "propertynotes"];

// Splits into word tokens rather than one joined blob, so e.g. "County
// Name" matches the "county" alias via its token but "Real Estate Notes"
// doesn't false-positive-match "state" the way naive substring matching
// on "realestatenotes" would.
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
    owner_name: findMatch(REQUIRED_FIELDS[0].aliases),
    county: findMatch(REQUIRED_FIELDS[1].aliases),
    state: findMatch(REQUIRED_FIELDS[2].aliases),
    property_description: findMatch(DESCRIPTION_ALIASES),
  };
}

export default function UploadPage() {
  const router = useRouter();
  const [step, setStep] = useState<Step>("select");
  const [file, setFile] = useState<File | null>(null);
  const [headers, setHeaders] = useState<string[]>([]);
  const [sampleRow, setSampleRow] = useState<Record<string, string> | null>(null);
  const [mapping, setMapping] = useState<Mapping>({
    owner_name: "",
    county: "",
    state: "",
    property_description: "",
  });
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
    setSubmitting(true);
    setError(null);

    const formData = new FormData();
    formData.append("file", file);
    formData.append(
      "mapping",
      JSON.stringify({
        owner_name: mapping.owner_name,
        county: mapping.county,
        state: mapping.state,
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

  const canSubmit = mapping.owner_name && mapping.county && mapping.state;

  return (
    <main>
      <h1>Property Address Lookup</h1>
      <p className="subtitle">
        Upload a leads CSV. Currently supported: Pinellas County, FL.
      </p>
      <div className="card">
        {error && <div className="error">{error}</div>}

        {step === "select" && (
          <>
            <input
              className="file-input"
              type="file"
              accept=".csv"
              onChange={handleFileChange}
              disabled={loadingPreview}
            />
            {loadingPreview && <p>Reading columns...</p>}
          </>
        )}

        {step === "map" && (
          <form onSubmit={handleSubmit}>
            <p className="subtitle">
              Match your CSV&apos;s columns to what we need. Owner Name, County, and
              State are required; Property Description is optional.
            </p>

            {REQUIRED_FIELDS.map(({ key, label }) => (
              <div className="field-row" key={key}>
                <label>{label} *</label>
                <select
                  value={mapping[key]}
                  onChange={(e) => setMapping({ ...mapping, [key]: e.target.value })}
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
            ))}

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
