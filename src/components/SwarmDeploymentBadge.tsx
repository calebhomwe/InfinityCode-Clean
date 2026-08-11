import type { Effort } from "../lib/composer";
import { effortMeta } from "../lib/composer";

export interface ModelInfo {
  id: string;
  label: string;
  shortLabel: string;
  tier: "local" | "free" | "standard" | "premium";
  isSwarm?: boolean;
}

// Maps model IDs to display info with agent counts
export const MODEL_INFO_MAP: Record<string, ModelInfo> = {
  "local/fable-max-35b": {
    id: "local/fable-max-35b",
    label: "Fable 35B",
    shortLabel: "35B",
    tier: "local",
  },
  "local/fable-fusion-27b": {
    id: "local/fable-fusion-27b",
    label: "Fable Fusion 27B",
    shortLabel: "27B",
    tier: "local",
  },
  "local/infinity-ai": {
    id: "local/infinity-ai",
    label: "Fable 35B (Local)",
    shortLabel: "35B",
    tier: "local",
  },
  "local/infinity-ai-fast": {
    id: "local/infinity-ai-fast",
    label: "Fable 9B (Local)",
    shortLabel: "9B",
    tier: "local",
  },
  "moonshotai/kimi-k3": {
    id: "moonshotai/kimi-k3",
    label: "Kimi K3",
    shortLabel: "K3",
    tier: "premium",
  },
  "moonshotai/kimi-k2.7-code": {
    id: "moonshotai/kimi-k2.7-code",
    label: "Kimi K2.7 Code",
    shortLabel: "K2.7",
    tier: "premium",
  },
  "moonshotai/kimi-k2.6": {
    id: "moonshotai/kimi-k2.6",
    label: "Kimi K2.6",
    shortLabel: "2.6",
    tier: "premium",
  },
  "qwen/qwen3.7-max": {
    id: "qwen/qwen3.7-max",
    label: "Qwen 3.7 Max",
    shortLabel: "Q3.7",
    tier: "premium",
  },
  "qwen/qwen3-coder": {
    id: "qwen/qwen3-coder",
    label: "Qwen3 Coder",
    shortLabel: "Q3C",
    tier: "standard",
  },
  "qwen/qwen3-vl-30b-a3b-thinking": {
    id: "qwen/qwen3-vl-30b-a3b-thinking",
    label: "Qwen3 VL",
    shortLabel: "QVL",
    tier: "standard",
  },
  "z-ai/glm-5.2": {
    id: "z-ai/glm-5.2",
    label: "GLM 5.2",
    shortLabel: "GLM",
    tier: "premium",
  },
  "z-ai/glm-5-flash": {
    id: "z-ai/glm-5-flash",
    label: "GLM 5 Flash",
    shortLabel: "G5F",
    tier: "free",
  },
  "minimax/minimax-m3": {
    id: "minimax/minimax-m3",
    label: "MiniMax M3",
    shortLabel: "M3",
    tier: "standard",
  },
  "google/gemini-3.1-flash-lite": {
    id: "google/gemini-3.1-flash-lite",
    label: "Gemini Flash Lite",
    shortLabel: "Gem-L",
    tier: "free",
  },
  "deepseek/deepseek-v4-flash": {
    id: "deepseek/deepseek-v4-flash",
    label: "DeepSeek V4 Flash",
    shortLabel: "DS Flash",
    tier: "free",
  },
};

/** Resolve any backend model id to display info — including the raw
 *  "local:<server id>" strings llama.cpp / LM Studio report, and unknown
 *  cloud slugs (shown as their slug tail rather than a blank). */
export function modelInfoFor(modelId: string | null | undefined): ModelInfo | undefined {
  if (!modelId) return undefined;
  const direct = MODEL_INFO_MAP[modelId];
  if (direct) return direct;
  if (modelId.startsWith("local:")) {
    const raw = modelId.slice("local:".length).toLowerCase();
    if (raw.includes("35b") || raw.includes("fable-max")) return MODEL_INFO_MAP["local/fable-max-35b"];
    if (raw.includes("27b") || raw.includes("fusion")) return MODEL_INFO_MAP["local/fable-fusion-27b"];
    if (raw.includes("9b") || raw.includes("fable-fast") || raw.includes("2b")) return MODEL_INFO_MAP["local/infinity-ai-fast"];
    return { id: modelId, label: "Local model", shortLabel: "Local", tier: "local" };
  }
  const tail = modelId.split("/").pop() ?? modelId;
  return { id: modelId, label: tail, shortLabel: tail.slice(0, 10), tier: "standard" };
}

interface SwarmDeploymentBadgeProps {
  modelId?: string;
  effort?: Effort;
  fast?: boolean;
  showFullLabel?: boolean;
  size?: "sm" | "md" | "lg";
  pulse?: boolean;
}

