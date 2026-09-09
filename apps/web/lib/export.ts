import type { CitationIndex } from "./evidence-presentation";
import type { QuestionResult } from "./types";

/**
 * Reference exports for the cited passages: RIS for reference managers, BibTeX for
 * LaTeX. One entry per cited passage, numbered as the answer numbers them, so a
 * citation in the exported text can be matched to the reference it names.
 */

function year(detail: QuestionResult["evidence_details"][number]): string {
  const fromLabel = /\b(19|20)\d{2}\b/.exec(detail.source_version_label)?.[0];
  if (fromLabel) return fromLabel;
  const fromDate = detail.effective_from ? /^\d{4}/.exec(detail.effective_from)?.[0] : null;
  return fromDate ?? "";
}

function pages(locationLabel: string): string {
  return /\d+/.exec(locationLabel)?.[0] ?? "";
}

export function toRis(citations: CitationIndex, result: QuestionResult): string {
  const lines: string[] = [];
  for (const citation of citations.ordered) {
    const detail = citation.detail;
    lines.push("TY  - RPRT");
    lines.push(`ID  - ${result.question_id}-${citation.number}`);
    lines.push(`TI  - ${detail.source_title}`);
    lines.push(`AU  - ${detail.publisher_name}`);
    lines.push(`PB  - ${detail.publisher_name}`);
    const published = year(detail);
    if (published) lines.push(`PY  - ${published}`);
    lines.push(`ET  - ${detail.source_version_label}`);
    const page = pages(citation.locationLabel);
    if (page) lines.push(`SP  - ${page}`);
    lines.push(`UR  - ${detail.source_url}`);
    lines.push(`N1  - ${citation.locationLabel}. Cited by review ${result.question_id}. Research use only.`);
    lines.push("ER  - ");
    lines.push("");
  }
  return lines.join("\r\n");
}

function bibKey(result: QuestionResult, number: number): string {
  const stem = result.question_id.replace(/[^A-Za-z0-9]/g, "").slice(-8) || "review";
  return `${stem}-${number}`;
}

function escapeBib(value: string): string {
  return value.replaceAll(/([{}])/g, "\\$1");
}

export function toBibTeX(citations: CitationIndex, result: QuestionResult): string {
  return citations.ordered
    .map((citation) => {
      const detail = citation.detail;
      const fields = [
        `  title = {${escapeBib(detail.source_title)}}`,
        `  author = {{${escapeBib(detail.publisher_name)}}}`,
        `  institution = {${escapeBib(detail.publisher_name)}}`,
        `  edition = {${escapeBib(detail.source_version_label)}}`,
      ];
      const published = year(detail);
      if (published) fields.push(`  year = {${published}}`);
      const page = pages(citation.locationLabel);
      if (page) fields.push(`  pages = {${page}}`);
      fields.push(`  url = {${detail.source_url}}`);
      fields.push(`  note = {${escapeBib(citation.locationLabel)}. Cited by review ${result.question_id}. Research use only.}`);
      return `@techreport{${bibKey(result, citation.number)},\n${fields.join(",\n")}\n}`;
    })
    .join("\n\n");
}

/** Offer a text file to the reader's browser. */
export function downloadText(filename: string, text: string, type = "text/plain"): void {
  const blob = new Blob([text], { type: `${type};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}
