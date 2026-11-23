import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Star } from "lucide-react";
import type { FeedbackRequest } from "../types/api";
import { ragService } from "../services/rag";
import { errorMessage } from "../lib/http";
import { Button } from "./ui/button";
import { Card } from "./ui/card";
import { Textarea } from "./ui/textarea";

export function RetrievalFeedback({ logId, traceId }: { logId: string; traceId: string }) {
  const [score, setScore] = useState(0);
  const [comment, setComment] = useState("");
  const [submissionId] = useState(() =>
    Array.from(crypto.getRandomValues(new Uint8Array(16)), (byte) => byte.toString(16).padStart(2, "0")).join(""),
  );
  const feedback = useMutation({
    mutationFn: (input: FeedbackRequest) => ragService.submitFeedback(input, submissionId),
    retry: false,
  });

  return (
    <Card className="mb-5">
      {feedback.isSuccess ? <p role="status" className="text-sm text-primary">已提交，已评 {feedback.data.score} 分</p> :
        <form aria-label="检索反馈" onSubmit={(event) => {
          event.preventDefault();
          if (score && !feedback.isPending) {
            feedback.mutate({ log_id: logId, trace_id: traceId, score, comment: comment.trim() || undefined });
          }
        }}>
          <fieldset disabled={feedback.isPending} className="space-y-3">
            <legend className="text-sm font-semibold">为本次检索评分</legend>
            <div className="flex items-center" role="radiogroup" aria-label="评分">
              {[1, 2, 3, 4, 5].map((value) => (
                <label key={value} className="cursor-pointer rounded p-1 focus-within:ring-2 focus-within:ring-primary">
                  <input className="sr-only" type="radio" name="feedback-score" value={value} aria-label={`${value} 分`}
                    checked={score === value} onChange={() => setScore(value)} />
                  <Star size={23} aria-hidden="true" className={value <= score ? "fill-amber-400 text-amber-500" : "text-slate-400"} />
                </label>
              ))}
            </div>
            <label className="block text-sm text-muted">备注（可选）
              <Textarea className="mt-1" rows={2} value={comment} onChange={(event) => setComment(event.target.value)} />
            </label>
            <Button type="submit" size="sm" disabled={!score || feedback.isPending}>{feedback.isPending ? "正在提交…" : "提交反馈"}</Button>
          </fieldset>
          {feedback.isError && <p role="alert" className="error-notice mt-2">{errorMessage(feedback.error)}</p>}
        </form>
      }
    </Card>
  );
}
