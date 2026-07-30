import type { SourceRef } from "@/types";

export type InterviewDifficulty = "mid" | "senior" | "expert";
export type InterviewCategory = "knowledge" | "profile" | "scenario" | "algorithm";
export type AlgorithmLanguage = "javascript" | "typescript" | "python";

export interface AlgorithmQuestion {
  title: string;
  slug: string;
  difficulty: "easy" | "mid" | "hard";
  description: string;
  parameters: string[];
  constraints: string[];
  functionName: string;
  starterCode: string;
  starterCodes: Record<AlgorithmLanguage, string>;
  testCases: Array<{
    args: unknown[];
    expected: unknown;
  }>;
}

export interface ScoreDimension {
  key: string;
  label: string;
  weight: number;
}

export interface InterviewMaterialState {
  uploaded: boolean;
  fileName?: string;
  contentHash?: string;
  fileSize?: number;
  updatedAt?: string;
  changed?: boolean;
}

export interface InterviewMaterials {
  resume: InterviewMaterialState;
  jd: InterviewMaterialState;
}

export interface InterviewQuestion {
  id: string;
  sequence: number;
  category: InterviewCategory;
  question: string;
  hasSource: boolean;
  agentGenerated: boolean;
  algorithm?: AlgorithmQuestion | null;
  answer: string;
  answerLanguage?: AlgorithmLanguage | null;
  skipped: boolean;
  answered: boolean;
  followUp?: string;
  rubric?: string[];
  source?: SourceRef | null;
  score?: number;
  analysis?: string;
  strengths?: string[];
  improvements?: string[];
  referencePoints?: string[];
}

export interface InterviewSession {
  id: string;
  status: "IN_PROGRESS" | "SCORING" | "COMPLETED" | "ABANDONED";
  kbId: string | null;
  kbName: string;
  targetRole: string;
  userFocus?: string | null;
  difficulty: InterviewDifficulty;
  questionCount: number;
  hasResume: boolean;
  hasJd: boolean;
  startedAt: string;
  completedAt?: string | null;
  questions: InterviewQuestion[];
  overallScore?: number;
  dimensionScores?: Record<string, number>;
  scoreDimensions?: ScoreDimension[];
  roleFitBreakdown?: Record<string, number>;
  summary?: string;
  profileDelta?: number;
  profileTrend?: "up" | "down" | "stable";
}

export interface InterviewProfileData {
  scoreDimensions: ScoreDimension[];
  profile: null | {
    dimensionScores: Record<string, number>;
    overallScore: number;
    previousOverallScore: number;
    delta: number;
    trend: "up" | "down" | "stable";
    interviewCount: number;
    latestInterviewId: string;
    targetRole: string;
    updatedAt: string;
  };
  recent: Array<{
    id: string;
    targetRole: string;
    kbName: string;
    difficulty: InterviewDifficulty;
    overallScore: number;
    completedAt: string;
  }>;
}

export interface InterviewHistoryItem {
  id: string;
  targetRole: string;
  kbName: string;
  difficulty: InterviewDifficulty;
  overallScore: number;
  dimensionScores: Record<string, number>;
  roleFitBreakdown: Record<string, number>;
  summary?: string;
  completedAt: string;
}

export interface InterviewHistoryData {
  total: number;
  scoreDimensions: ScoreDimension[];
  items: InterviewHistoryItem[];
}
