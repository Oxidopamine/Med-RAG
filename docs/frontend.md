# The web client

`apps/web` is a Next.js App Router application: a research instrument for evidence-gated
review of clinical guidelines. It is a thin client over the FastAPI service: it submits a
question, follows the run, and renders what the service returns. It never composes,
verifies or reproduces guideline text on its own.

## Structure

Routes, all under one shell (header with primary navigation and the served release,
footer with the reference pages):

| Route | What it is |
|---|---|
| `/` | The workspace: the question form with the corpus at a glance beside it, then the review desk |
| `/r/[questionId]` | A review opened by its identifier, on the same desk; printable |
| `/corpus` | The served release, the documents and editions it carries evidence from, the registered publishers |
| `/sources/[sourceId]` | A document: editions, licence terms, and its pages where the licence permits rendering |
| `/reviews` | Every review the service still holds, merged with this browser's history; filters, CSV export; flagged claims |
| `/methods` | How a review runs, how to read a result, the glossary, the boundary. The only place the interface explains itself |
| `/evaluation` | The released coverage runs, paired comparisons, minimum detectable effect and retrieval figures |
| `/labelling` | The labelling workbench for the pre-registered census, three passes, in the browser |
| `/settings` | Text size, default publishers, export format; kept in the browser |
| `/about` | The instrument, its licences, its author |

Server data comes from four endpoints: `GET /health/ready` (the served release),
`GET /v1/corpus` (the catalogue), `GET /v1/questions` (the review list) and the question
routes (`POST /v1/questions`, `GET /v1/questions/{id}`, its events stream, and the context
patch). The OpenAPI document is checked in and generates the TypeScript contract; Zod
schemas validate every payload at runtime. The catalogue and summary payloads are typed
from their schemas rather than the generated types, which mark defaulted fields optional.

The Evaluation page renders `lib/generated/evaluation.json`, written by
`scripts/export_web_evaluation.py` from the released benchmark files, with their digests.

## Design foundations

- **Two registers, one page.** The instrument speaks in monospace and the document speaks
  in a serif. Anything measured or identified wears the microlabel: monospace at the floor
  size, letterspaced, upper case, for column headers, field labels, release identifiers,
  stage names and axis ticks. Anything read wears the serif: page and section titles, the
  claims, quoted passages and every headline figure. The sans carries the sentences in
  between. A label is never a sentence and a sentence is never a label.
- **The chrome is ink.** The top bar is near-black, the one dark surface in the product,
  so the page has a top edge and the instrument's controls are visibly not the document.
- **A warm ground.** The neutral ramp is rotated warm, and quoted text sits on a warmer
  paper still. The cool grey it replaced is the default of every dashboard.
- **One accent.** A deep teal for links, selection and the active state. Black is the
  primary action and nothing else. Status is a mark and a few words; colour is reserved
  for supported, withheld and abstained.
- **Rules, not boxes.** A section is a hairline and a heading; a table is ruled; key
  figures sit in a band ruled top and bottom. A border is for an object a reader acts on:
  the composer, a claim, the passage pane. Nothing is boxed inside a box.
- **One grid.** Every page, narrow ones included, shares the same left edge and the same
  measure; narrow caps the body's width rather than centring it. Inside the review desk
  every section starts at its column's edge.
- **One control scale.** 28px for a control inside a line of text, 34px for a panel
  control, 40px for an action, 44px for anything on touch. Nothing in between.
- **A page is a band and a body.** Every page opens with a full-bleed title band on the
  surface colour carrying the title, one line of lede and the page's own controls, then
  the body on the canvas below it. `PageFrame` is the only way to build one.
- **One control kit.** `components/shell/shell.module.css` defines the buttons, inputs,
  selects, radios, checkboxes, tables, stat bands, status marks, cards, empty states and
  disclosures used across the product. Nothing native ships unstyled: a browser default
  in the middle of a designed page is the loudest thing on it.
