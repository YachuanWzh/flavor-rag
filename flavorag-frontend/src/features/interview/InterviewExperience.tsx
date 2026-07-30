import {
  ArrowLeft,
  BookOpen,
  BriefcaseBusiness,
  Check,
  ChevronDown,
  CircleCheck,
  CircleX,
  Clock3,
  Code2,
  FileText,
  History as HistoryIcon,
  LoaderCircle,
  LogOut,
  MessageSquareText,
  Mic2,
  Play,
  RotateCcw,
  ShieldCheck,
  Sparkles,
  Target,
  TimerReset,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { KnowledgeBase, SourceRef } from "@/types";
import {
  abandonInterview,
  clearInterviewHistory,
  deleteInterviewMaterial,
  getInterview,
  getInterviewHistory,
  getInterviewMaterials,
  saveInterviewAnswer,
  saveInterviewJdText,
  startInterview,
  submitInterview,
  uploadInterviewMaterial,
} from "./interviewService";
import type {
  AlgorithmLanguage,
  InterviewCategory,
  InterviewDifficulty,
  InterviewHistoryData,
  InterviewMaterials,
  InterviewSession,
} from "./types";
import {
  DEFAULT_INTERVIEW_TARGET_ROLE,
  DEFAULT_INTERVIEW_USER_FOCUS,
  formatElapsed,
  interviewProgress,
  nextUnansweredQuestionIndex,
  resolveInterviewBrief,
} from "./interviewUtils";
import InterviewHistoryTimeline from "./InterviewHistoryTimeline";
import InterviewRadar from "./InterviewRadar";

interface Props {
  knowledgeBases: KnowledgeBase[];
  selectedKbId: string | null;
  conversationId: string | null;
  onClose: () => void;
  onViewSources: (sources: SourceRef[]) => void;
}

type Phase = "setup" | "active" | "review" | "history";

const ACTIVE_INTERVIEW_KEY = "flavorag.activeInterviewId";

const categoryMeta: Record<
  InterviewCategory,
  { label: string; detail: string; className: string }
> = {
  knowledge: {
    label: "知识深挖",
    detail: "围绕知识库证据追问原理与边界",
    className: "bg-cyan-50 text-cyan-700 ring-cyan-200",
  },
  profile: {
    label: "经历追问",
    detail: "验证简历、JD 与个人贡献",
    className: "bg-violet-50 text-violet-700 ring-violet-200",
  },
  scenario: {
    label: "场景设计",
    detail: "考察复杂约束下的决策与取舍",
    className: "bg-amber-50 text-amber-700 ring-amber-200",
  },
  algorithm: {
    label: "算法实战",
    detail: "Hot 100 在线编码与复杂度评估",
    className: "bg-indigo-50 text-indigo-700 ring-indigo-200",
  },
};

function algorithmStarterCodes(
  question: NonNullable<InterviewSession["questions"][number]["algorithm"]>,
): Record<AlgorithmLanguage, string> {
  if (question.starterCodes) return question.starterCodes;
  const parameters = question.parameters?.length
    ? question.parameters
    : question.starterCode
      .match(/^[^(]*\(([^)]*)\)/)?.[1]
      ?.split(",")
      .map((item) => item.trim())
      .filter(Boolean) || [];
  return {
    javascript: question.starterCode,
    typescript: (
      `function ${question.functionName}(${parameters.map((item) => `${item}: any`).join(", ")}): any {\n`
      + "  // 在这里编写你的解法\n\n}\n"
    ),
    python: (
      `def ${question.functionName}(${parameters.join(", ")}):\n`
      + "    # 在这里编写你的解法\n"
      + "    pass\n"
    ),
  };
}

function initialQuestionAnswer(
  question: InterviewSession["questions"][number] | undefined,
): string {
  if (!question) return "";
  const language = question.answerLanguage || "javascript";
  const starterCodes = question.algorithm
    ? algorithmStarterCodes(question.algorithm)
    : null;
  return question.answer
    || (question.answered ? "" : starterCodes?.[language])
    || (question.answered ? "" : question.algorithm?.starterCode)
    || "";
}

export default function InterviewExperience({
  knowledgeBases,
  selectedKbId,
  conversationId,
  onClose,
  onViewSources,
}: Props) {
  const [phase, setPhase] = useState<Phase>("setup");
  const [materials, setMaterials] = useState<InterviewMaterials>({
    resume: { uploaded: false },
    jd: { uploaded: false },
  });
  const [interview, setInterview] = useState<InterviewSession | null>(null);
  const [kbId, setKbId] = useState(selectedKbId && selectedKbId !== "*" ? selectedKbId : "*");
  const [targetRole, setTargetRole] = useState("");
  const [userFocus, setUserFocus] = useState("");
  const [difficulty, setDifficulty] = useState<InterviewDifficulty>("senior");
  const [questionCount, setQuestionCount] = useState(12);
  const [algorithmCount, setAlgorithmCount] = useState(0);
  const [jdText, setJdText] = useState("");
  const [showJdText, setShowJdText] = useState(false);
  const [answer, setAnswer] = useState("");
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [answerLanguage, setAnswerLanguage] = useState<AlgorithmLanguage>("javascript");
  const [draftLanguages, setDraftLanguages] = useState<Record<string, AlgorithmLanguage>>({});
  const [currentIndex, setCurrentIndex] = useState(0);
  const [elapsed, setElapsed] = useState(0);
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState<"resume" | "jd" | null>(null);
  const [error, setError] = useState("");
  const [expandedQuestion, setExpandedQuestion] = useState<string | null>(null);
  const [showExitConfirm, setShowExitConfirm] = useState(false);
  const [historyData, setHistoryData] = useState<InterviewHistoryData | null>(null);
  const [historyInterview, setHistoryInterview] = useState<InterviewSession | null>(null);
  const [historySelectedId, setHistorySelectedId] = useState<string>();
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState("");
  const [historyReturnPhase, setHistoryReturnPhase] = useState<"setup" | "review">("setup");
  const answerRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      getInterviewMaterials(),
      Promise.resolve(localStorage.getItem(ACTIVE_INTERVIEW_KEY)),
    ])
      .then(async ([materialState, activeId]) => {
        if (cancelled) return;
        setMaterials(materialState);
        if (!activeId) return;
        try {
          const restored = await getInterview(activeId);
          if (cancelled) return;
          if (restored.status === "ABANDONED") {
            localStorage.removeItem(ACTIVE_INTERVIEW_KEY);
            return;
          }
          setInterview(restored);
          const restoredDrafts = Object.fromEntries(
            restored.questions.map((question) => [
              question.id,
              initialQuestionAnswer(question),
            ]),
          );
          setDrafts(restoredDrafts);
          const restoredLanguages = Object.fromEntries(
            restored.questions
              .filter((question) => question.category === "algorithm")
              .map((question) => [
                question.id,
                question.answerLanguage || "javascript",
              ]),
          ) as Record<string, AlgorithmLanguage>;
          setDraftLanguages(restoredLanguages);
          if (restored.status === "COMPLETED") {
            setPhase("review");
          } else {
            const next = restored.questions.findIndex((question) => !question.answered);
            const restoredIndex = next < 0 ? restored.questions.length - 1 : next;
            setCurrentIndex(restoredIndex);
            setAnswer(restoredDrafts[restored.questions[restoredIndex]?.id] || "");
            setAnswerLanguage(
              restoredLanguages[restored.questions[restoredIndex]?.id] || "javascript",
            );
            setPhase("active");
          }
        } catch {
          localStorage.removeItem(ACTIVE_INTERVIEW_KEY);
        }
      })
      .catch(() => setError("无法读取面试材料状态，请稍后重试。"));
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (phase !== "active") return;
    const timer = window.setInterval(() => setElapsed((value) => value + 1), 1000);
    return () => window.clearInterval(timer);
  }, [phase]);

  useEffect(() => {
    if (phase === "active") {
      answerRef.current?.focus();
    }
  }, [phase, currentIndex]);

  const currentQuestion = interview?.questions[currentIndex];
  const progress = interview
    ? interviewProgress(
        interview.questions.filter((question) => question.answered).length,
        interview.questions.length,
      )
    : { current: 1, percent: 0 };

  const handleUpload = async (kind: "resume" | "jd", file?: File) => {
    if (!file) return;
    setError("");
    setUploading(kind);
    try {
      const result = await uploadInterviewMaterial(kind, file);
      setMaterials((state) => ({ ...state, [kind]: result }));
    } catch (uploadError) {
      setError(uploadError instanceof Error ? uploadError.message : "材料上传失败");
    } finally {
      setUploading(null);
    }
  };

  const handleSaveJdText = async () => {
    if (jdText.trim().length < 20) {
      setError("岗位 JD 至少需要 20 个字符。");
      return;
    }
    setUploading("jd");
    setError("");
    try {
      const result = await saveInterviewJdText(jdText);
      setMaterials((state) => ({ ...state, jd: result }));
      setShowJdText(false);
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "JD 保存失败");
    } finally {
      setUploading(null);
    }
  };

  const handleClearMaterial = async (kind: "resume" | "jd") => {
    if (uploading) return;
    setUploading(kind);
    setError("");
    try {
      await deleteInterviewMaterial(kind);
      setMaterials((state) => ({ ...state, [kind]: { uploaded: false } }));
      if (kind === "jd") {
        setJdText("");
        setShowJdText(false);
      }
    } catch (clearError) {
      setError(clearError instanceof Error ? clearError.message : "材料清空失败");
    } finally {
      setUploading(null);
    }
  };

  const handleStart = async () => {
    setLoading(true);
    setError("");
    try {
      const resolvedBrief = resolveInterviewBrief(targetRole, userFocus);
      setTargetRole(resolvedBrief.targetRole);
      setUserFocus(resolvedBrief.userFocus);
      const created = await startInterview({
        kbId: kbId === "*" ? null : kbId,
        conversationId,
        targetRole: resolvedBrief.targetRole,
        userFocus: resolvedBrief.userFocus,
        difficulty,
        questionCount,
        algorithmCount,
      });
      localStorage.setItem(ACTIVE_INTERVIEW_KEY, created.id);
      setInterview(created);
      setDrafts(Object.fromEntries(
        created.questions.map((question) => [
          question.id,
          initialQuestionAnswer(question),
        ]),
      ));
      setDraftLanguages(Object.fromEntries(
        created.questions
          .filter((question) => question.category === "algorithm")
          .map((question) => [question.id, question.answerLanguage || "javascript"]),
      ) as Record<string, AlgorithmLanguage>);
      setCurrentIndex(0);
      setAnswer("");
      setAnswerLanguage("javascript");
      setElapsed(0);
      setPhase("active");
    } catch (startError) {
      setError(startError instanceof Error ? startError.message : "面试题生成失败");
    } finally {
      setLoading(false);
    }
  };

  const persistAndAdvance = async (skipped = false) => {
    if (!interview || !currentQuestion || loading) return;
    if (!skipped && !answer.trim()) {
      setError("请先作答，或选择跳过本题。");
      return;
    }
    setLoading(true);
    setError("");
    try {
      await saveInterviewAnswer(
        interview.id,
        currentQuestion.id,
        skipped ? "" : answer.trim(),
        skipped,
        currentQuestion.category === "algorithm" ? answerLanguage : undefined,
      );
      const updatedQuestions = interview.questions.map((question, index) =>
        index === currentIndex
          ? {
              ...question,
              answer: skipped ? "" : answer.trim(),
              answerLanguage: currentQuestion.category === "algorithm"
                ? answerLanguage
                : null,
              skipped,
              answered: true,
            }
          : question,
      );
      const updatedInterview = { ...interview, questions: updatedQuestions };
      setInterview(updatedInterview);
      const savedAnswer = skipped ? "" : answer.trim();
      const updatedDrafts = { ...drafts, [currentQuestion.id]: savedAnswer };
      setDrafts(updatedDrafts);
      const nextIndex = nextUnansweredQuestionIndex(
        updatedQuestions.map((question) => question.answered),
        currentIndex,
      );
      if (nextIndex < 0) {
        setAnswer("");
        await finishInterview(updatedInterview);
      } else {
        setCurrentIndex(nextIndex);
        setAnswerLanguage(
          draftLanguages[updatedQuestions[nextIndex].id]
          || updatedQuestions[nextIndex].answerLanguage
          || "javascript",
        );
        setAnswer(
          updatedDrafts[updatedQuestions[nextIndex].id]
          ?? updatedQuestions[nextIndex].answer
          ?? updatedQuestions[nextIndex].algorithm?.starterCode
          ?? "",
        );
      }
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "答案保存失败");
    } finally {
      setLoading(false);
    }
  };

  const finishInterview = async (state = interview) => {
    if (!state) return;
    setLoading(true);
    setError("");
    try {
      const pending = state.questions.filter((question) => !question.answered);
      for (const question of pending) {
        await saveInterviewAnswer(state.id, question.id, "", true);
      }
      const completed = await submitInterview(state.id);
      setInterview(completed);
      setPhase("review");
      setExpandedQuestion(completed.questions[0]?.id || null);
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : "评分生成失败");
    } finally {
      setLoading(false);
    }
  };

  const resetInterview = () => {
    localStorage.removeItem(ACTIVE_INTERVIEW_KEY);
    setInterview(null);
    setPhase("setup");
    setCurrentIndex(0);
    setElapsed(0);
    setAnswer("");
    setDrafts({});
    setDraftLanguages({});
    setAnswerLanguage("javascript");
    setError("");
  };

  const updateAnswer = (value: string) => {
    setAnswer(value);
    if (currentQuestion) {
      setDrafts((state) => ({ ...state, [currentQuestion.id]: value }));
    }
  };

  const updateAnswerLanguage = (language: AlgorithmLanguage) => {
    if (!currentQuestion?.algorithm) return;
    const starters = algorithmStarterCodes(currentQuestion.algorithm);
    const isUntouched = !answer.trim() || Object.values(starters).some(
      (starter) => starter && starter.trim() === answer.trim(),
    );
    setAnswerLanguage(language);
    setDraftLanguages((state) => ({ ...state, [currentQuestion.id]: language }));
    if (isUntouched && starters[language]) {
      updateAnswer(starters[language]);
    }
  };

  const selectQuestion = (index: number) => {
    if (!interview || loading || index === currentIndex) return;
    const target = interview.questions[index];
    setDrafts((state) => ({
      ...state,
      ...(currentQuestion ? { [currentQuestion.id]: answer } : {}),
    }));
    setCurrentIndex(index);
    setAnswer(drafts[target.id] ?? initialQuestionAnswer(target));
    setAnswerLanguage(
      draftLanguages[target.id] || target.answerLanguage || "javascript",
    );
    setError("");
  };

  const selectHistoryInterview = async (id: string) => {
    if (historyLoading || historyInterview?.id === id) return;
    setHistorySelectedId(id);
    setHistoryLoading(true);
    setHistoryError("");
    try {
      setHistoryInterview(await getInterview(id));
    } catch (historyLoadError) {
      setHistoryError(
        historyLoadError instanceof Error
          ? historyLoadError.message
          : "历史面试详情加载失败",
      );
    } finally {
      setHistoryLoading(false);
    }
  };

  const openHistory = async () => {
    const returnPhase = phase === "review" ? "review" : "setup";
    setHistoryReturnPhase(returnPhase);
    setPhase("history");
    setHistoryLoading(true);
    setHistoryError("");
    try {
      const data = await getInterviewHistory();
      setHistoryData(data);
      const currentCompletedId = (
        returnPhase === "review" && interview?.status === "COMPLETED"
      ) ? interview.id : null;
      const selectedId = (
        currentCompletedId
        && data.items.some((item) => item.id === currentCompletedId)
      )
        ? currentCompletedId
        : data.items[0]?.id;
      if (!selectedId) {
        setHistorySelectedId(undefined);
        setHistoryInterview(null);
      } else if (selectedId === interview?.id && interview.status === "COMPLETED") {
        setHistorySelectedId(selectedId);
        setHistoryInterview(interview);
      } else {
        setHistorySelectedId(selectedId);
        setHistoryInterview(await getInterview(selectedId));
      }
    } catch (historyLoadError) {
      setHistoryData(null);
      setHistoryInterview(null);
      setHistorySelectedId(undefined);
      setHistoryError(
        historyLoadError instanceof Error
          ? historyLoadError.message
          : "面试历史加载失败",
      );
    } finally {
      setHistoryLoading(false);
    }
  };

  const clearHistory = async () => {
    if (historyLoading) return;
    setHistoryLoading(true);
    setHistoryError("");
    try {
      await clearInterviewHistory();
      setHistoryData((state) => ({
        total: 0,
        scoreDimensions: state?.scoreDimensions || [],
        items: [],
      }));
      setHistoryInterview(null);
      setHistorySelectedId(undefined);
    } catch (historyClearError) {
      setHistoryError(
        historyClearError instanceof Error
          ? historyClearError.message
          : "面试历史清空失败",
      );
    } finally {
      setHistoryLoading(false);
    }
  };

  const openHistoricalReview = () => {
    if (!historyInterview || historyInterview.status !== "COMPLETED") return;
    setInterview(historyInterview);
    setExpandedQuestion(historyInterview.questions[0]?.id || null);
    setPhase("review");
  };

  const confirmExit = async () => {
    if (!interview || loading) return;
    setLoading(true);
    setError("");
    try {
      await abandonInterview(interview.id);
      localStorage.removeItem(ACTIVE_INTERVIEW_KEY);
      setShowExitConfirm(false);
      setInterview(null);
      setDrafts({});
      setDraftLanguages({});
      setAnswer("");
      setAnswerLanguage("javascript");
      setPhase("setup");
      onClose();
    } catch (exitError) {
      setShowExitConfirm(false);
      setError(exitError instanceof Error ? exitError.message : "退出面试失败");
    } finally {
      setLoading(false);
    }
  };

  return (
    <section className="relative flex min-h-0 flex-1 flex-col overflow-hidden bg-slate-50">
      <div className="pointer-events-none absolute inset-0 opacity-60 [background-image:radial-gradient(circle_at_15%_10%,rgba(6,182,212,0.09),transparent_28%),radial-gradient(circle_at_85%_12%,rgba(37,99,235,0.08),transparent_24%)]" />
      {phase === "setup" && (
        <SetupRoom
          knowledgeBases={knowledgeBases}
          kbId={kbId}
          setKbId={setKbId}
          targetRole={targetRole}
          setTargetRole={setTargetRole}
          userFocus={userFocus}
          setUserFocus={setUserFocus}
          difficulty={difficulty}
          setDifficulty={setDifficulty}
          questionCount={questionCount}
          setQuestionCount={setQuestionCount}
          algorithmCount={algorithmCount}
          setAlgorithmCount={setAlgorithmCount}
          materials={materials}
          uploading={uploading}
          showJdText={showJdText}
          setShowJdText={setShowJdText}
          jdText={jdText}
          setJdText={setJdText}
          onUpload={handleUpload}
          onClearMaterial={handleClearMaterial}
          onSaveJdText={handleSaveJdText}
          onStart={handleStart}
          onOpenHistory={openHistory}
          onClose={onClose}
          loading={loading}
          error={error}
        />
      )}
      {phase === "active" && interview && currentQuestion && (
        <ActiveRoom
          interview={interview}
          question={currentQuestion}
          answer={answer}
          setAnswer={updateAnswer}
          answerLanguage={answerLanguage}
          setAnswerLanguage={updateAnswerLanguage}
          currentIndex={currentIndex}
          progress={progress}
          elapsed={elapsed}
          loading={loading}
          error={error}
          answerRef={answerRef}
          onAnswer={() => persistAndAdvance(false)}
          onSkip={() => persistAndAdvance(true)}
          onFinish={() => finishInterview()}
          onSelectQuestion={selectQuestion}
          onRequestExit={() => setShowExitConfirm(true)}
          onClose={onClose}
        />
      )}
      {phase === "review" && interview && (
        <ReviewRoom
          interview={interview}
          expandedQuestion={expandedQuestion}
          setExpandedQuestion={setExpandedQuestion}
          onViewSources={onViewSources}
          onOpenHistory={openHistory}
          onReset={resetInterview}
          onClose={onClose}
        />
      )}
      {phase === "history" && (
        <InterviewHistoryTimeline
          history={historyData}
          selected={historyInterview}
          selectedId={historySelectedId}
          loading={historyLoading}
          error={historyError}
          onSelect={selectHistoryInterview}
          onOpenFullReview={openHistoricalReview}
          onClear={clearHistory}
          onBack={() => setPhase(historyReturnPhase)}
          onClose={onClose}
          onViewSources={onViewSources}
        />
      )}
      {showExitConfirm && (
        <ExitConfirmDialog
          loading={loading}
          onCancel={() => setShowExitConfirm(false)}
          onConfirm={confirmExit}
        />
      )}
    </section>
  );
}

