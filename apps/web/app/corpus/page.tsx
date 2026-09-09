import type { Metadata } from "next";

import { CorpusCatalogueView } from "@/components/pages/corpus-catalogue";
import { PageFrame } from "@/components/shell/page-frame";

export const metadata: Metadata = {
  title: "Corpus · Sentinel RAG",
  description: "The guideline release being served, the documents it carries, and the registered publishers.",
};

export default function CorpusPage() {
  return (
    <PageFrame
      title="Corpus"
      lede="The release being served, the documents and editions it carries evidence from, and the publishers the instrument is registered to acquire."
    >
      <CorpusCatalogueView />
    </PageFrame>
  );
}
