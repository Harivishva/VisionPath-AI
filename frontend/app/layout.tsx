import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "VisionPath AI — Intake decision support (prototype)",
  description:
    "Multimodal emergency-intake decision-support prototype. Not a diagnostic device; synthetic or consented demonstration data only.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
