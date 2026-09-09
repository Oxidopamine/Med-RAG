import type { Metadata, Viewport } from "next";
import { JetBrains_Mono, Public_Sans, Source_Serif_4 } from "next/font/google";

import { SettingsBoot } from "@/components/shell/settings-boot";
import { SiteFooter } from "@/components/shell/site-footer";
import { SiteHeader } from "@/components/shell/site-header";

import "./globals.css";

const sans = Public_Sans({
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

// The reading face. Answers and passages are read, not scanned, and a serif at 18px
// holds a 65-character measure the way the sans does not.
const serif = Source_Serif_4({
  variable: "--font-serif",
  subsets: ["latin"],
  weight: ["400", "600"],
  style: ["normal", "italic"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "Sentinel RAG",
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
      <body className={`${sans.variable} ${mono.variable} ${serif.variable}`}>
        <SettingsBoot />
        <SiteHeader />
        {children}
        <SiteFooter />
      </body>
    </html>
  );
}
