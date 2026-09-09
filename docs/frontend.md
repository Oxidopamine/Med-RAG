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

- **One accent.** A deep teal for links, selection and the active state. Black is the
  primary action and nothing else. Status is a mark and a few words, never a pill;
  colour is reserved for supported, withheld and abstained.
- **Two container tiers.** A section is set apart by space and a rule. Only an object (a
  form, a claim, a source) carries a border, and never inside another border. Radii are
  capped at 8px.
- **Type.** Public Sans for the interface; Source Serif 4, the reading face, for claims
  and quoted passages at 18px; JetBrains Mono only for hashes and identifiers inside a
  details disclosure. Headings are 28/24/18px and carry the hierarchy; there are no
  eyebrow labels.
- **Paper.** Quoted source text sits on a warm paper tint with a light term highlight, so
  it reads as a quotation without a label.
- **Icons** appear on actions only.
- **Copy.** The Methods page explains; panels state. The research-use boundary is stated
  once, in the footer and on Methods; the identifier guard speaks only when a question
  trips it.

## The workspace

**Start.** The form (title, question field with one line of help, sources and question
bank as quiet controls, one black button, examples as links) with the corpus at a glance
beside it: the served release, its counts, and the reviews this browser opened.

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
- *Checks*: the five stages with their outcome and duration; the log behind a disclosure.
- *Conflicts*: two passages side by side on paper with the disagreement as the caption.
- *Sources*: a reader. The passage first, one metadata line, the page where the licence
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

## Testing

Vitest with Testing Library for components and libraries; Playwright for the workflow
(Chromium and Firefox, the API mocked at `http://localhost:8000`, every source endpoint
answered with a 404 so page frames settle the same way on every host). A local
`.env.local` may point the client at another port; run the browser suite with
`NEXT_PUBLIC_API_URL=http://localhost:8000` so the mocks match.
