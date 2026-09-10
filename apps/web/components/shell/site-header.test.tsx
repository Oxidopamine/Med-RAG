import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const pathname = vi.hoisted(() => ({ current: "/" }));

vi.mock("next/navigation", () => ({
  usePathname: () => pathname.current,
}));

vi.mock("./release-chip", () => ({
  ReleaseChip: () => <span>Release guidelines-test</span>,
}));

import { NAVIGATION, SiteHeader } from "./site-header";

afterEach(cleanup);

describe("SiteHeader", () => {
  it("names every route once in the primary navigation and marks the current one", () => {
    pathname.current = "/corpus";
    render(<SiteHeader />);
    const nav = screen.getByRole("navigation", { name: "Primary" });
    // Scoped to the bar's own navigation: the phone drawer renders the same links first
    // in document order, now that its button sits inside the bar rather than under it.
    for (const item of NAVIGATION) {
      expect(within(nav).getByRole("link", { name: item.label })).toBeInTheDocument();
    }
    const current = nav.querySelector('a[aria-current="page"]');
    expect(current).not.toBeNull();
    expect(current).toHaveTextContent("Corpus");
  });

  it("treats a shared run as the workspace", () => {
    pathname.current = "/r/Q_abc";
    render(<SiteHeader />);
    const nav = screen.getByRole("navigation", { name: "Primary" });
    expect(nav.querySelector('a[aria-current="page"]')).toHaveTextContent("Workspace");
  });

  it("states the served release in the bar", () => {
    pathname.current = "/";
    render(<SiteHeader />);
    expect(screen.getAllByText("Release guidelines-test").length).toBeGreaterThan(0);
  });
});
