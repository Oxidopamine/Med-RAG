import type { Metadata } from "next";

import { ReviewList } from "@/components/pages/review-list";
import { PageFrame } from "@/components/shell/page-frame";

export const metadata: Metadata = {
  title: "Reviews · Sentinel RAG",
  description: "Every review the evidence service still holds, and the ones opened in this browser.",
};

export default function ReviewsPage() {
  return (
    <PageFrame
      title="Reviews"
      lede="Every review the evidence service still holds, newest first, with the ones opened in this browser. Open one to read it again, or export the list."
    >
      <ReviewList />
    </PageFrame>
  );
}
