import { EvidenceWorkspace } from "@/components/evidence-workspace";

/**
 * A completed or in-flight review, addressable by its run identifier.
 *
 * The workspace used to live at one route with the run id held only in memory, so a
 * refresh lost the review and the id on the clipboard led nowhere. A run is the unit
 * this product asks people to audit, so it gets a URL.
 */
export default async function RunPage({
  params,
}: {
  params: Promise<{ questionId: string }>;
}) {
  const { questionId } = await params;
  return <EvidenceWorkspace initialQuestionId={questionId} />;
}
