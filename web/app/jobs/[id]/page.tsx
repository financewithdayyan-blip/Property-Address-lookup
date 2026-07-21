"use client";

import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

interface JobRowError {
  owner_name_input: string;
  county: string;
  state: string;
  error_message: string;
}

interface JobRow {
  row_index: number;
  owner_name_input: string;
  county: string;
  state: string;
  processing_status: "pending" | "claimed" | "done";
  owner_name_found: string;
  property_address: string;
  mailing_address: string;
  parcel_id: string;
  result_status: string;
  match_score: string;
}

interface JobStatusResponse {
  job: {
    id: string;
    status: "pending" | "running" | "done";
    total_rows: number;
    processed_rows: number;
  };
  statusCounts: Record<string, number>;
  errors: JobRowError[];
  rows: JobRow[];
  error?: string;
}

const POLL_MS = 2500;

export default function JobStatusPage() {
  const params = useParams<{ id: string }>();
  const jobId = params.id;
  const [data, setData] = useState<JobStatusResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;

    async function poll() {
      try {
        const res = await fetch(`/api/jobs/${jobId}`, { cache: "no-store" });
        const json = await res.json();
        if (cancelled) return;
        if (!res.ok) {
          setError(json.error ?? "Could not load job status.");
          return;
        }
        setData(json);
        if (json.job.status !== "done") {
          timer = setTimeout(poll, POLL_MS);
        }
      } catch {
        if (!cancelled) timer = setTimeout(poll, POLL_MS);
      }
    }
    poll();

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [jobId]);

  if (error) {
    return (
      <main>
        <div className="error">{error}</div>
      </main>
    );
  }

  if (!data) {
    return (
      <main>
        <p>Loading...</p>
      </main>
    );
  }

  const { job, statusCounts, errors, rows } = data;
  const pct = job.total_rows > 0 ? Math.round((job.processed_rows / job.total_rows) * 100) : 0;
  const hasMultiMatches = (statusCounts["MULTIPLE MATCHES"] ?? 0) > 0;
  const queueRows = rows.filter((r) => r.processing_status !== "done");
  const resultRows = rows.filter((r) => r.processing_status === "done");

  return (
    <main>
      <h1>Job status</h1>
      <p className="subtitle">
        {job.status === "done"
          ? "Done."
          : job.status === "running"
          ? "Processing..."
          : "Queued - waiting for the worker to pick this up."}
      </p>

      <div className="card">
        <div className="progress-bar">
          <div className="progress-bar-fill" style={{ width: `${pct}%` }} />
        </div>
        <p>
          {job.processed_rows} / {job.total_rows} rows processed ({pct}%)
        </p>

        <div className="status-grid">
          {Object.entries(statusCounts).map(([status, count]) => (
            <div className="status-tile" key={status}>
              <div className="count">{count}</div>
              <div className="label">{status}</div>
            </div>
          ))}
        </div>

        {job.status === "done" && (
          <div className="download-links">
            <a href={`/api/jobs/${jobId}/download`}>Download results CSV</a>
            {hasMultiMatches && (
              <a className="secondary" href={`/api/jobs/${jobId}/download-multi`}>
                Download multiple-matches CSV
              </a>
            )}
          </div>
        )}

        {errors && errors.length > 0 && (
          <div className="error-list">
            <h3>Rows that errored</h3>
            <ul>
              {errors.map((e, i) => (
                <li key={i}>
                  <strong>{e.owner_name_input}</strong> ({e.county}, {e.state}): {e.error_message}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {queueRows.length > 0 && (
        <div className="card">
          <h3>Queue ({queueRows.length} waiting)</h3>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>#</th>
                  <th>Owner name</th>
                  <th>County</th>
                  <th>State</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {queueRows.map((r) => (
                  <tr key={r.row_index}>
                    <td>{r.row_index + 1}</td>
                    <td>{r.owner_name_input}</td>
                    <td>{r.county}</td>
                    <td>{r.state}</td>
                    <td>{r.processing_status === "claimed" ? "Searching..." : "Waiting"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {resultRows.length > 0 && (
        <div className="card">
          <h3>Results ({resultRows.length} done)</h3>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>#</th>
                  <th>Owner name (input)</th>
                  <th>Owner name (found)</th>
                  <th>Property address</th>
                  <th>Mailing address</th>
                  <th>Parcel ID</th>
                  <th>Status</th>
                  <th>Score</th>
                </tr>
              </thead>
              <tbody>
                {resultRows.map((r) => (
                  <tr key={r.row_index}>
                    <td>{r.row_index + 1}</td>
                    <td>{r.owner_name_input}</td>
                    <td>{r.owner_name_found}</td>
                    <td>{r.property_address}</td>
                    <td>{r.mailing_address}</td>
                    <td>{r.parcel_id}</td>
                    <td>{r.result_status}</td>
                    <td>{r.match_score}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </main>
  );
}