- **Type.** Six sizes and one reading size: 12, 13, 14, 16, 20, 24, 40, plus 18px for
  claims and passages. Every size is absolute and in rem, so the reader's text-size
  setting scales all of it and no ratio can compound below the 12px floor. Titles are
  40/24/20px in the reading face.
- **Tables set their own widths.** Every table carries a `colgroup` with explicit
  percentages. Automatic widths wrapped four columns of eight and made a three-row table
  as tall as a paragraph.
- **Paper.** Quoted source text sits on a warm paper tint with a light term highlight, so
  it reads as a quotation without a label.
- **Icons** appear on actions only.
- **One word per outcome.** A review that gave no answer says "No answer" on the answer,
  on the checks badge, in the strip and in the log. It was called three different things
  on one screen.
- **Copy.** The Methods page explains; panels state. The research-use boundary is stated
  once, in the footer and on Methods; the identifier guard speaks only when a question
  trips it.

## The workspace

**Start.** The form (title, question field sized for a question, sources and question
bank as quiet controls, one black button, examples as cards carrying the question they
fill in) with the corpus at a glance beside it: the served release, its counts as a ruled
band, and the reviews this browser opened.

**Answered.** The form folds away and the question becomes the page's headline, with
"Edit question" and "New review" beside it. A finished review is about its question, not
about the form that asked it.

**Running.** The five stages as a strip with the current one marked, a skeleton of the
answer beneath, and the run identifier.

**The desk.** Above 1280px the answer and its checks sit on the left and the source pane
on the right, pinned, so a claim and the passage it rests on are readable against each
other. Below that the desk is one column with three panes behind tabs (Answer, Evidence,
Checks); choosing a citation in the answer opens the evidence pane.

- *Provenance line*: the release and its standing, the activation date, the run;
  identifiers and the manifest behind Details; Copy link; an Export menu (print or PDF,
  RIS, BibTeX, the audit record).
- *Answer*: one status line, the claims as numbered recommendations in the reading face
  with superscript citations, one line of evidence roles under each, a flag control, then
  the references as footnotes numbered as the citations are.
- *Interpreted context*: chips under the question, with Edit context beside them.
- *Checks*: the five stages, each with one outcome, in one row of five. Durations and the
  full stage names are in the log behind a disclosure.
- *Conflicts*: two passages side by side on paper with the disagreement as the caption.
- *Sources*: a reader. A vertical list of retrieved records, each naming its document,
  page and citation state, then the passage, one metadata line, the page where the licence
  permits one and a single line where it does not, everything else behind Details. The
  toolbar has navigation, find, and actions; text size is an app setting.
- *No answer*: the same desk, with the reason, the next step and the closest passages as
  references.

## Behaviour worth knowing

- Readiness is checked with retries and again when the tab regains focus after a
  failure; an unreachable service is said in the neutral tone with a way to check again.
- Floating panels close on the outside click, not on pointerdown, because closing early
  shrinks the page under a pending click.
- Flags raised on claims, reader settings, the review history and the label file stay in
  the browser. Nothing is sent to the service that the question itself does not carry.
- The labelling workbench reproduces the worksheet builder's seeded orderings exactly:
  `lib/labelling.ts` implements CPython's Mersenne Twister, `shuffle` and `sample`, so
  item numbers match the key files for the same seed.

## Charts

The evaluation page draws its own SVG; there is no chart library. Four arms whose answered
rates are nearly identical are a dot plot on one labelled axis rather than four bars of
almost the same length, and the power table is a curve with its numbers kept in a table
behind a disclosure. Every chart carries `role="img"` and an `aria-label` that states the
finding, and the table beside it is the text alternative.

## Testing

Vitest with Testing Library for components and libraries; Playwright for the workflow
(Chromium and Firefox, the API mocked at `http://localhost:8000`, every source endpoint
answered with a 404 so page frames settle the same way on every host). A local
`.env.local` may point the client at another port; run the browser suite with
`NEXT_PUBLIC_API_URL=http://localhost:8000` so the mocks match.
