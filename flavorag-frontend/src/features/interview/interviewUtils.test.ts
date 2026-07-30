import { describe, expect, it } from "vitest";
import {
  clampInterviewScore,
  interviewProgress,
  lowestRatedQuestions,
  nextUnansweredQuestionIndex,
  radarPolygonPoints,
  resolveInterviewBrief,
  scoreTrend,
} from "./interviewUtils";

describe("interview presentation contract", () => {
  it("clamps decimal scores to the supported 0-10 range", () => {
    expect(clampInterviewScore(12.4)).toBe(10);
    expect(clampInterviewScore(-2)).toBe(0);
    expect(clampInterviewScore(8.26)).toBe(8.3);
  });

  it("reports one-based progress without exceeding 100 percent", () => {
    expect(interviewProgress(0, 12)).toEqual({ current: 1, percent: 0 });
    expect(interviewProgress(5, 12)).toEqual({ current: 6, percent: 42 });
    expect(interviewProgress(20, 12)).toEqual({ current: 12, percent: 100 });
  });

  it("builds a closed six-axis radar polygon", () => {
    const points = radarPolygonPoints([10, 10, 10, 10, 10, 10], 100, 100, 80);
    expect(points.split(" ")).toHaveLength(6);
    expect(points).toContain("100,20");
  });

  it("uses a neutral band for tiny score fluctuations", () => {
    expect(scoreTrend(0.31)).toBe("up");
    expect(scoreTrend(-0.31)).toBe("down");
    expect(scoreTrend(0.19)).toBe("stable");
  });

  it("finds the next unanswered card and wraps around", () => {
    expect(nextUnansweredQuestionIndex([true, false, true, false], 1)).toBe(3);
    expect(nextUnansweredQuestionIndex([false, true, true, true], 3)).toBe(0);
    expect(nextUnansweredQuestionIndex([true, true], 0)).toBe(-1);
  });

  it("fills blank interview briefing fields with their placeholder defaults", () => {
    expect(resolveInterviewBrief("  ", "")).toEqual({
      targetRole: "Agent开发工程师",
      userFocus: "偏重 Agent 任务规划、RAG 检索评估，并深挖我在简历中的相关项目",
    });
    expect(resolveInterviewBrief("RAG 工程师", "  ")).toEqual({
      targetRole: "RAG 工程师",
      userFocus: "偏重 Agent 任务规划、RAG 检索评估，并深挖我在简历中的相关项目",
    });
  });

  it("selects the lowest-rated answered questions for history review", () => {
    const questions = [
      { id: "q1", sequence: 1, score: 8.2, answered: true, skipped: false },
      { id: "q2", sequence: 2, score: 4.6, answered: true, skipped: false },
      { id: "q3", sequence: 3, score: 0, answered: true, skipped: true },
      { id: "q4", sequence: 4, score: 6.1, answered: true, skipped: false },
    ];

    expect(lowestRatedQuestions(questions, 3).map((question) => question.id))
      .toEqual(["q3", "q2", "q4"]);
  });
});
