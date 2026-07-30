export type ScoreTrend = "up" | "down" | "stable";

export const DEFAULT_INTERVIEW_TARGET_ROLE = "Agent开发工程师";
export const DEFAULT_INTERVIEW_USER_FOCUS =
  "偏重 Agent 任务规划、RAG 检索评估，并深挖我在简历中的相关项目";

export function resolveInterviewBrief(
  targetRole: string,
  userFocus: string,
): { targetRole: string; userFocus: string } {
  return {
    targetRole: targetRole.trim() || DEFAULT_INTERVIEW_TARGET_ROLE,
    userFocus: userFocus.trim() || DEFAULT_INTERVIEW_USER_FOCUS,
  };
}

export function clampInterviewScore(value: number): number {
  return Math.round(Math.max(0, Math.min(10, value)) * 10) / 10;
}

export function interviewProgress(
  answeredIndex: number,
  total: number,
): { current: number; percent: number } {
  const safeTotal = Math.max(1, total);
  const bounded = Math.max(0, Math.min(answeredIndex, safeTotal));
  return {
    current: Math.min(safeTotal, bounded + 1),
    percent: Math.round((bounded / safeTotal) * 100),
  };
}

export function nextUnansweredQuestionIndex(
  answered: boolean[],
  currentIndex: number,
): number {
  if (!answered.length || answered.every(Boolean)) return -1;
  for (let offset = 1; offset <= answered.length; offset += 1) {
    const index = (currentIndex + offset) % answered.length;
    if (!answered[index]) return index;
  }
  return -1;
}

export function lowestRatedQuestions<
  T extends { score?: number; sequence: number },
>(questions: T[], limit = 3): T[] {
  return questions
    .filter((question) => typeof question.score === "number")
    .sort(
      (left, right) =>
        (left.score as number) - (right.score as number)
        || left.sequence - right.sequence,
    )
    .slice(0, Math.max(0, limit));
}

export function radarPolygonPoints(
  values: number[],
  centerX: number,
  centerY: number,
  radius: number,
): string {
  if (!values.length) return "";
  return values
    .map((rawValue, index) => {
      const value = clampInterviewScore(rawValue) / 10;
      const angle = -Math.PI / 2 + (Math.PI * 2 * index) / values.length;
      const x = centerX + Math.cos(angle) * radius * value;
      const y = centerY + Math.sin(angle) * radius * value;
      return `${roundCoordinate(x)},${roundCoordinate(y)}`;
    })
    .join(" ");
}

function roundCoordinate(value: number): number {
  return Math.round(value * 10) / 10;
}

export function scoreTrend(delta: number): ScoreTrend {
  if (delta >= 0.3) return "up";
  if (delta <= -0.3) return "down";
  return "stable";
}

export function formatElapsed(totalSeconds: number): string {
  const seconds = Math.max(0, Math.floor(totalSeconds));
  const minutes = Math.floor(seconds / 60);
  return `${String(minutes).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}
