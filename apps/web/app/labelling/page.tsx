import type { Metadata } from "next";

import { LabellingWorkbench } from "@/components/pages/labelling-workbench";
import { PageFrame } from "@/components/shell/page-frame";

export const metadata: Metadata = {
  title: "Labelling · Sentinel RAG",
  description: "The three passes of the pre-registered human census, labelled in this browser.",
};

export default function LabellingPage() {
  return (
    <PageFrame
      title="Labelling"
      lede="The three passes of the pre-registered census, labelled in this browser with the passage beside the claim. Files are read locally and the label file is written back as a download; nothing is sent anywhere."
    >
      <LabellingWorkbench />
    </PageFrame>
  );
}
