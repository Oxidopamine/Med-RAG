import type { Metadata } from "next";

import { SourceReader } from "@/components/pages/source-reader";
import { PageFrame } from "@/components/shell/page-frame";

export const metadata: Metadata = {
  title: "Source · Sentinel RAG",
  description: "A guideline in the served release: its editions, licence terms and pages.",
};

export default async function SourcePage({
  params,
}: {
  params: Promise<{ sourceId: string }>;
}) {
  const { sourceId } = await params;
  return (
    <PageFrame
      title="Source"
      lede="A document in the served release: its editions, the terms it is read under, and its pages where the licence permits them."
    >
      <SourceReader sourceId={sourceId} />
    </PageFrame>
  );
}