export function SwarmDeploymentBadge({
  modelId,
  effort = "med",
  fast = false,
  showFullLabel = false,
  size = "md",
  pulse = false,
}: SwarmDeploymentBadgeProps): JSX.Element {
  const effortMetaData = effortMeta(effort);
  const agentCount = effortMetaData.agents;
  const isSwarm = agentCount > 1;
  
  const modelInfo = modelId ? MODEL_INFO_MAP[modelId] : undefined;
  const modelLabel = modelInfo
    ? (showFullLabel ? modelInfo.label : modelInfo.shortLabel)
    : "Agent";
  // Size classes
  const sizeClasses = {
    sm: "text-[9px] px-1 py-0.5 gap-0.5",
    md: "text-[10px] px-1.5 py-0.5 gap-1",
    lg: "text-xs px-2 py-1 gap-1.5",
  };
  
  const iconSize = {
    sm: "w-2.5 h-2.5",
    md: "w-3 h-3",
    lg: "w-3.5 h-3.5",
  };
  
  return (
    <span
      className={`inline-flex items-center font-medium rounded ${sizeClasses[size]} ${
        pulse ? "animate-pulse" : ""
      } ${
        modelInfo?.tier === "local"
          ? "bg-gradient-to-r from-success/20 to-info/20 text-success border border-success/30"
          : modelInfo?.tier === "premium"
          ? "bg-gradient-to-r from-warning/20 to-warning/10 text-warning border border-warning/30"
          : "bg-surface border border-bd/[0.1] text-tx-dim"
      }`}
      title={`${isSwarm ? "Swarm" : "Agent"}: ${modelLabel} × ${agentCount}`}
    >
      {/* Swarm or Single Agent Icon */}
      {isSwarm ? (
        <svg
          className={`${iconSize[size]} flex-none`}
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
        >
          <circle cx="12" cy="5" r="2" />
          <circle cx="5" cy="12" r="2" />
          <circle cx="19" cy="12" r="2" />
          <path d="M12 7v3l-5 3" />
          <path d="M12 10l5 3" />
          <path d="M7 14l-2 5" />
          <path d="M17 14l2 5" />
        </svg>
      ) : (
        <svg
          className={`${iconSize[size]} flex-none`}
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
        >
          <circle cx="12" cy="8" r="3" />
          <path d="M4 20c0-4 4-6 8-6s8 2 8 6" />
        </svg>
      )}
      
      {/* Model Label + Count */}
      <span className="whitespace-nowrap">
        {fast ? "Fast" : modelLabel} × {agentCount}
      </span>
      
      {/* Fast mode indicator */}
      {fast && (
        <span className="text-[8px] uppercase tracking-wider px-0.5 rounded bg-accent/30 text-accent font-bold">
          turbo
        </span>
      )}
      
      {/* Swarm indicator for high effort */}
      {isSwarm && !fast && (
        <span
          className={`text-[8px] uppercase tracking-wider px-0.5 rounded ${
            effort === "ultracode"
              ? "bg-accent/30 text-accent font-bold"
              : effort === "max"
              ? "bg-warning/30 text-warning"
              : "bg-tx-mut/20 text-tx-mut"
          }`}
        >
          {effort === "ultracode" ? "max" : "swarm"}
        </span>
      )}
    </span>
  );
}

// Shows a summary of what's running
export function SwarmDeploymentSummary({
  modelId,
  effort,
}: {
  modelId?: string;
  effort?: Effort;
}): JSX.Element {
  const effortMetaData = effortMeta(effort ?? "med");
  const agentCount = effortMetaData.agents;
  const isSwarm = agentCount > 1;
  const modelInfo = modelId ? MODEL_INFO_MAP[modelId] : undefined;
  
  return (
    <div className="flex items-center gap-2 text-xs">
      <div className={`flex items-center gap-1.5 ${isSwarm ? "text-accent" : "text-tx-dim"}`}>
        {isSwarm ? (
          <>
            <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <circle cx="12" cy="5" r="2" />
              <circle cx="5" cy="12" r="2" />
              <circle cx="19" cy="12" r="2" />
              <path d="M12 7v3l-5 3" />
              <path d="M12 10l5 3" />
              <path d="M7 14l-2 5" />
              <path d="M17 14l2 5" />
            </svg>
            <span className="font-medium">
              {agentCount} agents deploying
            </span>
          </>
        ) : (
          <>
            <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <circle cx="12" cy="8" r="3" />
              <path d="M4 20c0-4 4-6 8-6s8 2 8 6" />
            </svg>
            <span>1 agent running</span>
          </>
        )}
      </div>
      
      {modelInfo && (
        <div className="flex items-center gap-1">
          <span className="text-tx-mut">·</span>
          <span
            className={`font-medium ${
              modelInfo.tier === "local"
                ? "text-success"
                : modelInfo.tier === "premium"
                ? "text-warning"
                : "text-tx-dim"
            }`}
          >
            {modelInfo.label}
          </span>
        </div>
      )}
    </div>
  );
}

export default SwarmDeploymentBadge;
