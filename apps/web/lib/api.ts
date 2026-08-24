import type { ClinicalContext, QuestionAccepted, QuestionResult } from "@/lib/types";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function parseResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    const detail = payload?.detail ?? `Request failed with status ${response.status}`;
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

export async function submitQuestion(question: string): Promise<QuestionAccepted> {
  const response = await fetch(`${API_URL}/v1/questions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      question,
      source_filters: { jurisdictions: ["US", "EU", "UK"], organizations: [] },
      conversation_id: null,
    }),
  });
  return parseResponse<QuestionAccepted>(response);
}

export async function getQuestion(questionId: string): Promise<QuestionResult> {
  const response = await fetch(`${API_URL}/v1/questions/${questionId}`, {
    cache: "no-store",
  });
  return parseResponse<QuestionResult>(response);
}

export async function replaceQuestionContext(
  questionId: string,
  context: ClinicalContext,
): Promise<QuestionAccepted> {
  const response = await fetch(`${API_URL}/v1/questions/${questionId}/context`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ context }),
  });
  return parseResponse<QuestionAccepted>(response);
}

