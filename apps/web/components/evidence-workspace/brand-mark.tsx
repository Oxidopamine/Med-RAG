/**
 * The product mark: a virion — filled capsid, eight knobbed surface spikes.
 *
 * Drawn here rather than imported because lucide carries no virus glyph, and the two it
 * offers nearby both say the wrong thing: `Biohazard` is a containment warning, and `Bug`
 * is an insect. The box, the 24-unit grid and the round joins follow lucide's conventions
 * so it sits beside `ShieldAlert` in the header without reading as a second icon set.
 *
 * Two departures from that house style, both forced by the 15px the header renders it at.
 * The capsid is filled rather than stroked: a 5.2r ring at that size is a one-pixel hoop,
 * and a hoop with rays coming off it is a ship's wheel, not a virus. And the spikes end in
 * knobs rather than plain caps — the knobs are the whole signal. Without them eight even
 * rays around a disc read as a sun; with them the silhouette is lobed, which is the shape
 * that survives shrinking. Eight spikes rather than six: six leaves gaps wide enough that
 * the lobes read as separate dots instead of as one body.
 *
 * `app/icon.svg` carries this same geometry for the browser tab. The two are kept in step
 * by `brand-mark.test.tsx`, which fails if a path here has no counterpart there.
 */
export function BrandMark({ size = 24 }: { size?: number }) {
  return (
    <svg
      aria-hidden="true"
      fill="none"
      focusable="false"
      height={size}
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth={2}
      viewBox="0 0 24 24"
      width={size}
      xmlns="http://www.w3.org/2000/svg"
    >
      {/* Capsid. */}
      <circle cx="12" cy="12" r="5.2" fill="currentColor" />

      {/* Surface spikes, every 45° from vertical: stem, then its knob. */}
      <path d="M12 6.8L12 4.4" />
      <circle cx="12" cy="3.58" r="1.5" fill="currentColor" stroke="none" />
      <path d="M8.32 8.32L6.63 6.63" />
      <circle cx="6.04" cy="6.04" r="1.5" fill="currentColor" stroke="none" />
      <path d="M6.8 12L4.4 12" />
      <circle cx="3.58" cy="12" r="1.5" fill="currentColor" stroke="none" />
      <path d="M8.32 15.68L6.63 17.37" />
      <circle cx="6.04" cy="17.96" r="1.5" fill="currentColor" stroke="none" />
      <path d="M12 17.2L12 19.6" />
      <circle cx="12" cy="20.42" r="1.5" fill="currentColor" stroke="none" />
      <path d="M15.68 15.68L17.37 17.37" />
      <circle cx="17.96" cy="17.96" r="1.5" fill="currentColor" stroke="none" />
      <path d="M17.2 12L19.6 12" />
      <circle cx="20.42" cy="12" r="1.5" fill="currentColor" stroke="none" />
      <path d="M15.68 8.32L17.37 6.63" />
      <circle cx="17.96" cy="6.04" r="1.5" fill="currentColor" stroke="none" />
    </svg>
  );
}
