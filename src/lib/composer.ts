/**
 * Shared composer contract. The backend (`POST /api/v1/missions`) accepts
 * exactly these string values; the frontend composer and App integration both
 * import from here so their payloads cannot drift.
 */

export type Mode = "auto" | "code" | "image" | "video" | "3d";
export type Effort = "low" | "med" | "high" | "xhigh" | "max" | "ultracode" | "vibe";
export type ToolId = "run_code" | "image_gen" | "render_3d" | "learn";

export interface Attachment {
  path: string;
  name: string;
  size: number;
}

/** The single payload the composer emits on submit. */
export interface ComposerSubmission {
  title: string;
  goal: string;
  referenceImagePath: string;
  /** Optional final frame used by video interpolation. */
  endReferenceImagePath?: string;
  mode: Mode;
  effort: Effort;
  fast: boolean;
  vision_loop: boolean;
  speculative: boolean;
  attachments: Attachment[];
  tools: ToolId[];
  agents: string[];
  combine_with_default_swarm: boolean;
}

export interface ModeMeta {
  id: Mode;
  label: string;
  hint: string;
}

export const MODES: ModeMeta[] = [
  { id: "auto", label: "Auto", hint: "Route to the best agent automatically" },
  { id: "code", label: "Code", hint: "Write and run code" },
  { id: "image", label: "Image", hint: "Generate an image" },
  { id: "video", label: "Video", hint: "Animate between reference frames" },
  { id: "3d", label: "3D", hint: "Render a 3D scene in Blender" },
];

export interface EffortMeta {
  id: Effort;
  label: string;
  hint: string;
  /** Conceptual agent count shown as a badge. */
  agents: number;
  /** ultracode gets the lightning treatment. */
  bolt?: boolean;
}

export const EFFORTS: EffortMeta[] = [
  { id: "low", label: "Low", hint: "Fastest, a single pass", agents: 1 },
  { id: "med", label: "Medium", hint: "Balanced, a couple of tries", agents: 2 },
  { id: "high", label: "High", hint: "Thorough, iterates on feedback", agents: 3 },
  { id: "xhigh", label: "Extra high", hint: "Deep, uses the best model", agents: 4 },
  { id: "max", label: "Max", hint: "Exhaustive, five attempts", agents: 5 },
  {
    id: "ultracode",
    label: "Infinity Code",
    hint: "Maximum swarm, eight agents",
    agents: 8,
    bolt: true,
  },
  {
    id: "vibe",
    label: "Vibe Coder",
    hint: "Your 35B + Kimi swarm & GLM-5.2; cloud-only if the local server is off",
    agents: 4,
  },
];

export interface ToolMeta {
  id: ToolId;
  label: string;
}

export const TOOLS: ToolMeta[] = [
  { id: "run_code", label: "Code execution" },
  { id: "image_gen", label: "Image generation" },
  { id: "render_3d", label: "3D render" },
  { id: "learn", label: "Tutorial ingest" },
];

export const DEFAULT_MODE: Mode = "auto";
export const DEFAULT_EFFORT: Effort = "med";
export const DEFAULT_TOOLS: ToolId[] = ["run_code", "image_gen"];

export function effortMeta(id: Effort): EffortMeta {
  return EFFORTS.find((e) => e.id === id) ?? EFFORTS[1];
}

export function modeMeta(id: Mode): ModeMeta {
  return MODES.find((m) => m.id === id) ?? MODES[0];
}
