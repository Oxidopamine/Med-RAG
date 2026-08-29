import { readFileSync } from "node:fs";
import { join } from "node:path";

import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { BrandMark } from "./brand-mark";

afterEach(cleanup);

const ICON_PATH = join(__dirname, "..", "..", "app", "icon.svg");

/**
 * The drawing itself, in document order, with presentation dropped.
 *
 * Read off parsed elements rather than matched out of the markup: the two copies of this
 * mark are written differently on purpose - one self-closes its tags, the other is JSX
 * output - and a comparison that could see that difference would be comparing the wrong
 * thing. What has to agree is the geometry, so that is all this returns.
 */
function geometry(root: ParentNode): string[] {
  return [...root.querySelectorAll("path, circle")].map((node) => {
    const at = (name: string) => node.getAttribute(name) ?? "";
    return node.tagName.toLowerCase() === "path"
      ? `path ${at("d")}`
      : `circle ${at("cx")},${at("cy")} r${at("r")}`;
  });
}

function parseIcon(): Document {
  return new DOMParser().parseFromString(readFileSync(ICON_PATH, "utf8"), "image/svg+xml");
}

describe("BrandMark", () => {
  it("is decorative, so it carries no name of its own", () => {
    const { container } = render(<BrandMark />);
    const svg = container.querySelector("svg");

    // The lockup labels the link; a mark that also announced itself would be read twice.
    expect(svg).toHaveAttribute("aria-hidden", "true");
    expect(svg).toHaveAttribute("focusable", "false");
  });

  it("scales from one prop, so the tile and the box cannot disagree", () => {
    const { container } = render(<BrandMark size={40} />);
    const svg = container.querySelector("svg");

    expect(svg).toHaveAttribute("width", "40");
    expect(svg).toHaveAttribute("height", "40");
    // The viewBox is fixed: the geometry is authored once, at 24, and only ever scaled.
    expect(svg).toHaveAttribute("viewBox", "0 0 24 24");
  });
});

/*
 * The tab icon is a second copy of this drawing, and a second copy is a thing that drifts.
 * `app/icon.svg` may restyle it - it paints its own ink tile, scales the mark up and
 * thickens the stems for 16px - but the coordinates underneath have to be the same ones,
 * or the tab stops showing the product's mark and starts showing a near miss.
 */
describe("the tab icon", () => {
  it("is well-formed XML, which is the bar a browser holds it to", () => {
    // An SVG loaded as an image is parsed strictly, and a strict parser rejects the whole
    // file over things a lenient HTML parse would forgive - a double hyphen inside a
    // comment, an unclosed tag. The failure mode is a blank tab, not a warning.
    expect(parseIcon().querySelector("parsererror")).toBeNull();
  });

  it("draws the same virion as the component", () => {
    const { container } = render(<BrandMark />);

    const drawn = geometry(container);
    expect(drawn).toHaveLength(17); // capsid + 8 stems + 8 knobs
    expect(geometry(parseIcon())).toEqual(drawn);
  });
});