interface SetupRoomProps {
  knowledgeBases: KnowledgeBase[];
  kbId: string;
  setKbId: (value: string) => void;
  targetRole: string;
  setTargetRole: (value: string) => void;
  userFocus: string;
  setUserFocus: (value: string) => void;
  difficulty: InterviewDifficulty;
  setDifficulty: (value: InterviewDifficulty) => void;
  questionCount: number;
  setQuestionCount: (value: number) => void;
  algorithmCount: number;
  setAlgorithmCount: (value: number) => void;
  materials: InterviewMaterials;
  uploading: "resume" | "jd" | null;
  showJdText: boolean;
  setShowJdText: (value: boolean) => void;
  jdText: string;
  setJdText: (value: string) => void;
  onUpload: (kind: "resume" | "jd", file?: File) => void;
  onClearMaterial: (kind: "resume" | "jd") => void;
  onSaveJdText: () => void;
  onStart: () => void;
  onOpenHistory: () => void;
  onClose: () => void;
  loading: boolean;
  error: string;
}

function SetupRoom(props: SetupRoomProps) {
  return (
    <div className="relative z-10 flex min-h-0 flex-1 flex-col overflow-y-auto">
      <RoomHeader
        eyebrow="Interview briefing"
        title="面试准备室"
        detail="材料只用于你的面试出题与评分，不会写入公共知识库。"
        onClose={props.onClose}
      />
      <div className="mx-auto grid w-full max-w-6xl gap-6 px-5 py-6 lg:grid-cols-[1.05fr_0.95fr] lg:px-8 lg:py-9">
        <div className="rounded-3xl border border-slate-200 bg-white p-5 shadow-[0_18px_50px_-32px_rgba(15,23,42,0.35)] md:p-7">
          <div className="flex items-center gap-3">
            <div className="grid h-11 w-11 place-items-center rounded-2xl bg-slate-950 text-white">
              <BriefcaseBusiness className="h-5 w-5" />
            </div>
            <div>
              <h3 className="font-semibold text-slate-950">本场面试</h3>
              <p className="text-sm text-slate-500">确定岗位、知识范围与面试强度</p>
            </div>
          </div>
          <div className="mt-6 space-y-5">
            <Field label="目标岗位" hint="可留空，系统会尝试从 JD 识别">
              <input
                value={props.targetRole}
                onChange={(event) => props.setTargetRole(event.target.value)}
                placeholder={`例如：${DEFAULT_INTERVIEW_TARGET_ROLE}`}
                className="w-full rounded-xl border border-slate-200 bg-slate-50 px-3.5 py-3 text-sm text-slate-900 outline-none transition focus:border-cyan-500 focus:bg-white focus:ring-4 focus:ring-cyan-100"
              />
            </Field>
            <Field
              label="本场重点"
              hint="优先级最高，会与简历、JD 和知识库证据交叉出题"
            >
              <textarea
                value={props.userFocus}
                onChange={(event) => props.setUserFocus(event.target.value)}
                rows={3}
                maxLength={1000}
                placeholder={`例如：${DEFAULT_INTERVIEW_USER_FOCUS}`}
                className="w-full resize-none rounded-xl border border-slate-200 bg-slate-50 px-3.5 py-3 text-sm leading-6 text-slate-900 outline-none transition focus:border-cyan-500 focus:bg-white focus:ring-4 focus:ring-cyan-100"
              />
              <div className="mt-2 flex flex-wrap gap-1.5">
                {["Agent 架构", "RAG 检索评估", "简历项目深挖"].map((focus) => (
                  <button
                    key={focus}
                    type="button"
                    onClick={() => props.setUserFocus(
                      props.userFocus.includes(focus)
                        ? props.userFocus
                        : [props.userFocus.trim(), focus].filter(Boolean).join("、"),
                    )}
                    className="rounded-full border border-slate-200 bg-white px-2.5 py-1 text-[11px] font-medium text-slate-600 transition hover:border-cyan-300 hover:bg-cyan-50 hover:text-cyan-800"
                  >
                    + {focus}
                  </button>
                ))}
              </div>
            </Field>
            <Field label="知识范围" hint="选择单库可进行专项巩固">
              <div className="relative">
                <BookOpen className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
                <select
                  value={props.kbId}
                  onChange={(event) => props.setKbId(event.target.value)}
                  className="w-full appearance-none rounded-xl border border-slate-200 bg-slate-50 py-3 pl-10 pr-10 text-sm text-slate-800 outline-none focus:border-cyan-500 focus:bg-white focus:ring-4 focus:ring-cyan-100"
                >
                  <option value="*">全部可访问知识库</option>
                  {props.knowledgeBases.map((kb) => (
                    <option key={kb.id} value={kb.id}>{kb.name}</option>
                  ))}
                </select>
                <ChevronDown className="pointer-events-none absolute right-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
              </div>
            </Field>
            <Field label="面试难度" hint="按大厂技术面试深挖强度生成">
              <div className="grid grid-cols-3 gap-2">
                {([
                  ["mid", "中级", "核心掌握"],
                  ["senior", "高级", "原理与取舍"],
                  ["expert", "专家", "系统与决策"],
                ] as const).map(([key, label, detail]) => (
                  <button
                    key={key}
                    type="button"
                    onClick={() => props.setDifficulty(key)}
                    className={`rounded-xl border px-3 py-2.5 text-left transition ${
                      props.difficulty === key
                        ? "border-cyan-500 bg-cyan-50 ring-2 ring-cyan-100"
                        : "border-slate-200 bg-white hover:border-slate-300"
                    }`}
                  >
                    <span className="block text-sm font-semibold text-slate-900">{label}</span>
                    <span className="mt-0.5 block text-[11px] text-slate-500">{detail}</span>
                  </button>
                ))}
              </div>
            </Field>
            <Field
              label={`常规题数量 · ${props.questionCount} 题`}
              hint={props.algorithmCount ? `末尾另加 ${props.algorithmCount} 道算法题` : "最少 10 题，默认 12 题"}
            >
              <input
                type="range"
                min={10}
                max={20}
                value={props.questionCount}
                onChange={(event) => props.setQuestionCount(Number(event.target.value))}
                className="h-2 w-full cursor-pointer appearance-none rounded-full bg-slate-200 accent-cyan-600"
              />
              <div className="mt-2 flex justify-between text-[10px] font-medium text-slate-400">
                <span>10</span><span>15</span><span>20</span>
              </div>
            </Field>
            <Field label="LeetCode Hot 100" hint="easy 35% · mid 60% · hard 5%">
              <div className="grid grid-cols-3 gap-2" role="group" aria-label="算法题数量">
                {([
                  [0, "不添加", "常规面试"],
                  [1, "1 道", "末尾加题"],
                  [2, "2 道", "强化练习"],
                ] as const).map(([count, label, detail]) => (
                  <button
                    key={count}
                    type="button"
                    onClick={() => props.setAlgorithmCount(count)}
                    className={`rounded-xl border px-3 py-2.5 text-left transition ${
                      props.algorithmCount === count
                        ? "border-indigo-500 bg-indigo-50 ring-2 ring-indigo-100"
                        : "border-slate-200 bg-white hover:border-slate-300"
                    }`}
                  >
                    <span className="flex items-center gap-1.5 text-sm font-semibold text-slate-900">
                      {count > 0 && <Code2 className="h-3.5 w-3.5 text-indigo-600" />}
                      {label}
                    </span>
                    <span className="mt-0.5 block text-[11px] text-slate-500">{detail}</span>
                  </button>
                ))}
              </div>
            </Field>
          </div>
        </div>

        <div className="space-y-4">
          <MaterialCard
            icon={<FileText className="h-5 w-5" />}
            title="个人简历"
            detail="仅支持 PDF；用于经历追问和项目可信度分析"
            state={props.materials.resume}
            accept=".pdf,application/pdf"
            loading={props.uploading === "resume"}
            onFile={(file) => props.onUpload("resume", file)}
            onClear={() => props.onClearMaterial("resume")}
          />
          <MaterialCard
            icon={<Target className="h-5 w-5" />}
            title="岗位 JD"
            detail="支持 PDF、DOCX、TXT、Markdown，也可以直接粘贴"
            state={props.materials.jd}
            accept=".pdf,.docx,.txt,.md,.markdown"
            loading={props.uploading === "jd"}
            onFile={(file) => props.onUpload("jd", file)}
            onClear={() => props.onClearMaterial("jd")}
            secondaryAction={
              <button
                type="button"
                onClick={() => props.setShowJdText(!props.showJdText)}
                className="text-xs font-medium text-cyan-700 hover:text-cyan-800"
              >
                {props.showJdText ? "收起文本框" : "粘贴 JD"}
              </button>
            }
          />
          {props.showJdText && (
            <div className="rounded-2xl border border-cyan-200 bg-white p-4 shadow-sm">
              <textarea
                value={props.jdText}
                onChange={(event) => props.setJdText(event.target.value)}
                rows={6}
                placeholder="粘贴岗位职责、任职要求、技术栈和加分项…"
                className="w-full resize-none rounded-xl border border-slate-200 bg-slate-50 p-3 text-sm leading-6 outline-none focus:border-cyan-500 focus:bg-white"
              />
              <div className="mt-3 flex justify-end">
                <button
                  type="button"
                  disabled={props.uploading === "jd"}
                  onClick={props.onSaveJdText}
                  className="rounded-lg bg-slate-900 px-3.5 py-2 text-xs font-semibold text-white disabled:opacity-50"
                >
                  保存这份 JD
                </button>
              </div>
            </div>
          )}
          <div className="rounded-2xl border border-slate-200 bg-slate-900 p-5 text-white shadow-lg">
            <div className="flex items-start gap-3">
              <ShieldCheck className="mt-0.5 h-5 w-5 text-cyan-300" />
              <div>
                <p className="text-sm font-semibold">材料可以全部跳过</p>
                <p className="mt-1 text-xs leading-5 text-slate-300">
                  未上传材料时，Agent 会依据所选知识库自由出题。完整材料能获得更准确的经历追问和岗位匹配评分。
                </p>
              </div>
            </div>
            {props.error && (
              <p className="mt-4 rounded-xl bg-rose-500/15 px-3 py-2 text-xs text-rose-200">
                {props.error}
              </p>
            )}
            <button
              type="button"
              disabled={props.loading || props.uploading !== null}
              onClick={props.onStart}
              className="mt-5 flex w-full items-center justify-center gap-2 rounded-xl bg-cyan-400 px-4 py-3 text-sm font-semibold text-slate-950 transition hover:bg-cyan-300 disabled:cursor-wait disabled:opacity-60"
            >
              {props.loading ? (
                <><LoaderCircle className="h-4 w-4 animate-spin" /> 正在召回知识并布置面试…</>
              ) : (
                <><Mic2 className="h-4 w-4" /> 进入面试房间</>
              )}
            </button>
            <button
              type="button"
              disabled={props.loading}
              onClick={props.onOpenHistory}
              className="mt-2.5 flex w-full items-center justify-center gap-2 rounded-xl border border-white/15 px-4 py-2.5 text-xs font-semibold text-slate-200 transition hover:border-cyan-300/50 hover:bg-white/5 hover:text-white disabled:opacity-50"
            >
              <HistoryIcon className="h-4 w-4 text-cyan-300" />
              查看面试历史与能力变化
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function ActiveRoom({
  interview,
  question,
  answer,
  setAnswer,
  answerLanguage,
  setAnswerLanguage,
  currentIndex,
  progress,
  elapsed,
  loading,
  error,
  answerRef,
  onAnswer,
  onSkip,
  onFinish,
  onSelectQuestion,
  onRequestExit,
  onClose,
}: {
  interview: InterviewSession;
  question: InterviewSession["questions"][number];
  answer: string;
  setAnswer: (value: string) => void;
  answerLanguage: AlgorithmLanguage;
  setAnswerLanguage: (value: AlgorithmLanguage) => void;
  currentIndex: number;
  progress: { current: number; percent: number };
  elapsed: number;
  loading: boolean;
  error: string;
  answerRef: React.RefObject<HTMLTextAreaElement>;
  onAnswer: () => void;
  onSkip: () => void;
  onFinish: () => void;
  onSelectQuestion: (index: number) => void;
  onRequestExit: () => void;
  onClose: () => void;
}) {
  const meta = categoryMeta[question.category];
  return (
    <div className="relative z-10 flex min-h-0 flex-1 flex-col">
      <div className="border-b border-slate-200 bg-white/95 px-4 py-3 backdrop-blur md:px-6">
        <div className="mx-auto flex max-w-6xl items-center gap-4">
          <div className="hidden min-w-0 sm:block">
            <p className="truncate text-sm font-semibold text-slate-950">{interview.targetRole}</p>
            <p className="truncate text-[11px] text-slate-500">{interview.kbName} · {interview.difficulty}</p>
          </div>
          <div className="min-w-0 flex-1">
            <div className="mb-1.5 flex items-center justify-between text-[11px] font-medium text-slate-500">
              <span>第 {currentIndex + 1} / {interview.questions.length} 题</span>
              <span>{progress.percent}%</span>
            </div>
            <div className="h-1.5 overflow-hidden rounded-full bg-slate-100">
              <div
                className="h-full rounded-full bg-gradient-to-r from-cyan-500 to-blue-600 transition-all duration-500"
                style={{ width: `${progress.percent}%` }}
              />
            </div>
          </div>
          <div className="flex items-center gap-1.5 rounded-lg bg-slate-100 px-2.5 py-1.5 font-mono text-xs font-semibold tabular-nums text-slate-700">
            <Clock3 className="h-3.5 w-3.5 text-cyan-700" />
            {formatElapsed(elapsed)}
          </div>
          <button
            type="button"
            onClick={onRequestExit}
            className="hidden items-center gap-1.5 rounded-lg border border-rose-200 bg-rose-50 px-2.5 py-1.5 text-xs font-semibold text-rose-700 transition hover:bg-rose-100 sm:inline-flex"
          >
            <LogOut className="h-3.5 w-3.5" />
            退出本场
          </button>
          <button
            type="button"
            onClick={onClose}
            aria-label="暂时离开面试"
            className="rounded-lg p-2 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      </div>

      <div className="border-b border-slate-200 bg-slate-50/95 px-4 py-3 md:px-6">
        <div className="mx-auto flex max-w-6xl items-center gap-3">
          <div
            className="grid min-w-0 flex-1 grid-flow-col auto-cols-[2.25rem] gap-2 overflow-x-auto pb-1"
            aria-label="面试题目导航"
          >
            {interview.questions.map((item, index) => {
              const isCurrent = index === currentIndex;
              const isAnswered = item.answered && !item.skipped;
              const isSkipped = item.answered && item.skipped;
              return (
                <button
                  key={item.id}
                  type="button"
                  disabled={loading}
                  onClick={() => onSelectQuestion(index)}
                  aria-label={`第 ${index + 1} 题，${
                    isAnswered ? "已回答" : isSkipped ? "已跳过" : "未回答"
                  }${isCurrent ? "，当前题" : ""}`}
                  aria-current={isCurrent ? "step" : undefined}
                  className={`grid h-9 w-9 shrink-0 place-items-center rounded-lg border text-xs font-bold tabular-nums transition ${
                    isCurrent
                      ? "border-slate-950 bg-slate-950 text-white ring-4 ring-cyan-100"
                      : isAnswered
                        ? "border-emerald-500 bg-emerald-500 text-white hover:bg-emerald-600"
                        : isSkipped
                          ? "border-amber-300 bg-amber-50 text-amber-700 hover:bg-amber-100"
                          : "border-slate-200 bg-white text-slate-500 hover:border-cyan-400 hover:text-cyan-700"
                  }`}
                >
                  {isAnswered ? <Check className="h-4 w-4" strokeWidth={3} /> : isSkipped ? "—" : index + 1}
                </button>
              );
            })}
          </div>
          <div className="hidden shrink-0 items-center gap-3 text-[10px] text-slate-500 md:flex">
            <span className="flex items-center gap-1">
              <span className="h-2 w-2 rounded-sm bg-emerald-500" />已回答
            </span>
            <span className="flex items-center gap-1">
              <span className="h-2 w-2 rounded-sm bg-amber-200" />已跳过
            </span>
          </div>
          <button
            type="button"
            onClick={onRequestExit}
            aria-label="退出本场面试"
            className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-rose-200 bg-rose-50 text-rose-700 sm:hidden"
          >
            <LogOut className="h-4 w-4" />
          </button>
        </div>
      </div>

      <div className="flex min-h-0 flex-1 overflow-y-auto px-4 py-6 md:px-8 md:py-10">
        <div className="mx-auto flex w-full max-w-4xl flex-col">
          <div className="mb-5 flex items-center gap-3">
            <span className={`inline-flex rounded-full px-3 py-1 text-xs font-semibold ring-1 ${meta.className}`}>
              {meta.label}
            </span>
            <span className="text-xs text-slate-500">{meta.detail}</span>
            {question.hasSource && (
              <span className="ml-auto hidden items-center gap-1 text-[11px] text-emerald-600 sm:flex">
                <Check className="h-3.5 w-3.5" /> 已绑定知识库证据
              </span>
            )}
          </div>

          <article className="relative rounded-[28px] border border-slate-200 bg-white p-6 shadow-[0_24px_70px_-42px_rgba(15,23,42,0.45)] md:p-9">
            <div className="absolute -left-3 top-8 grid h-7 w-7 place-items-center rounded-full border-4 border-slate-50 bg-slate-950 text-[10px] font-bold text-white">
              Q
            </div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-cyan-700">
              Interviewer
            </p>
            {question.category === "algorithm" ? (
              <>
                <h2 className="mt-4 text-xl font-semibold leading-9 tracking-tight text-slate-950 md:text-2xl md:leading-10">
                  {question.question.split("\n\n")[0]}
                </h2>
                {question.algorithm ? (
                  <AlgorithmProblemDetails
                    question={question.algorithm}
                    fallbackDescription={question.question.split("\n\n").slice(1).join("\n\n")}
                  />
                ) : (
                  <p className="mt-3 whitespace-pre-line text-sm leading-7 text-slate-600 md:text-[15px]">
                    {question.question.split("\n\n").slice(1).join("\n\n")}
                  </p>
                )}
              </>
            ) : (
              <h2 className="mt-4 text-xl font-semibold leading-9 tracking-tight text-slate-950 md:text-2xl md:leading-10">
                {question.question}
              </h2>
            )}
            <div className="mt-5 flex items-center gap-2 border-t border-slate-100 pt-4 text-xs text-slate-400">
              <MessageSquareText className="h-4 w-4" />
              {question.category === "algorithm"
                ? "先运行示例确认结果；AI 将继续检查复杂度、边界条件与代码质量。"
                : "请像真实面试一样先给结论，再解释原理、取舍和验证方式。"}
            </div>
          </article>

          <div className="mt-5 rounded-3xl border border-slate-200 bg-white p-4 shadow-sm md:p-5">
            {question.category === "algorithm" && question.algorithm ? (
              <AlgorithmAnswerEditor
                question={question.algorithm}
                value={answer}
                onChange={setAnswer}
                language={answerLanguage}
                onLanguageChange={setAnswerLanguage}
                answerRef={answerRef}
                onSubmit={onAnswer}
              />
            ) : (
              <>
                <div className="mb-3 flex items-center justify-between">
                  <label htmlFor="interview-answer" className="text-sm font-semibold text-slate-800">
                    你的回答
                  </label>
                  <span className="text-[11px] text-slate-400">Ctrl / Cmd + Enter 提交</span>
                </div>
                <textarea
                  ref={answerRef}
                  id="interview-answer"
                  value={answer}
                  onChange={(event) => setAnswer(event.target.value)}
                  onKeyDown={(event) => {
                    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
                      event.preventDefault();
                      onAnswer();
                    }
                  }}
                  rows={7}
                  placeholder="开始作答…"
                  className="w-full resize-none rounded-2xl border border-slate-200 bg-slate-50 p-4 text-[15px] leading-7 text-slate-800 outline-none transition focus:border-cyan-500 focus:bg-white focus:ring-4 focus:ring-cyan-100"
                />
              </>
            )}
            {error && <p className="mt-2 text-xs text-rose-600">{error}</p>}
            <div className="mt-4 flex flex-wrap items-center gap-2">
              <button
                type="button"
                disabled={loading}
                onClick={onAnswer}
                className="inline-flex min-w-36 items-center justify-center gap-2 rounded-xl bg-slate-950 px-5 py-3 text-sm font-semibold text-white hover:bg-slate-800 disabled:opacity-50"
              >
                {loading ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
                我答完了
              </button>
              <button
                type="button"
                disabled={loading}
                onClick={onSkip}
                className="rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm font-medium text-slate-600 hover:bg-slate-50 disabled:opacity-50"
              >
                跳过本题
              </button>
              <button
                type="button"
                disabled={loading}
                onClick={onFinish}
                className="ml-auto rounded-xl px-3 py-2 text-xs font-medium text-slate-400 hover:bg-rose-50 hover:text-rose-600 disabled:opacity-50"
              >
                提前结束并评分
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function stringifyCaseValue(value: unknown): string {
  const serialized = JSON.stringify(value, null, 2);
  return serialized === undefined ? String(value) : serialized;
}

function AlgorithmProblemDetails({
  question,
  fallbackDescription,
}: {
  question: NonNullable<InterviewSession["questions"][number]["algorithm"]>;
  fallbackDescription: string;
}) {
  const starterParameters = question.starterCode
    ?.match(/^[^(]*\(([^)]*)\)/)?.[1]
    ?.split(",")
    .map((item) => item.trim())
    .filter(Boolean) || [];
  const parameters = question.parameters?.length ? question.parameters : starterParameters;
  const constraints = question.constraints || [];
  return (
    <div className="mt-5 space-y-5">
      <section>
        <h3 className="text-xs font-semibold uppercase tracking-[0.12em] text-slate-400">
          题目描述
        </h3>
        <p className="mt-2 whitespace-pre-line text-sm leading-7 text-slate-700 md:text-[15px]">
          {question.description || fallbackDescription}
        </p>
      </section>

      <section className="rounded-2xl border border-indigo-100 bg-indigo-50/60 p-4">
        <h3 className="text-xs font-semibold text-indigo-900">函数要求</h3>
        <p className="mt-1.5 text-sm leading-6 text-indigo-800">
          请实现下面的 JavaScript 函数，并返回题目要求的结果。不要修改函数名和参数顺序。
        </p>
        <code className="mt-3 block overflow-x-auto rounded-xl bg-slate-950 px-3.5 py-3 font-mono text-xs text-indigo-200">
          {`function ${question.functionName}(${parameters.join(", ")})`}
        </code>
      </section>

      {constraints.length > 0 && (
        <section>
          <h3 className="text-xs font-semibold uppercase tracking-[0.12em] text-slate-400">
            数据范围
          </h3>
          <ul className="mt-2 grid gap-2 sm:grid-cols-2">
            {constraints.map((constraint) => (
              <li
                key={constraint}
                className="flex items-start gap-2 rounded-xl bg-slate-50 px-3 py-2 font-mono text-[11px] leading-5 text-slate-600 ring-1 ring-slate-200"
              >
                <span className="mt-2 h-1 w-1 shrink-0 rounded-full bg-indigo-500" />
                {constraint}
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="border-t border-slate-100 pt-5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">
            示例 Case
          </h3>
          <span className="text-[11px] text-slate-400">与下方“运行示例”使用同一组数据</span>
        </div>
        <div className="mt-3 grid gap-3 md:grid-cols-2">
          {question.testCases.map((testCase, index) => (
            <div
              key={`${question.slug}-${index}`}
              className="overflow-hidden rounded-2xl border border-slate-200 bg-slate-50"
            >
              <div className="flex items-center justify-between border-b border-slate-200 bg-white px-3.5 py-2">
                <span className="font-mono text-[10px] font-bold tracking-[0.16em] text-indigo-600">
                  CASE {String(index + 1).padStart(2, "0")}
                </span>
                <span className="text-[10px] text-slate-400">公开示例</span>
              </div>
              <div className="grid gap-3 p-3.5 text-xs">
                <div>
                  <p className="mb-1.5 font-semibold text-slate-500">输入</p>
                  <pre className="overflow-x-auto whitespace-pre-wrap break-words rounded-lg bg-slate-900 px-3 py-2.5 font-mono leading-5 text-slate-100">
                    {testCase.args.map((argument, argumentIndex) => (
                      `${parameters[argumentIndex] || `arg${argumentIndex + 1}`} = ${stringifyCaseValue(argument)}`
                    )).join("\n")}
                  </pre>
                </div>
                <div>
                  <p className="mb-1.5 font-semibold text-slate-500">期望输出</p>
                  <pre className="overflow-x-auto whitespace-pre-wrap break-words rounded-lg bg-emerald-50 px-3 py-2.5 font-mono leading-5 text-emerald-800 ring-1 ring-emerald-100">
                    {stringifyCaseValue(testCase.expected)}
                  </pre>
                </div>
              </div>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

type AlgorithmRunResult = {
  passed: boolean;
  actual?: unknown;
  expected: unknown;
  error?: string;
};

const algorithmLanguageOptions: Array<{
  key: AlgorithmLanguage;
  label: string;
  extension: string;
  runtime: string;
}> = [
  { key: "javascript", label: "JavaScript", extension: "js", runtime: "ES2022" },
  { key: "typescript", label: "TypeScript", extension: "ts", runtime: "TS 5" },
  { key: "python", label: "Python", extension: "py", runtime: "Python 3" },
];

function formatPythonCode(code: string): string {
  const lines = code
    .replace(/\t/g, "    ")
    .split(/\r?\n/)
    .map((line) => line.replace(/\s+$/g, ""));
  const normalized: string[] = [];
  let blankLines = 0;
  for (const line of lines) {
    if (!line.trim()) {
      blankLines += 1;
      if (blankLines <= 2) normalized.push("");
      continue;
    }
    blankLines = 0;
    const leadingSpaces = line.length - line.trimStart().length;
    const normalizedIndent = " ".repeat(Math.floor(leadingSpaces / 4) * 4);
    normalized.push(normalizedIndent + line.trimStart());
  }
  return `${normalized.join("\n").trim()}\n`;
}

async function formatJavaScriptLike(
  code: string,
  language: "javascript" | "typescript",
): Promise<string> {
  const ts = await import("typescript");
  const sourceFile = ts.createSourceFile(
    language === "typescript" ? "solution.ts" : "solution.js",
    code,
    ts.ScriptTarget.Latest,
    true,
    language === "typescript" ? ts.ScriptKind.TS : ts.ScriptKind.JS,
  );
  return `${ts.createPrinter({
    newLine: ts.NewLineKind.LineFeed,
  }).printFile(sourceFile).trim()}\n`;
}

function AlgorithmAnswerEditor({
  question,
  value,
  onChange,
  language,
  onLanguageChange,
  answerRef,
  onSubmit,
}: {
  question: NonNullable<InterviewSession["questions"][number]["algorithm"]>;
  value: string;
  onChange: (value: string) => void;
  language: AlgorithmLanguage;
  onLanguageChange: (value: AlgorithmLanguage) => void;
  answerRef: React.RefObject<HTMLTextAreaElement>;
  onSubmit: () => void;
}) {
  const [running, setRunning] = useState(false);
  const [formatting, setFormatting] = useState(false);
  const [results, setResults] = useState<AlgorithmRunResult[] | null>(null);
  const activeLanguage = algorithmLanguageOptions.find(
    (item) => item.key === language,
  ) || algorithmLanguageOptions[0];

  const formatAnswer = async () => {
    if (formatting || !value.trim()) return;
    setFormatting(true);
    try {
      const formatted = language === "python"
        ? formatPythonCode(value)
        : await formatJavaScriptLike(value, language);
      onChange(formatted);
      setResults(null);
    } catch (formatError) {
      setResults([
        {
          passed: false,
          expected: "语法正确的代码",
          error: formatError instanceof Error ? formatError.message : "格式化失败",
        },
      ]);
    } finally {
      setFormatting(false);
    }
  };

  const runExamples = async () => {
    if (running || language === "python") return;
    setRunning(true);
    setResults(null);
    let executableCode = value;
    if (language === "typescript") {
      try {
        const ts = await import("typescript");
        executableCode = ts.transpileModule(value, {
          compilerOptions: {
            target: ts.ScriptTarget.ES2022,
            module: ts.ModuleKind.None,
          },
          reportDiagnostics: true,
        }).outputText;
      } catch (transpileError) {
        setResults([
          {
            passed: false,
            expected: "可转译的 TypeScript 代码",
            error: transpileError instanceof Error
              ? transpileError.message
              : "TypeScript 转译失败",
          },
        ]);
        setRunning(false);
        return;
      }
    }
    const workerSource = `
      self.onmessage = (event) => {
        const { code, functionName, tests } = event.data;
        try {
          const factory = new Function(
            '"use strict";\\n' + code +
            '\\nreturn typeof ' + functionName + ' === "function" ? ' + functionName + ' : null;'
          );
          const candidate = factory();
          if (typeof candidate !== "function") {
            throw new Error("未找到函数 " + functionName);
          }
          const results = tests.map((test) => {
            try {
              const args = JSON.parse(JSON.stringify(test.args));
              const actual = candidate(...args);
              if (actual && typeof actual.then === "function") {
                throw new Error("示例运行暂不支持异步函数");
              }
              return {
                passed: JSON.stringify(actual) === JSON.stringify(test.expected),
                actual,
                expected: test.expected,
              };
            } catch (error) {
              return {
                passed: false,
                expected: test.expected,
                error: error instanceof Error ? error.message : String(error),
              };
            }
          });
          self.postMessage({ results });
        } catch (error) {
          self.postMessage({
            error: error instanceof Error ? error.message : String(error),
          });
        }
      };
    `;
    const workerUrl = URL.createObjectURL(
      new Blob([workerSource], { type: "text/javascript" }),
    );
    const worker = new Worker(workerUrl);
    const finish = () => {
      worker.terminate();
      URL.revokeObjectURL(workerUrl);
      setRunning(false);
    };
    const timeout = window.setTimeout(() => {
      setResults([
        {
          passed: false,
          expected: "2 秒内完成",
          error: "运行超时，请检查是否存在死循环或复杂度过高。",
        },
      ]);
      finish();
    }, 2000);
    worker.onmessage = (event: MessageEvent<{
      results?: AlgorithmRunResult[];
      error?: string;
    }>) => {
      window.clearTimeout(timeout);
      setResults(
        event.data.results || [
          {
            passed: false,
            expected: "可运行的 JavaScript 函数",
            error: event.data.error || "运行失败",
          },
        ],
      );
      finish();
    };
    worker.onerror = (event) => {
      window.clearTimeout(timeout);
      setResults([
        {
          passed: false,
          expected: "可运行的 JavaScript 函数",
          error: event.message || "代码解析失败",
        },
      ]);
      finish();
    };
    worker.postMessage({
      code: executableCode,
      functionName: question.functionName,
      tests: question.testCases,
    });
  };

  const passedCount = results?.filter((result) => result.passed).length || 0;
  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <label htmlFor="interview-answer" className="text-sm font-semibold text-slate-800">
            JavaScript 解答
          </label>
          <span className="rounded-md bg-indigo-50 px-2 py-0.5 font-mono text-[10px] font-semibold text-indigo-700 ring-1 ring-indigo-200">
            {question.functionName}()
          </span>
        </div>
        <button
          type="button"
          disabled={formatting || !value.trim()}
          onClick={formatAnswer}
          title="格式化代码（Shift + Alt + F）"
          className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-[11px] font-semibold text-slate-600 transition hover:border-indigo-300 hover:bg-indigo-50 hover:text-indigo-700 disabled:opacity-50"
        >
          {formatting ? (
            <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Code2 className="h-3.5 w-3.5" />
          )}
          格式化
        </button>
      </div>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div
          className="inline-flex rounded-xl bg-slate-100 p-1 ring-1 ring-slate-200"
          role="group"
          aria-label="编程语言"
        >
          {algorithmLanguageOptions.map((option) => (
            <button
              key={option.key}
              type="button"
              onClick={() => {
                onLanguageChange(option.key);
                setResults(null);
              }}
              className={`rounded-lg px-3 py-1.5 text-[11px] font-semibold transition ${
                language === option.key
                  ? "bg-white text-indigo-700 shadow-sm ring-1 ring-slate-200"
                  : "text-slate-500 hover:text-slate-800"
              }`}
              aria-pressed={language === option.key}
            >
              {option.label}
            </button>
          ))}
        </div>
        <span className="text-[11px] text-slate-400">Ctrl / Cmd + Enter 提交</span>
      </div>
      <div className="overflow-hidden rounded-2xl border border-slate-800 bg-[#111827] shadow-inner focus-within:ring-4 focus-within:ring-indigo-100">
        <div className="flex items-center justify-between border-b border-white/10 bg-slate-950/80 px-4 py-2.5">
          <div className="flex items-center gap-2 text-[11px] font-medium text-slate-300">
            <span className="h-2.5 w-2.5 rounded-full bg-rose-400" />
            <span className="h-2.5 w-2.5 rounded-full bg-amber-300" />
            <span className="h-2.5 w-2.5 rounded-full bg-emerald-400" />
            <span className="ml-1 font-mono text-slate-400">
              solution.{activeLanguage.extension}
            </span>
          </div>
          <span className="font-mono text-[10px] text-slate-500">
            {activeLanguage.runtime}
          </span>
        </div>
        <textarea
          ref={answerRef}
          id="interview-answer"
          value={value}
          onChange={(event) => {
            onChange(event.target.value);
            setResults(null);
          }}
          onKeyDown={(event) => {
            if (event.shiftKey && event.altKey && event.key.toLowerCase() === "f") {
              event.preventDefault();
              void formatAnswer();
              return;
            }
            if (event.key === "Tab") {
              event.preventDefault();
              const textarea = event.currentTarget;
              const start = textarea.selectionStart;
              const end = textarea.selectionEnd;
              const next = `${value.slice(0, start)}  ${value.slice(end)}`;
              onChange(next);
              window.requestAnimationFrame(() => {
                textarea.selectionStart = start + 2;
                textarea.selectionEnd = start + 2;
              });
            }
            if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
              event.preventDefault();
              onSubmit();
            }
          }}
          spellCheck={false}
          rows={13}
          aria-label={`${question.title} JavaScript 解答`}
          className="w-full resize-y bg-transparent p-4 font-mono text-[13px] leading-6 text-slate-100 outline-none placeholder:text-slate-600 md:p-5"
        />
        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-white/10 bg-slate-950/70 px-3 py-2.5">
          <span className="text-[10px] text-slate-500">
            {language === "python"
              ? "Python 支持编辑、粘贴、格式化与 AI 评分；本地示例运行暂未开放"
              : "示例在浏览器隔离线程中运行，超时 2 秒自动停止"}
          </span>
          <button
            type="button"
            disabled={running || !value.trim() || language === "python"}
            onClick={runExamples}
            className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-400 px-3 py-1.5 text-xs font-semibold text-slate-950 transition hover:bg-indigo-300 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {running ? (
              <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Play className="h-3.5 w-3.5 fill-current" />
            )}
            {language === "python" ? "Python 暂不运行" : "运行示例"}
          </button>
        </div>
      </div>
      {results && (
        <div
          className={`mt-3 rounded-xl border px-3.5 py-3 ${
            passedCount === results.length
              ? "border-emerald-200 bg-emerald-50"
              : "border-amber-200 bg-amber-50"
          }`}
          aria-live="polite"
        >
          <div className="flex items-center justify-between text-xs font-semibold">
            <span className={passedCount === results.length ? "text-emerald-800" : "text-amber-800"}>
              示例测试 {passedCount} / {results.length} 通过
            </span>
            <span className="text-[10px] font-normal text-slate-500">
              示例通过不代表所有隐藏用例通过
            </span>
          </div>
          <div className="mt-2 grid gap-1.5 sm:grid-cols-2">
            {results.map((result, index) => (
              <div
                key={index}
                className="flex min-w-0 items-start gap-2 rounded-lg bg-white/70 px-2.5 py-2 text-[11px]"
              >
                {result.passed ? (
                  <CircleCheck className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-600" />
                ) : (
                  <CircleX className="mt-0.5 h-3.5 w-3.5 shrink-0 text-rose-600" />
                )}
                <span className="min-w-0 text-slate-600">
                  <span className="font-semibold text-slate-800">用例 {index + 1}</span>
                  {result.error ? (
                    <span className="ml-1 break-words text-rose-700">{result.error}</span>
                  ) : !result.passed ? (
                    <span className="ml-1 break-words">
                      得到 {JSON.stringify(result.actual)}，期望 {JSON.stringify(result.expected)}
                    </span>
                  ) : (
                    <span className="ml-1">通过</span>
                  )}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function ExitConfirmDialog({
  loading,
  onCancel,
  onConfirm,
}: {
  loading: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="exit-interview-title"
      className="absolute inset-0 z-50 grid place-items-center bg-slate-950/50 p-5 backdrop-blur-sm"
    >
      <div className="w-full max-w-md rounded-[28px] border border-white/70 bg-white p-6 shadow-2xl md:p-7">
        <div className="flex items-start gap-4">
          <div className="grid h-11 w-11 shrink-0 place-items-center rounded-2xl bg-rose-50 text-rose-700">
            <LogOut className="h-5 w-5" />
          </div>
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-rose-600">
              Leave interview
            </p>
            <h2 id="exit-interview-title" className="mt-1 text-xl font-semibold text-slate-950">
              确认退出本场面试？
            </h2>
            <p className="mt-2 text-sm leading-6 text-slate-600">
              退出后本场不会评分，也不会计入面试画像或岗位匹配记录。若只是暂时离开，请取消后点击右上角关闭按钮。
            </p>
          </div>
        </div>
        <div className="mt-6 flex gap-3">
          <button
            type="button"
            disabled={loading}
            onClick={onCancel}
            className="flex-1 rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-50"
          >
            继续面试
          </button>
          <button
            type="button"
            disabled={loading}
            onClick={onConfirm}
            className="inline-flex flex-1 items-center justify-center gap-2 rounded-xl bg-rose-600 px-4 py-3 text-sm font-semibold text-white hover:bg-rose-700 disabled:opacity-50"
          >
            {loading && <LoaderCircle className="h-4 w-4 animate-spin" />}
            确认退出
          </button>
        </div>
      </div>
    </div>
  );
}

function ReviewRoom({
  interview,
  expandedQuestion,
  setExpandedQuestion,
  onViewSources,
  onOpenHistory,
  onReset,
  onClose,
}: {
  interview: InterviewSession;
  expandedQuestion: string | null;
  setExpandedQuestion: (value: string | null) => void;
  onViewSources: (sources: SourceRef[]) => void;
  onOpenHistory: () => void;
  onReset: () => void;
  onClose: () => void;
}) {
  const roleFitLabels: Record<string, string> = {
    skillCoverage: "硬技能覆盖",
    projectEvidence: "项目证据",
    responsibilityMatch: "职责匹配",
    levelMatch: "难度匹配",
  };
  return (
    <div className="relative z-10 min-h-0 flex-1 overflow-y-auto">
      <RoomHeader
        eyebrow="Interview debrief"
        title="面试复盘室"
        detail={`${interview.targetRole} · ${interview.kbName}`}
        onClose={onClose}
      />
      <div className="mx-auto max-w-6xl space-y-6 px-5 py-7 md:px-8 md:py-10">
        <div className="grid gap-5 lg:grid-cols-[1.05fr_0.95fr]">
          <InterviewRadar
            dimensions={interview.scoreDimensions || []}
            scores={interview.dimensionScores || {}}
            overallScore={interview.overallScore}
            delta={interview.profileDelta || 0}
          />
          <div className="rounded-3xl border border-slate-200 bg-slate-950 p-6 text-white shadow-lg">
            <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-cyan-300">
              Role fit
            </p>
            <h3 className="mt-1 text-lg font-semibold">岗位匹配拆解</h3>
            <p className="mt-2 text-sm leading-6 text-slate-300">{interview.summary}</p>
            <div className="mt-6 space-y-4">
              {Object.entries(interview.roleFitBreakdown || {}).map(([key, score]) => (
                <div key={key}>
                  <div className="mb-1.5 flex justify-between text-xs">
                    <span className="text-slate-300">{roleFitLabels[key] || key}</span>
                    <span className="font-mono font-semibold text-white">{score.toFixed(1)}</span>
                  </div>
                  <div className="h-1.5 overflow-hidden rounded-full bg-white/10">
                    <div
                      className="h-full rounded-full bg-gradient-to-r from-cyan-400 to-blue-400"
                      style={{ width: `${score * 10}%` }}
                    />
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>

        <section className="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm md:p-7">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-cyan-700">
                Question review
              </p>
              <h3 className="mt-1 text-xl font-semibold text-slate-950">逐题复盘</h3>
            </div>
            <p className="text-xs text-slate-500">点击来源可定位到知识库文档与 PDF 页码</p>
          </div>
          <div className="mt-5 divide-y divide-slate-100">
            {interview.questions.map((question) => {
              const open = expandedQuestion === question.id;
              const meta = categoryMeta[question.category];
              return (
                <article key={question.id} className="py-3">
                  <button
                    type="button"
                    onClick={() => setExpandedQuestion(open ? null : question.id)}
                    className="flex w-full items-start gap-3 rounded-xl px-2 py-2 text-left hover:bg-slate-50"
                  >
                    <span className="mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-slate-100 font-mono text-xs font-semibold text-slate-600">
                      {question.sequence}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="flex flex-wrap items-center gap-2">
                        <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ring-1 ${meta.className}`}>
                          {meta.label}
                        </span>
                        <span className="font-mono text-sm font-semibold text-slate-900">
                          {(question.score || 0).toFixed(1)} / 10
                        </span>
                      </span>
                      <span className="mt-1.5 block line-clamp-2 text-sm leading-6 text-slate-700">
                        {question.question}
                      </span>
                    </span>
                    <ChevronDown className={`mt-2 h-4 w-4 shrink-0 text-slate-400 transition ${open ? "rotate-180" : ""}`} />
                  </button>
                  {open && (
                    <div className="ml-0 mt-2 grid gap-4 rounded-2xl bg-slate-50 p-4 md:ml-11 md:grid-cols-2 md:p-5">
                      <ReviewBlock
                        title={
                          question.category === "algorithm"
                            ? `你的回答 · ${
                                algorithmLanguageOptions.find(
                                  (item) => item.key === question.answerLanguage,
                                )?.label || "JavaScript"
                              }`
                            : "你的回答"
                        }
                      >
                        <p className={`whitespace-pre-wrap ${
                          question.category === "algorithm"
                            ? "font-mono text-[12px] leading-6"
                            : ""
                        }`}>
                          {question.skipped ? "本题已跳过" : question.answer || "未作答"}
                        </p>
                      </ReviewBlock>
                      <ReviewBlock title="面试官分析">
                        <p>{question.analysis}</p>
                      </ReviewBlock>
                      <ReviewBlock title="改进建议">
                        <ul className="space-y-1.5">
                          {(question.improvements || []).map((item) => (
                            <li key={item} className="flex gap-2">
                              <span className="mt-2 h-1 w-1 shrink-0 rounded-full bg-amber-500" />
                              <span>{item}</span>
                            </li>
                          ))}
                        </ul>
                      </ReviewBlock>
                      <ReviewBlock title="参考要点">
                        <ul className="space-y-1.5">
                          {(question.referencePoints || []).map((item) => (
                            <li key={item} className="flex gap-2">
                              <Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-600" />
                              <span>{item}</span>
                            </li>
                          ))}
                        </ul>
                      </ReviewBlock>
                      <div className="md:col-span-2">
                        {question.source ? (
                          <button
                            type="button"
                            onClick={() => onViewSources([question.source!])}
                            className="flex w-full items-center gap-3 rounded-xl border border-cyan-200 bg-cyan-50 px-4 py-3 text-left transition hover:border-cyan-300 hover:bg-cyan-100/60"
                          >
                            <BookOpen className="h-4 w-4 shrink-0 text-cyan-700" />
                            <span className="min-w-0 flex-1">
                              <span className="block truncate text-xs font-semibold text-cyan-900">
                                {question.source.docName}
                              </span>
                              <span className="mt-0.5 block text-[11px] text-cyan-700">
                                {question.source.kbName || interview.kbName}
                                {question.source.pageStart ? ` · 第 ${question.source.pageStart} 页` : ""}
                              </span>
                            </span>
                            <span className="text-xs font-medium text-cyan-700">查看来源</span>
                          </button>
                        ) : question.category === "algorithm" ? (
                          <div className="flex items-center gap-2 rounded-xl border border-indigo-200 bg-indigo-50 px-4 py-3 text-xs text-indigo-700">
                            <Code2 className="h-4 w-4 shrink-0" />
                            LeetCode Hot 100 算法题 · AI 已结合正确性、复杂度与边界条件评分
                          </div>
                        ) : (
                          <div className="rounded-xl border border-dashed border-slate-300 px-4 py-3 text-xs text-slate-500">
                            Agent 自拟题：本题没有可用知识库来源，系统未伪造引用。
                          </div>
                        )}
                      </div>
                    </div>
                  )}
                </article>
              );
            })}
          </div>
        </section>

        <div className="flex flex-wrap justify-end gap-3 pb-4">
          <button
            type="button"
            onClick={onOpenHistory}
            className="inline-flex items-center gap-2 rounded-xl border border-cyan-200 bg-cyan-50 px-4 py-2.5 text-sm font-semibold text-cyan-800 hover:bg-cyan-100"
          >
            <HistoryIcon className="h-4 w-4" />
            历史回溯
          </button>
          <button
            type="button"
            onClick={onClose}
            className="rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-medium text-slate-600 hover:bg-slate-50"
          >
            返回对话
          </button>
          <button
            type="button"
            onClick={onReset}
            className="inline-flex items-center gap-2 rounded-xl bg-slate-950 px-4 py-2.5 text-sm font-semibold text-white hover:bg-slate-800"
          >
            <RotateCcw className="h-4 w-4" /> 再模拟一次
          </button>
        </div>
      </div>
    </div>
  );
}

function RoomHeader({
  eyebrow,
  title,
  detail,
  onClose,
}: {
  eyebrow: string;
  title: string;
  detail: string;
  onClose: () => void;
}) {
  return (
    <header className="sticky top-0 z-20 border-b border-slate-200 bg-white/90 px-5 py-4 backdrop-blur md:px-8">
      <div className="mx-auto flex max-w-6xl items-center gap-3">
        <div className="grid h-10 w-10 place-items-center rounded-2xl bg-slate-950 text-cyan-300">
          <Sparkles className="h-5 w-5" />
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-cyan-700">{eyebrow}</p>
          <div className="flex flex-wrap items-baseline gap-x-3">
            <h2 className="text-lg font-semibold text-slate-950">{title}</h2>
            <p className="truncate text-xs text-slate-500">{detail}</p>
          </div>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="inline-flex items-center gap-2 rounded-xl px-3 py-2 text-xs font-medium text-slate-500 hover:bg-slate-100 hover:text-slate-800"
        >
          <ArrowLeft className="h-4 w-4" /> 返回对话
        </button>
      </div>
    </header>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-2 flex items-center justify-between gap-3">
        <span className="text-sm font-semibold text-slate-800">{label}</span>
        <span className="text-[11px] text-slate-400">{hint}</span>
      </span>
      {children}
    </label>
  );
}

function MaterialCard({
  icon,
  title,
  detail,
  state,
  accept,
  loading,
  onFile,
  onClear,
  secondaryAction,
}: {
  icon: React.ReactNode;
  title: string;
  detail: string;
  state: InterviewMaterials["resume"];
  accept: string;
  loading: boolean;
  onFile: (file?: File) => void;
  onClear: () => void;
  secondaryAction?: React.ReactNode;
}) {
  const inputId = `interview-material-${title}`;
  const hashPreview = state.contentHash?.slice(0, 10);
  const [confirmingClear, setConfirmingClear] = useState(false);
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex items-start gap-3">
        <div className={`grid h-10 w-10 shrink-0 place-items-center rounded-xl ${state.uploaded ? "bg-emerald-50 text-emerald-600" : "bg-slate-100 text-slate-500"}`}>
          {loading ? <LoaderCircle className="h-5 w-5 animate-spin" /> : state.uploaded ? <Check className="h-5 w-5" /> : icon}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-2">
            <h3 className="text-sm font-semibold text-slate-900">{title}</h3>
            {secondaryAction}
          </div>
          <p className="mt-0.5 text-xs leading-5 text-slate-500">{detail}</p>
          {state.uploaded && (
            <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px]">
              <span className="max-w-[230px] truncate rounded-md bg-slate-100 px-2 py-1 text-slate-600">{state.fileName}</span>
              {hashPreview && <span className="font-mono text-slate-400">sha256:{hashPreview}</span>}
              {state.changed === false && <span className="text-amber-600">内容未变化</span>}
              {state.changed === true && <span className="text-emerald-600">已更新</span>}
            </div>
          )}
        </div>
        {state.uploaded && (
          <button
            type="button"
            disabled={loading}
            onClick={() => {
              if (confirmingClear) {
                setConfirmingClear(false);
                onClear();
              } else {
                setConfirmingClear(true);
              }
            }}
            aria-label={confirmingClear ? `确认清空${title}` : `清空${title}`}
            className={`inline-flex items-center gap-1.5 rounded-lg border px-3 py-2 text-xs font-medium transition disabled:cursor-wait disabled:opacity-50 ${
              confirmingClear
                ? "border-rose-600 bg-rose-600 text-white hover:bg-rose-700"
                : "border-rose-200 text-rose-600 hover:bg-rose-50"
            }`}
          >
            <Trash2 className="h-3.5 w-3.5" />
            {confirmingClear ? "确认清空" : "清空"}
          </button>
        )}
        <label
          htmlFor={inputId}
          className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-2 text-xs font-medium text-slate-600 hover:bg-slate-50"
          onClick={() => setConfirmingClear(false)}
        >
          <Upload className="h-3.5 w-3.5" />
          {state.uploaded ? "更新" : "上传"}
        </label>
        <input
          id={inputId}
          type="file"
          accept={accept}
          className="sr-only"
          disabled={loading}
          onChange={(event) => {
            onFile(event.target.files?.[0]);
            event.target.value = "";
          }}
        />
      </div>
    </div>
  );
}

function ReviewBlock({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <h4 className="mb-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500">{title}</h4>
      <div className="text-xs leading-6 text-slate-600">{children}</div>
    </div>
  );
}
