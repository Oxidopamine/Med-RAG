import createClient from "openapi-fetch";

import {
  corpusReadinessSchema,
  parseContract,
  questionAcceptedSchema,
  questionResultSchema,
} from "@/lib/contracts";
import type { paths } from "@/lib/generated/api-schema";
import type {
  ClinicalContext,
  CorpusReadiness,
  QuestionAccepted,
  QuestionResult,
  SourceFilters,
} from "@/lib/types";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const client = createClient<paths>({ baseUrl: API_URL });

function errorDetail(error: unknown, status: number): string {
  if (error && typeof error === "object" && "detail" in error) {
    const detail = error.detail;
    if (typeof detail === "string") return detail;
  }
  return `Request failed with status ${status}`;
}

const DEFAULT_SOURCE_FILTERS: SourceFilters = {
  jurisdictions: ["US", "EU", "UK"],
  organizations: [],
};

export async function getCorpusReadiness(): Promise<CorpusReadiness> {
  const { data, error, response } = await client.GET("/health/ready", {
    cache: "no-store",
  });
  if (!response.ok) throw new Error(errorDetail(error, response.status));
  return parseContract(corpusReadinessSchema, data, "corpus readiness payload");
}

export async function submitQuestion(
  question: string,
  sourceFilters: SourceFilters = DEFAULT_SOURCE_FILTERS,
): Promise<QuestionAccepted> {
  const { data, error, response } = await client.POST("/v1/questions", {
    body: {
      question,
      source_filters: sourceFilters,
      conversation_id: null,
    },
  });
  if (!response.ok) throw new Error(errorDetail(error, response.status));
  return parseContract(questionAcceptedSchema, data, "question acceptance payload");
}

export async function getQuestion(questionId: string): Promise<QuestionResult> {
  const { data, error, response } = await client.GET("/v1/questions/{question_id}", {
    params: { path: { question_id: questionId } },
    cache: "no-store",
  });
  if (!response.ok) throw new Error(errorDetail(error, response.status));
  return parseContract(questionResultSchema, data, "question result");
}

export async function replaceQuestionContext(
  questionId: string,
  context: ClinicalContext,
): Promise<QuestionAccepted> {
  const { data, error, response } = await client.PATCH(
    "/v1/questions/{question_id}/context",
    {
      params: { path: { question_id: questionId } },
      body: { context },
    },
  );
  if (!response.ok) throw new Error(errorDetail(error, response.status));
  return parseContract(questionAcceptedSchema, data, "context update response");
}
