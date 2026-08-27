import type { Metadata, Viewport } from "next";
import { Inter_Tight, JetBrains_Mono } from "next/font/google";

import "./globals.css";

const sans = Inter_Tight({
  variable: "--font-sans",
  subsets: ["latin"],
  display: "swap",
});

/** Identifiers, locators, page numbers and hashes - anything meant to be compared. */
const mono = JetBrains_Mono({
  variable: "--font-mono",
  subsets: ["latin"],
  weight: ["400", "500"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "Guideline Evidence QA",
  description: "Research-only clinical guideline evidence workspace",
};

/**
 * One theme, declared. `light` rather than `light dark` because the interface commits to
 * a single palette, and leaving the door open would let the browser paint its own chrome
 * in a scheme the stylesheet never designed for.
 */
export const viewport: Viewport = {
  colorScheme: "light",
  themeColor: "#ffffff",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className={`${sans.variable} ${mono.variable}`}>{children}</body>
    </html>
  );
}
