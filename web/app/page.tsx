"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

export default function UploadPage() {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!file) {
      setError("Choose a CSV file first.");
      return;
    }
    setSubmitting(true);
    setError(null);

    const formData = new FormData();
    formData.append("file", file);

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

  return (
    <main>
      <h1>Property Address Lookup</h1>
      <p className="subtitle">
        Upload a CSV with <code>owner_name</code>, <code>county</code>, and{" "}
        <code>state</code> columns. Currently supported: Pinellas County, FL.
      </p>
      <div className="card">
        <form onSubmit={handleSubmit}>
          {error && <div className="error">{error}</div>}
          <input
            className="file-input"
            type="file"
            accept=".csv"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
          <button type="submit" disabled={submitting || !file}>
            {submitting ? "Uploading..." : "Start lookup"}
          </button>
        </form>
      </div>
    </main>
  );
}
