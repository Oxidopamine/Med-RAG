"use client";

import { useEffect } from "react";

import { applyTextScale, readSettings } from "@/lib/settings";

/** Applies the stored text scale on load. Renders nothing. */
export function SettingsBoot() {
  useEffect(() => {
    applyTextScale(readSettings().textScale);
  }, []);
  return null;
}
