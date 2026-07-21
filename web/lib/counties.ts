// Counties the web version can actually search (WEB_SUPPORTED_SEARCH_TYPES
// in worker.py - see county_configs.py for each one's config). Kept as a
// small hand-maintained list here rather than fetched from the backend
// since it only changes when a county is added to county_configs.py,
// which already requires a code change anyway.
export interface SupportedCounty {
  county: string; // matches normalize_county()'s expected input, e.g. "Palm Beach"
  state: string; // two-letter abbreviation
  label: string; // shown in the dropdown
}

export const SUPPORTED_COUNTIES: SupportedCounty[] = [
  { county: "Pinellas", state: "FL", label: "Pinellas County, FL" },
  { county: "Hillsborough", state: "FL", label: "Hillsborough County, FL" },
  { county: "Lee", state: "FL", label: "Lee County, FL" },
  { county: "Palm Beach", state: "FL", label: "Palm Beach County, FL" },
  { county: "Duval", state: "FL", label: "Duval County, FL" },
];
