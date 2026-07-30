import { ArrowDownRight, ArrowRight, ArrowUpRight } from "lucide-react";
import { radarPolygonPoints } from "./interviewUtils";
import type { ScoreDimension } from "./types";

interface Props {
  dimensions: ScoreDimension[];
  scores: Record<string, number>;
  overallScore?: number;
  delta?: number;
  compact?: boolean;
}

const CENTER_X = 160;
const CENTER_Y = 136;
const RADIUS = 92;

export default function InterviewRadar({
  dimensions,
  scores,
  overallScore,
  delta = 0,
  compact = false,
}: Props) {
  const visibleDimensions = dimensions.slice(0, 6);
  const values = visibleDimensions.map((dimension) => scores[dimension.key] || 0);
  const polygon = radarPolygonPoints(values, CENTER_X, CENTER_Y, RADIUS);
  const trend = delta >= 0.3 ? "up" : delta <= -0.3 ? "down" : "stable";

  return (
    <div className={compact ? "" : "rounded-3xl border border-slate-200 bg-white p-5 shadow-sm"}>
      {!compact && (
        <div className="flex items-start justify-between">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-cyan-700">
              Interview capability
            </p>
            <h3 className="mt-1 text-lg font-semibold text-slate-950">面试能力画像</h3>
          </div>
          {overallScore != null && (
            <div className="text-right">
              <p className="font-mono text-3xl font-semibold tracking-tight text-slate-950">
                {overallScore.toFixed(1)}
              </p>
              <TrendBadge trend={trend} delta={delta} />
            </div>
          )}
        </div>
      )}

      <div className="mx-auto mt-2 max-w-[360px]">
        <svg
          viewBox="0 0 320 280"
          role="img"
          aria-label="面试六维能力雷达图"
          className="h-auto w-full overflow-visible"
        >
          <defs>
            <linearGradient id="interview-radar-fill" x1="0" y1="0" x2="1" y2="1">
              <stop offset="0%" stopColor="#06b6d4" stopOpacity="0.42" />
              <stop offset="100%" stopColor="#2563eb" stopOpacity="0.18" />
            </linearGradient>
          </defs>
          {[2, 4, 6, 8, 10].map((level) => (
            <polygon
              key={level}
              points={radarPolygonPoints(
                visibleDimensions.map(() => level),
                CENTER_X,
                CENTER_Y,
                RADIUS,
              )}
              fill="none"
              stroke={level === 10 ? "#cbd5e1" : "#e2e8f0"}
              strokeWidth="1"
            />
          ))}
          {visibleDimensions.map((dimension, index) => {
            const angle = -Math.PI / 2 + (Math.PI * 2 * index) / visibleDimensions.length;
            const x = CENTER_X + Math.cos(angle) * RADIUS;
            const y = CENTER_Y + Math.sin(angle) * RADIUS;
            const labelX = CENTER_X + Math.cos(angle) * (RADIUS + 28);
            const labelY = CENTER_Y + Math.sin(angle) * (RADIUS + 28);
            return (
              <g key={dimension.key}>
                <line
                  x1={CENTER_X}
                  y1={CENTER_Y}
                  x2={x}
                  y2={y}
                  stroke="#dbe3ee"
                  strokeWidth="1"
                />
                <text
                  x={labelX}
                  y={labelY}
                  textAnchor={Math.abs(labelX - CENTER_X) < 8 ? "middle" : labelX > CENTER_X ? "start" : "end"}
                  dominantBaseline="middle"
                  className="fill-slate-500 text-[10px] font-medium"
                >
                  {dimension.label}
                </text>
              </g>
            );
          })}
          <polygon
            points={polygon}
            fill="url(#interview-radar-fill)"
            stroke="#0284c7"
            strokeWidth="2.5"
            strokeLinejoin="round"
          />
          {polygon.split(" ").map((point, index) => {
            const [cx, cy] = point.split(",").map(Number);
            return (
              <circle
                key={visibleDimensions[index]?.key}
                cx={cx}
                cy={cy}
                r="4"
                fill="#fff"
                stroke="#0284c7"
                strokeWidth="2.5"
              />
            );
          })}
        </svg>
      </div>
    </div>
  );
}

function TrendBadge({
  trend,
  delta,
}: {
  trend: "up" | "down" | "stable";
  delta: number;
}) {
  if (trend === "up") {
    return (
      <span className="mt-1 inline-flex items-center gap-1 text-xs font-medium text-emerald-600">
        <ArrowUpRight className="h-3.5 w-3.5" />
        较上次 +{delta.toFixed(1)}
      </span>
    );
  }
  if (trend === "down") {
    return (
      <span className="mt-1 inline-flex items-center gap-1 text-xs font-medium text-rose-600">
        <ArrowDownRight className="h-3.5 w-3.5" />
        较上次 {delta.toFixed(1)}
      </span>
    );
  }
  return (
    <span className="mt-1 inline-flex items-center gap-1 text-xs font-medium text-slate-500">
      <ArrowRight className="h-3.5 w-3.5" />
      与上次持平
    </span>
  );
}

