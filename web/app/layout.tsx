import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Property Address Lookup",
  description: "Upload a leads CSV, get back property addresses.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
