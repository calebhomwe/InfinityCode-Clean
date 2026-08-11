import { useEffect, useRef, useState } from "react";
import { useGSAP } from "@gsap/react";
import gsap from "gsap";
import Markdown from "react-markdown";
import { PrismLight as SyntaxHighlighter } from "react-syntax-highlighter";
import tsx from "react-syntax-highlighter/dist/esm/languages/prism/tsx";
import typescript from "react-syntax-highlighter/dist/esm/languages/prism/typescript";
import javascript from "react-syntax-highlighter/dist/esm/languages/prism/javascript";
import jsx from "react-syntax-highlighter/dist/esm/languages/prism/jsx";
import python from "react-syntax-highlighter/dist/esm/languages/prism/python";
import json from "react-syntax-highlighter/dist/esm/languages/prism/json";
import bash from "react-syntax-highlighter/dist/esm/languages/prism/bash";
import css from "react-syntax-highlighter/dist/esm/languages/prism/css";
import markup from "react-syntax-highlighter/dist/esm/languages/prism/markup";
import rust from "react-syntax-highlighter/dist/esm/languages/prism/rust";
import sql from "react-syntax-highlighter/dist/esm/languages/prism/sql";
import yaml from "react-syntax-highlighter/dist/esm/languages/prism/yaml";
import {
  fetchChatModels,
  type ChatMessage,
  type ChatModel,
  type ToolInvocation,
} from "../hooks/useChats";
import { useWorkspace } from "../hooks/useWorkspace";
import { extractArtifact, type Artifact } from "../lib/artifact";
import AgentPicker, { type AgentLite } from "./AgentPicker";

/**
 * Client-side mirror of the backend demand dispatcher: labels the auto
 * team for the current draft so the composer shows what will deploy.
 * The backend dispatcher remains the source of truth for missions.
 */
function teamForPrompt(text: string): string {
  const t = text.toLowerCase();
  if (/(research|design|spec|architecture|plan|brainstorm|ux)/.test(t))
    return "Research + design (scout + designer + critic)";
  if (/(test|verify|qa|debug|check|benchmark)/.test(t))
    return "Testing (tester + fixer)";
  if (/(image|picture|art|video|3d asset|icon|logo|screenshot)/.test(t))
    return "Media (artist + eye)";
  if (/(build a whole|full game|long mission|multi-step|entire app|project)/.test(t))
    return "Long-horizon mission (builder + reviewer)";
  return "Coding (coder + verifier + reviewer)";
}
import heroLogo from "../assets/logo.webp";
import ApprovalModePicker, { isApprovalMode, type ApprovalMode } from "./ApprovalModePicker";
import DiffModal from "./DiffModal";
import InfinityMark from "./InfinityMark";
import { apiFetch, API_BASE  } from "../lib/api";
import { playSound } from "../lib/feedback";
import {
  buildGreeting,
  getDaypart,
  getSuggestions,
  usePersonality,
} from "../features/personality";

gsap.registerPlugin(useGSAP);

SyntaxHighlighter.registerLanguage("tsx", tsx);
SyntaxHighlighter.registerLanguage("typescript", typescript);
SyntaxHighlighter.registerLanguage("ts", typescript);
SyntaxHighlighter.registerLanguage("javascript", javascript);
SyntaxHighlighter.registerLanguage("js", javascript);
SyntaxHighlighter.registerLanguage("jsx", jsx);
SyntaxHighlighter.registerLanguage("python", python);
SyntaxHighlighter.registerLanguage("py", python);
SyntaxHighlighter.registerLanguage("json", json);
SyntaxHighlighter.registerLanguage("bash", bash);
SyntaxHighlighter.registerLanguage("sh", bash);
SyntaxHighlighter.registerLanguage("shell", bash);
SyntaxHighlighter.registerLanguage("css", css);
SyntaxHighlighter.registerLanguage("html", markup);
SyntaxHighlighter.registerLanguage("xml", markup);
SyntaxHighlighter.registerLanguage("svg", markup);
SyntaxHighlighter.registerLanguage("rust", rust);
SyntaxHighlighter.registerLanguage("sql", sql);
SyntaxHighlighter.registerLanguage("yaml", yaml);

const HIGHLIGHTED = new Set([
  "tsx", "typescript", "ts", "javascript", "js", "jsx", "python", "py",
  "json", "bash", "sh", "shell", "css", "html", "xml", "svg", "rust",
  "sql", "yaml",
]);

export interface ChatViewProps {
  chatId: string | null;
  /** Active tab id — changes on every tab switch so the composer resets. */
  tabKey: string | null;
  assistant?: boolean;
  onChatCreated: (id: string) => void;
  onChanged: () => void;
  onOpenArtifact: (artifact: Artifact) => void;
  onOpenToolsSettings: () => void;
}

interface ToolCatalogEntry {
  id: string;
  label: string;
  cat: string;
}

/** One MCP server, merged from GET /mcp/servers (config) + /mcp/status (live). */
interface McpServerRow {
  name: string;
  enabled: boolean;
  connected: boolean;
  detail: string;
  tools: number | null;
}

interface ToolCatalog {
  built_in: ToolCatalogEntry[];
  mcp: { server: string; tool: string }[];
  counts: { built_in: number; mcp: number; total: number };
}

const TOOL_DESCRIPTIONS: Record<string, string> = {
  run_python: "Run sandboxed Python for calculations, data, and repeatable tasks.",
  calculator: "Solve precise arithmetic without relying on model estimation.",
  web_search: "Search the live web for current facts and sources.",
  fetch_url: "Open a specific page and read its contents.",
  research: "Build a deeper, source-backed briefing across multiple pages.",
  review_screen: "Capture your primary display and inspect what is visible.",
  see_image: "Open an image and reason about its visual details.",
  critique: "Review an answer or artifact and score what needs improvement.",
  list_skills: "See the specialist skills available to Infinity Code.",
  read_skill: "Load a skill's instructions before doing specialist work.",
  read_file: "Read a file you choose from your computer.",
  list_dir: "Inspect the contents of a folder you choose.",
  write_file: "Create or update a file after you approve the action.",
  read_workspace_file: "Read a file from the active workspace/project.",
  list_workspace_dir: "List files and folders in the active workspace/project.",
  find_workspace_files: "Find project files with a safe workspace-relative glob.",
  search_workspace_text: "Search text across project files with path and line results.",
  read_workspace_files: "Read several project files together in one bounded call.",
  apply_workspace_edit: "Apply a precise edit to a workspace file after approval.",
  run_workspace_shell: "Run a shell command in the workspace directory after approval.",
  launch_app: "Launch an installed desktop application after approval.",
  generate_image: "Create an image through a configured provider after approval.",
  text_to_speech: "Turn text into audio through a configured provider after approval.",
};

const TOOL_CATEGORY_LABELS: Record<string, string> = {
  Compute: "Compute",
  Web: "Web & research",
  Vision: "Screen & vision",
  Reasoning: "Review",
  Knowledge: "Skills",
  Files: "Files",
  "Files (action)": "Files",
  Workspace: "Workspace",
  "Workspace (action)": "Workspace",
  "Desktop (action)": "Desktop",
  "Create (action)": "Create",
};

/** Turn a raw build-loop failure into human copy. The headline stays short
 *  and friendly; the raw JS error is demoted to a small detail line so
 *  "TypeError: Failed to fetch" never becomes the headline. */
function loopFailureMessage(err: unknown): string {
  if (err instanceof DOMException && err.name === "AbortError") {
    return "**Build stopped.**";
  }
  const text = err instanceof Error ? err.message : String(err);
  const raw = err instanceof Error ? `${err.name}: ${err.message}` : String(err);
  const httpStatus = /^HTTP (\d{3})/i.exec(text);
  const headline = httpStatus
    ? `The backend rejected the request (HTTP ${httpStatus[1]}).`
    : /failed to fetch|network|load failed|econnrefused|connection|unreachable|timed? ?out/i.test(text)
      ? "Lost connection to the backend — is it running?"
      : "Something went wrong while running the build loop.";
  return `**Loop failed:** ${headline}\n\n\`${raw}\``;
}

interface CodeProps {
  inline?: boolean;
  className?: string;
  children?: React.ReactNode;
}

/** Walk a React node tree and pull out its plain text (for copy). */
function extractText(node: React.ReactNode): string {
  if (node == null || node === false || node === true) {
    return "";
  }
  if (typeof node === "string" || typeof node === "number") {
    return String(node);
  }
  if (Array.isArray(node)) {
    return node.map(extractText).join("");
  }
  if (typeof node === "object" && "props" in node) {
    return extractText((node as { props?: { children?: React.ReactNode } }).props?.children);
  }
  return "";
}

/** Inline code chips; fenced blocks are framed by PreBlock below.
 *
 * react-markdown v9 dropped the `inline` prop, so detect blocks by the
 * language- class (fenced w/ tag) or embedded newlines (fenced w/o tag).
 */
function CodeBlock({ inline, className, children }: CodeProps): JSX.Element {
  const langMatch = /language-([\w+#.-]+)/.exec(className ?? "");
  const lang = langMatch ? langMatch[1].toLowerCase() : "";
  const code = extractText(children).replace(/\n+$/, "");
  const isBlock = inline === false || Boolean(langMatch) || code.includes("\n");
  if (inline === true || !isBlock) {
    return (
      <code className="inline-code font-mono text-[0.85em] bg-bd/[0.06] ring-1 ring-inset ring-bd/[0.07] rounded-[5px] px-[0.4em] py-[0.12em]">
        {children}
      </code>
    );
  }
  if (HIGHLIGHTED.has(lang)) {
    return (
      <SyntaxHighlighter
        language={lang}
        useInlineStyles={false}
        PreTag="span"
        CodeTag="code"
        codeTagProps={{
          className: `language-${lang} font-mono text-[12.5px] leading-[1.65]`,
        }}
      >
        {code}
      </SyntaxHighlighter>
    );
  }
  return (
    <code className={`${className ?? ""} font-mono text-[12.5px] leading-[1.65] text-tx`}>
      {children}
    </code>
  );
}

/** Framed fenced code block with a language label and a copy button. */
function PreBlock({
  children,
  workspacePath,
}: {
  children?: React.ReactNode;
  workspacePath: string;
}): JSX.Element {
  const [copied, setCopied] = useState(false);
  const [applyOpen, setApplyOpen] = useState(false);
  const codeEl = (Array.isArray(children) ? children[0] : children) as
    | { props?: { className?: string } }
    | undefined;
  const langMatch = /language-([\w+#.-]+)/.exec(codeEl?.props?.className ?? "");
  const lang = langMatch ? langMatch[1] : "text";
  const code = extractText(children).replace(/\n+$/, "");
  const copy = async (): Promise<void> => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1400);
    } catch {
      // Clipboard unavailable; ignore.
    }
  };
  return (
    <>
      <div className="code-card">
        <div className="code-card__bar">
          <span className="code-card__lang">{lang}</span>
          <div className="flex items-center gap-1">
            {workspacePath && (
              <button
                type="button"
                onClick={() => setApplyOpen(true)}
                className="code-card__copy mr-1"
                title="Apply to workspace file"
              >
                <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M12 20h9M16.5 3.5a2.1 2.1 0 013 3L7 19l-4 1 1-4z" />
                </svg>
                Apply
              </button>
            )}
            <button type="button" onClick={() => void copy()} className="code-card__copy">
              {copied ? (
                <>
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M20 6L9 17l-5-5" />
                  </svg>
                  Copied
                </>
              ) : (
                <>
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
                    <rect x="9" y="9" width="13" height="13" rx="2" />
                    <path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1" />
                  </svg>
                  Copy
                </>
              )}
            </button>
          </div>
        </div>
        <pre>{children}</pre>
      </div>
      {applyOpen && (
        <DiffModal
          initialCode={code}
          suggestedPath={lang === "tsx" || lang === "ts" || lang === "jsx" || lang === "js" || lang === "py" ? "" : ""}
          workspacePath={workspacePath}
          onClose={() => setApplyOpen(false)}
        />
      )}
    </>
  );
}

/** Collapsible reasoning panel for models that emit thinking tokens. */
function ReasoningPanel({ text }: { text: string }): JSX.Element {
  const [open, setOpen] = useState<boolean>(false);
  const chars = text.length;
  return (
    <details
      className="mb-2 rounded-lg border border-bd/[0.06] bg-surface/[0.5] open:bg-surface"
      open={open}
      onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}
    >
      <summary className="flex cursor-pointer select-none items-center gap-2 px-3 py-2 text-[12px] text-tx-mut hover:text-tx-dim">
        <svg
          className={`w-3.5 h-3.5 transition-transform ${open ? "rotate-90" : ""}`}
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M9 18l6-6-6-6" />
        </svg>
        <span className="font-medium">Thinking</span>
        <span className="ml-auto tabular-nums">{chars.toLocaleString()} tok</span>
      </summary>
      <div className="max-h-80 overflow-auto border-t border-bd/[0.06] px-3 py-2">
        <pre className="whitespace-pre-wrap font-mono text-[12px] leading-[1.6] text-tx-mut">
          {text}
        </pre>
      </div>
    </details>
  );
}

/** Small copy control shown under a finished assistant message. */
function CopyMessage({ text }: { text: string }): JSX.Element {
  const [copied, setCopied] = useState(false);
  const copy = async (): Promise<void> => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1400);
    } catch {
      // ignore
    }
  };
  return (
    <button type="button" onClick={() => void copy()} className="msg-action">
      {copied ? (
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M20 6L9 17l-5-5" />
        </svg>
      ) : (
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
          <rect x="9" y="9" width="13" height="13" rx="2" />
          <path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1" />
        </svg>
      )}
      {copied ? "Copied" : "Copy"}
    </button>
  );
}

const TOOL_META: Record<string, { label: string; icon: JSX.Element }> = {
  run_python: {
    label: "Ran Python",
    icon: <path d="M8 3v4M16 3v4M4 11h16M7 15l2 2 2-2M14 15l3 3" />,
  },
  calculator: {
    label: "Calculated",
    icon: <path d="M9 7h6M9 11h6M9 15h2M4 3h16v18H4z" />,
  },
  fetch_url: {
    label: "Fetched page",
    icon: <path d="M10 13a5 5 0 007 0l3-3a5 5 0 00-7-7l-1 1M14 11a5 5 0 00-7 0l-3 3a5 5 0 007 7l1-1" />,
  },
  web_search: {
    label: "Searched web",
    icon: <><circle cx="11" cy="11" r="7" /><path d="M21 21l-4-4" /></>,
  },
  review_screen: {
    label: "Reviewed screen",
    icon: <><rect x="2" y="3" width="20" height="14" rx="2" /><path d="M8 21h8M12 17v4" /></>,
  },
  see_image: {
    label: "Viewed image",
    icon: <><rect x="3" y="3" width="18" height="18" rx="2" /><circle cx="8.5" cy="8.5" r="1.5" /><path d="M21 15l-5-5L5 21" /></>,
  },
  critique: {
    label: "Critiqued",
    icon: <path d="M9 11l3 3L22 4M21 12v7a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2h11" />,
  },
  research: {
    label: "Researched",
    icon: <><circle cx="11" cy="11" r="7" /><path d="M21 21l-4-4M11 8v6M8 11h6" /></>,
  },
  list_skills: {
    label: "Listed skills",
    icon: <path d="M4 6h16M4 12h16M4 18h10" />,
  },
  read_skill: {
    label: "Read skill",
    icon: <path d="M4 19.5A2.5 2.5 0 016.5 17H20V3H6.5A2.5 2.5 0 004 5.5z" />,
  },
  read_file: {
    label: "Read file",
    icon: <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8zM14 2v6h6" />,
  },
  list_dir: {
    label: "Listed folder",
    icon: <path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2z" />,
  },
  write_file: {
    label: "Wrote file",
    icon: <path d="M12 20h9M16.5 3.5a2.1 2.1 0 013 3L7 19l-4 1 1-4z" />,
  },
  read_workspace_file: {
    label: "Read workspace file",
    icon: <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8zM14 2v6h6" />,
  },
  list_workspace_dir: {
    label: "Listed workspace dir",
    icon: <path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2z" />,
  },
  apply_workspace_edit: {
    label: "Applied workspace edit",
    icon: <path d="M12 20h9M16.5 3.5a2.1 2.1 0 013 3L7 19l-4 1 1-4z" />,
  },
  run_workspace_shell: {
    label: "Ran workspace shell",
    icon: <><rect x="2" y="3" width="20" height="14" rx="2" /><path d="M6 8l4 4-4 4M12 16h6" /></>,
  },
  generate_image: {
    label: "Generated image",
    icon: <><rect x="3" y="3" width="18" height="18" rx="2" /><circle cx="8.5" cy="8.5" r="1.5" /><path d="M21 15l-5-5L5 21" /></>,
  },
  text_to_speech: {
    label: "Generated speech",
    icon: <path d="M11 5L6 9H2v6h4l5 4zM15.5 8.5a5 5 0 010 7M19 5a9 9 0 010 14" />,
  },
};

/** Human-readable summary of what a pending action will do, from its args. */
function describePending(tool: ToolInvocation): { title: string; detail: string } {
  const a = tool.args ?? {};
  const s = (v: unknown): string => (typeof v === "string" ? v : "");
  if (tool.name === "write_file") {
    return { title: `Write file "${s(a.name) || s(a.path) || "untitled"}"`, detail: s(a.content) };
  }
  if (tool.name === "apply_workspace_edit") {
    return { title: `Edit workspace file "${s(a.file_path) || "untitled"}"`, detail: s(a.replace) };
  }
  if (tool.name === "run_workspace_shell") {
    return { title: `Run shell command in workspace`, detail: s(a.command) };
  }
  if (tool.name === "read_workspace_file") {
    return { title: `Read workspace file`, detail: s(a.file_path) };
  }
  if (tool.name === "list_workspace_dir") {
    return { title: `List workspace directory`, detail: s(a.dir_path) || "." };
  }
  if (tool.name === "generate_image") {
    return { title: "Generate an image (paid)", detail: s(a.prompt) };
  }
  if (tool.name === "text_to_speech") {
    return { title: "Generate speech (paid)", detail: s(a.text) };
  }
  if (tool.name === "read_file") {
    return { title: "Read a file from your machine", detail: s(a.path) };
  }
  if (tool.name === "list_dir") {
    return { title: "List a folder on your machine", detail: s(a.path) };
  }
  if (tool.name === "review_screen") {
    return { title: "Capture and review your screen", detail: "Takes a screenshot of your primary display." };
  }
  if (tool.name.startsWith("mcp__")) {
    const server = tool.name.split("__")[1] ?? "server";
    return {
      title: `Run MCP tool via "${server}"`,
      detail: Object.keys(a).length ? JSON.stringify(a, null, 2) : "(no arguments)",
    };
  }
  return { title: tool.name, detail: Object.keys(a).length ? JSON.stringify(a, null, 2) : "" };
}

const RISK_STYLE: Record<string, { border: string; badge: string; label: string }> = {
  paid: { border: "border-l-error/70", badge: "bg-error/15 text-error", label: "paid" },
  mcp: { border: "border-l-error/70", badge: "bg-error/15 text-error", label: "MCP" },
  write: { border: "border-l-warning/70", badge: "bg-warning/15 text-warning", label: "writes disk" },
  screen: { border: "border-l-warning/70", badge: "bg-warning/15 text-warning", label: "screen" },
  fs: { border: "border-l-warning/70", badge: "bg-warning/15 text-warning", label: "reads files" },
};

/** Pull a leading markdown list (the assistant's stated plan) off the reply.
 *  Returns items + the remaining body. Fewer than 2 items => not treated as a plan. */
function extractPlan(content: string): { items: string[]; body: string } {
  const lines = content.split("\n");
  let i = 0;
  while (i < lines.length && lines[i].trim() === "") i += 1;
  const items: string[] = [];
  const re = /^\s*(?:[-*]|\d+[.)])\s+(.*)$/;
  while (i < lines.length) {
    const m = lines[i].match(re);
    if (!m) break;
    items.push(m[1].replace(/\*\*/g, "").trim());
    i += 1;
  }
  if (items.length < 2) return { items: [], body: content };
  return { items, body: lines.slice(i).join("\n").replace(/^\n+/, "") };
}

/** Compact checklist of the assistant's plan, ticked as tool calls complete. */
function PlanChecklist({
  content,
  tools,
}: {
  content: string;
  tools: ToolInvocation[];
}): JSX.Element | null {
  const { items } = extractPlan(content);
  if (items.length === 0) return null;
  const doneCount = tools.filter((t) => t.status === "done").length;
  return (
    <div className="my-2 rounded-lg border border-bd/[0.08] bg-bd/[0.02] px-3 py-2 elev-1">
      <div className="text-[10px] uppercase tracking-wide text-tx-mut mb-1.5">Plan</div>
      <ul className="space-y-1">
        {items.map((item, idx) => {
          const done = idx < doneCount;
          return (
            <li key={idx} className="flex items-start gap-2 text-[12px]">
              <span className={`mt-0.5 flex-none ${done ? "accent-text" : "text-tx-mut"}`}>
                <svg className="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
                  {done ? <path d="M20 6L9 17l-5-5" /> : <circle cx="12" cy="12" r="8" />}
                </svg>
              </span>
              <span className={done ? "text-tx-dim line-through" : "text-tx-dim"}>{item}</span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/** Pending-approval card: shows what the action will do + Approve/Skip. */
function PendingCard({
  tool,
  onResolve,
}: {
  tool: ToolInvocation;
  onResolve?: (callId: string, decision: "approve" | "skip", remember: boolean) => void;
}): JSX.Element {
  const [remember, setRemember] = useState(false);
  const [busy, setBusy] = useState(false);
  const risk = tool.risk ?? "write";
  const style = RISK_STYLE[risk] ?? RISK_STYLE.write;
  const { title, detail } = describePending(tool);
  const canRemember = risk === "screen" || risk === "fs";
  const resolve = (decision: "approve" | "skip") => {
    if (busy || !tool.call_id) return;
    setBusy(true);
    onResolve?.(tool.call_id, decision, remember);
  };
  return (
    <div className={`my-2 rounded-lg border border-bd/[0.1] border-l-2 ${style.border} bg-bd/[0.03] overflow-hidden text-[13px]`}>
      <div className="px-3 py-2.5 space-y-2">
        <div className="flex items-center gap-2">
          <span className="text-tx font-medium">{title}</span>
          <span className={`text-[10px] px-1.5 py-0.5 rounded ${style.badge}`}>{style.label}</span>
          {typeof tool.cost === "number" && tool.cost > 0 && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-bd/[0.08] text-tx-dim tabular-nums">
              ~${tool.cost.toFixed(4)}
            </span>
          )}
          <span className="ml-auto text-[10px] text-tx-mut">needs approval</span>
        </div>
        {detail && (
          <pre className="font-mono text-[11px] text-tx-dim whitespace-pre-wrap break-words max-h-40 overflow-auto rounded bg-bd/[0.03] px-2 py-1.5">
            {detail.length > 1200 ? detail.slice(0, 1200) + "\n…" : detail}
          </pre>
        )}
        <div className="flex items-center gap-2 pt-0.5">
          <button
            type="button"
            disabled={busy}
            onClick={() => resolve("approve")}
            className="rounded-lg bg-accent/90 hover:bg-accent px-3 py-1 text-xs font-medium text-black disabled:opacity-50 transition-colors"
          >
            Approve
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => resolve("skip")}
            className="rounded-lg border border-bd/[0.12] px-3 py-1 text-xs text-tx-dim hover:bg-bd/[0.05] disabled:opacity-50 transition-colors"
          >
            Skip
          </button>
          {canRemember && (
            <label className="ml-1 flex items-center gap-1.5 text-[11px] text-tx-mut select-none cursor-pointer">
              <input
                type="checkbox"
                checked={remember}
                onChange={(e) => setRemember(e.target.checked)}
                className="accent-current"
              />
              remember this session
            </label>
          )}
          {busy && (
            <span className="flex items-center gap-1" aria-label="Sending">
              <span className="typing-dot" />
              <span className="typing-dot" />
              <span className="typing-dot" />
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

/** Collapsible card showing a single tool invocation + its output. */
function ToolCard({
  tool,
  onResolve,
}: {
  tool: ToolInvocation;
  onResolve?: (callId: string, decision: "approve" | "skip", remember: boolean) => void;
}): JSX.Element {
  const [open, setOpen] = useState(false);
  const meta = TOOL_META[tool.name] ?? {
    label: tool.name,
    icon: <circle cx="12" cy="12" r="9" />,
  };
  if (tool.status === "pending") {
    return <PendingCard tool={tool} onResolve={onResolve} />;
  }
  const running = tool.status === "running";
  const skipped = tool.status === "skipped";
  if (skipped) {
    return (
      <div className="my-2 rounded-lg border border-bd/[0.08] bg-bd/[0.02] px-3 py-2 text-[12px] text-tx-mut flex items-center gap-2">
        <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
          <path d="M18 6L6 18M6 6l12 12" />
        </svg>
        Skipped {meta.label.toLowerCase()}
      </div>
    );
  }
  return (
    <div className="my-2 rounded-lg border border-bd/[0.08] bg-bd/[0.02] overflow-hidden text-[13px]">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-bd/[0.03] transition-colors"
      >
        <span className={`accent-text flex-none ${running ? "animate-pulse" : ""}`}>
          <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
            {meta.icon}
          </svg>
        </span>
        <span className="text-tx-dim">{meta.label}</span>
        {running && (
          <span className="flex items-center gap-1 ml-1" aria-label="Running">
            <span className="typing-dot" />
            <span className="typing-dot" />
            <span className="typing-dot" />
          </span>
        )}
        {!running && tool.result && (
          <svg
            className={`ml-auto w-3.5 h-3.5 text-tx-mut transition-transform ${open ? "rotate-90" : ""}`}
            viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
          >
            <path d="M9 18l6-6-6-6" />
          </svg>
        )}
      </button>
      {open && (
        <div className="border-t border-bd/[0.07] px-3 py-2 space-y-2">
          {tool.args && Object.keys(tool.args).length > 0 && (
            <pre className="font-mono text-[11px] text-tx-mut whitespace-pre-wrap break-words">
              {JSON.stringify(tool.args, null, 2)}
            </pre>
          )}
          {tool.result && (
            <pre className="font-mono text-[11px] text-tx-dim whitespace-pre-wrap break-words max-h-56 overflow-auto">
              {tool.result}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}

export default function ChatView({
  chatId,
  tabKey,
  assistant = false,
  onChatCreated,
  onChanged,
  onOpenArtifact,
  onOpenToolsSettings,
}: ChatViewProps): JSX.Element {
  // Unique composer id per mounted tab instance so focus calls never land
  // on a hidden sibling tab's textarea.
  const inputId = tabKey ? `chat-input-${tabKey}` : "chat-input";
  // Personality — stable identity for the empty-state greeting, suggestion
  // chips, and composer placeholder. Pure UI; no model routing.
  const { settings: persona } = usePersonality();
  // Actions default ON: a chat that can't write anything is the surprising
  // state, not the safe one — every risky call still asks for approval.
  const [allowActions, setAllowActions] = useState<boolean>(
    () => localStorage.getItem("infinity-allow-actions") !== "0",
  );
  useEffect(() => {
    localStorage.setItem("infinity-allow-actions", allowActions ? "1" : "0");
  }, [allowActions]);
  const [approvalMode, setApprovalMode] = useState<ApprovalMode>(() => {
    const saved = localStorage.getItem("infinity-approval-mode");
    return isApprovalMode(saved) ? saved : "smart";
  });
  const [approvalMenuOpen, setApprovalMenuOpen] = useState(false);
  const [composerOptionsOpen, setComposerOptionsOpen] = useState(false);
  useEffect(() => {
    localStorage.setItem("infinity-approval-mode", approvalMode);
  }, [approvalMode]);
  const [agentPickerOpen, setAgentPickerOpen] = useState<boolean>(false);
  const [activeAgent, setActiveAgent] = useState<AgentLite | null>(() => {
    try {
      const raw = localStorage.getItem("infinity-active-agent");
      return raw ? (JSON.parse(raw) as AgentLite) : null;
    } catch {
      return null;
    }
  });
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState<string>("");
  const [sending, setSending] = useState<boolean>(false);
  // Follow-up queue (Qoder-style): messages submitted mid-generation stack
  // here instead of being dropped, and drain FIFO once the turn closes.
  // Steer aborts the run and jumps the queue.
  const [queue, setQueue] = useState<{ id: number; text: string }[]>([]);
  const [queueOpen, setQueueOpen] = useState<boolean>(true);
  const queueIdRef = useRef(0);
  const steerRef = useRef<string | null>(null);
  const composerPlaceholder = sending
    ? "Add a steer instruction"
    : "Ask or build anything";
  const [error, setError] = useState<string | null>(null);
  const [models, setModels] = useState<ChatModel[]>([]);
  const [model, setModel] = useState<string>("");
  // FL-Studio-style flair switch + Chat/Work vibe (both persist locally).
  const [fancy, setFancy] = useState<boolean>(
    () => localStorage.getItem("infinity-fancy") !== "0",
  );
  const [vibe, setVibe] = useState<"chat" | "work">(
    () => (localStorage.getItem("infinity-vibe") === "work" ? "work" : "chat"),
  );
  const [modelPillOpen, setModelPillOpen] = useState<boolean>(false);
  const [attachments, setAttachments] = useState<
    { name: string; dataUrl: string }[]
  >([]);
  const [webOn, setWebOn] = useState<boolean>(
    () => localStorage.getItem("infinity-default-web") === "1",
  );
  const [toolsOn, setToolsOn] = useState<boolean>(
    () => localStorage.getItem("infinity-default-tools") === "1",
  );
  const [toolCount, setToolCount] = useState<number>(0);
  const [toolCatalog, setToolCatalog] = useState<ToolCatalog | null>(null);
  const [toolMenuOpen, setToolMenuOpen] = useState<boolean>(false);
  // Per-tool opt-out. We persist the DISABLED ids (not the enabled ones) so a
  // tool added by a later backend release is on by default instead of silently
  // missing from an old saved allowlist.
  const [disabledTools, setDisabledTools] = useState<string[]>(() => {
    try {
      const raw = localStorage.getItem("infinity-disabled-tools");
      const parsed = raw ? (JSON.parse(raw) as unknown) : null;
      return Array.isArray(parsed) ? parsed.filter((x): x is string => typeof x === "string") : [];
    } catch {
      return [];
    }
  });
  useEffect(() => {
    localStorage.setItem("infinity-disabled-tools", JSON.stringify(disabledTools));
  }, [disabledTools]);
  const toggleTool = (id: string): void => {
    setDisabledTools((prev) =>
      prev.includes(id) ? prev.filter((t) => t !== id) : [...prev, id],
    );
  };
  // MCP servers popover (config map + live connection status).
  const [mcpMenuOpen, setMcpMenuOpen] = useState<boolean>(false);
  const mcpRef = useRef<HTMLDivElement | null>(null);
  const approvalRef = useRef<HTMLDivElement | null>(null);
  const composerOptionsRef = useRef<HTMLDivElement | null>(null);
  const [mcpServers, setMcpServers] = useState<McpServerRow[] | null>(null);
  const enabledToolIds = (toolCatalog?.built_in ?? [])
    .map((t) => t.id)
    .filter((id) => !disabledTools.includes(id));
  const enabledToolCount = toolCatalog ? enabledToolIds.length : toolCount;
  const { workspace } = useWorkspace();
  const [sendOnEnter, setSendOnEnter] = useState<boolean>(
    () => localStorage.getItem("infinity-send-on-enter") !== "0",
  );
  const [editIndex, setEditIndex] = useState<number | null>(null);
  const [lastUsage, setLastUsage] = useState<{
    prompt_tokens?: number;
    completion_tokens?: number;
    cost?: number | null;
  } | null>(null);
  const [showMessageCost, setShowMessageCost] = useState<boolean>(
    () => localStorage.getItem("infinity-show-message-cost") === "1",
  );
  const [lastRecalled, setLastRecalled] = useState<number>(0);
  const [lastOmnibrain, setLastOmnibrain] = useState<number>(0);
  const threadRef = useRef<HTMLDivElement>(null);
  const motionRootRef = useRef<HTMLDivElement>(null);
  const modelPillRef = useRef<HTMLDivElement>(null);
  const toolMenuRef = useRef<HTMLDivElement>(null);
  const toolButtonRef = useRef<HTMLButtonElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  // Guards the [chatId] loader: when a send creates the chat, the prop flips
  // mid-stream and a naive refetch would clobber the streaming thread.
  const sendingRef = useRef<boolean>(false);
  // Lets the user stop a running generation mid-stream.
  const abortRef = useRef<AbortController | null>(null);
  const stopGeneration = (): void => abortRef.current?.abort();

  // Run the quality-gated Code loop (/loop/run) and render its step trail
  // inline as an assistant message, then the final output. Triggered by "/build".
  const runLoop = async (goal: string): Promise<void> => {
    if (!goal.trim() || sending) return;
    setInput("");
    const assistantIndexRef = { current: -1 };
    setMessages((prev) => {
      const next: ChatMessage[] = [
        ...prev,
        { role: "user", content: goal },
        { role: "assistant", content: "**Building…**", tools: [] },
      ];
      assistantIndexRef.current = next.length - 1;
      return next;
    });
    setSending(true);
    const abort = new AbortController();
    abortRef.current = abort;
    const steps: string[] = [];
    const sync = (body: string): void =>
      setMessages((prev) => {
        const idx = assistantIndexRef.current;
        if (idx < 0 || idx >= prev.length) return prev;
        const c = [...prev];
        c[idx] = { ...c[idx], content: body };
        return c;
      });
    try {
      const resp = await apiFetch(`${API_BASE}/loop/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: abort.signal,
        body: JSON.stringify({ goal, job_type: "code", max_iter: 5 }),
      });
      if (!resp.ok || !resp.body) throw new Error(`HTTP ${resp.status}`);
      const reader = resp.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const parts = buf.split("\n\n");
        buf = parts.pop() ?? "";
        for (const p of parts) {
          if (!p.startsWith("data:")) continue;
          const e = JSON.parse(p.slice(5).trim());
          if (e.type === "step") {
            const state =
              e.status === "done" ? "Done" : e.status === "escalate" ? "Escalated" :
              e.status === "retry" ? "Retrying" : e.status === "error" ? "Error" : "Running";
            steps.push(`**${state}: ${e.label}** ? ${e.detail ?? ""}`);
            sync("**Building…**\n\n" + steps.join("\n\n"));
          } else {
            const head = e.ok ? "**Build complete**" : "**Finished at the iteration limit**";
            sync(
              `${head} · ${e.iterations} iteration(s)\n\n` +
              steps.join("\n\n") +
              (e.text ? "\n\n---\n\n" + e.text : "") +
              (e.error ? "\n\n**Error:** " + e.error : ""),
            );
          }
        }
      }
    } catch (err) {
      sync(loopFailureMessage(err));
    } finally {
      setSending(false);
    }
  };

  // Activate (or clear) an Agent Library persona; persisted across sessions.
  const pickAgent = (agent: AgentLite | null): void => {
    setActiveAgent(agent);
    try {
      if (agent) localStorage.setItem("infinity-active-agent", JSON.stringify(agent));
      else localStorage.removeItem("infinity-active-agent");
    } catch {
      /* ignore quota */
    }
  };

  // Approve/skip a pending tool call; the SSE stream (still blocked on the
  // backend) resumes and flips the card to running/skipped on its own.
  const resolveApproval = (
    callId: string,
    decision: "approve" | "skip",
    remember: boolean,
  ): void => {
    if (decision === "approve") playSound("approve");
    void apiFetch(`${API_BASE}/approvals/${callId}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision, remember }),
    }).catch(() => undefined);
  };

  const addFiles = (files: FileList | File[]): void => {
    for (const file of Array.from(files).slice(0, 4)) {
      if (!file.type.startsWith("image/")) {
        continue;
      }
      const reader = new FileReader();
      reader.onload = () => {
        const dataUrl = String(reader.result ?? "");
        if (dataUrl.startsWith("data:image/")) {
          setAttachments((prev) =>
            prev.length >= 4 ? prev : [...prev, { name: file.name, dataUrl }],
          );
        }
      };
      reader.readAsDataURL(file);
    }
  };

  // Load the model list + tool count once.
  useEffect(() => {
    let ok = true;
    void fetchChatModels()
      .then((data) => {
        if (ok) {
          setModels(data.models);
          setModel((current) => current || data.default);
        }
      })
      .catch(() => undefined);
    void apiFetch(`${API_BASE}/tools`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d: ToolCatalog | null) => {
        if (ok && d) {
          setToolCatalog(d);
          setToolCount(d.counts?.total ?? 0);
        }
      })
      .catch(() => undefined);
    return () => {
      ok = false;
    };
  }, []);

  useEffect(() => {
    if (!toolMenuOpen) return;
    const onPointerDown = (event: MouseEvent): void => {
      if (!toolMenuRef.current?.contains(event.target as Node)) {
        setToolMenuOpen(false);
      }
    };
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key === "Escape") {
        setToolMenuOpen(false);
        toolButtonRef.current?.focus();
      }
    };
    document.addEventListener("mousedown", onPointerDown);
    window.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [toolMenuOpen]);

  // Load messages when the active chat changes (or clear for a new chat).
  // Skipped while sending: a send that lazily creates the chat flips this
  // prop mid-stream, and the DB is still empty at that point.
  // Every tab switch starts a clean composer. chatId alone can't detect
  // placeholder -> placeholder switches (both are null), so key off the tab
  // id: switching tabs must not leak text, edit mode, or attachments.
  useEffect(() => {
    setInput("");
    setEditIndex(null);
    setAttachments([]);
  }, [tabKey]);

  useEffect(() => {
    if (sendingRef.current) {
      return;
    }
    if (!chatId) {
      setMessages([]);
      return;
    }
    // Clear immediately so the old thread doesn't flash while the new one loads.
    setMessages([]);
    setError(null);
    let ok = true;
    void (async () => {
      try {
        const response = await apiFetch(`${API_BASE}/chats/${chatId}`);
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }
        const data = (await response.json()) as {
          model: string;
          messages: ChatMessage[];
        };
        if (ok && !sendingRef.current) {
          setMessages(data.messages);
          if (data.model) {
            setModel(data.model);
          }
        }
      } catch {
        if (ok) {
          setError("Could not load this chat.");
        }
      }
    })();
    return () => {
      ok = false;
    };
  }, [chatId]);

  // Auto-scroll to the newest message — but only if the user is already near
  // the bottom, so reading earlier messages mid-stream isn't interrupted.
  useEffect(() => {
    const el = threadRef.current;
    if (!el) return;
    const nearBottom =
      el.scrollHeight - el.scrollTop - el.clientHeight < 140;
    if (nearBottom) {
      el.scrollTo({ top: el.scrollHeight });
    }
  }, [messages, sending]);

  // React to device-local prefs changing in Settings.
  useEffect(() => {
    const sync = (): void => {
      setShowMessageCost(
        localStorage.getItem("infinity-show-message-cost") === "1",
      );
      setSendOnEnter(localStorage.getItem("infinity-send-on-enter") !== "0");
    };
    window.addEventListener("infinity:appearance", sync);
    return () => window.removeEventListener("infinity:appearance", sync);
  }, []);

  // Load MCP servers whenever the popover opens, so the status is live rather
  // than whatever it was at mount. Config gives us the full configured set;
  // status gives us which of them actually came up.
  useEffect(() => {
    if (!mcpMenuOpen) return;
    let ok = true;
    setMcpServers(null);
    void (async (): Promise<void> => {
      try {
        const [configRes, statusRes] = await Promise.all([
          apiFetch(`${API_BASE}/mcp/servers`),
          apiFetch(`${API_BASE}/mcp/status`),
        ]);
        const config = configRes.ok
          ? ((await configRes.json()) as {
              servers?: Record<string, { enabled?: boolean }>;
            })
          : { servers: {} };
        const status = statusRes.ok
          ? ((await statusRes.json()) as {
              servers?: {
                name: string;
                enabled?: boolean;
                connected?: boolean;
                tool_count?: number;
                error?: string | null;
              }[];
            })
          : { servers: [] };
        const live = new Map(
          (status.servers ?? []).map((s) => [s.name, s] as const),
        );
        const names = new Set<string>([
          ...Object.keys(config.servers ?? {}),
          ...live.keys(),
        ]);
        const rows: McpServerRow[] = [...names].sort().map((name) => {
          const s = live.get(name);
          const enabled = s?.enabled ?? config.servers?.[name]?.enabled ?? true;
          const connected = Boolean(s?.connected);
          return {
            name,
            enabled,
            connected,
            detail: !enabled
              ? "Disabled"
              : connected
                ? "Connected"
                : (s?.error ?? "Not connected"),
            tools: typeof s?.tool_count === "number" ? s.tool_count : null,
          };
        });
        if (ok) setMcpServers(rows);
      } catch {
        if (ok) setMcpServers([]);
      }
    })();
    return () => {
      ok = false;
    };
  }, [mcpMenuOpen]);

  useEffect(() => {
    if (!approvalMenuOpen) return;
    const onDown = (e: MouseEvent): void => {
      if (approvalRef.current && !approvalRef.current.contains(e.target as Node)) {
        setApprovalMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [approvalMenuOpen]);

  useEffect(() => {
    if (!composerOptionsOpen) return;
    const onDown = (e: MouseEvent): void => {
      if (
        composerOptionsRef.current &&
        !composerOptionsRef.current.contains(e.target as Node)
      ) {
        setComposerOptionsOpen(false);
      }
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [composerOptionsOpen]);

  // Close the MCP menu on outside click.
  useEffect(() => {
    if (!mcpMenuOpen) return;
    const onDown = (e: MouseEvent): void => {
      if (mcpRef.current && !mcpRef.current.contains(e.target as Node)) {
        setMcpMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [mcpMenuOpen]);

  // Fancy gates every animation app-wide (Don't distract me = off switch).
  useEffect(() => {
    document.documentElement.classList.toggle("fancy-off", !fancy);
    try {
      localStorage.setItem("infinity-fancy", fancy ? "1" : "0");
    } catch {
      /* ignore quota */
    }
  }, [fancy]);

  useEffect(() => {
    document.documentElement.classList.toggle("infinity-thinking", sending);
    return () => document.documentElement.classList.remove("infinity-thinking");
  }, [sending]);

  // Vibe: Work mode arms the tools; Chat keeps things conversational.
  useEffect(() => {
    try {
      localStorage.setItem("infinity-vibe", vibe);
    } catch {
      /* ignore quota */
    }
    if (vibe === "work") setToolsOn(true);
  }, [vibe]);

  // Close the top model pill on outside click.
  useEffect(() => {
    if (!modelPillOpen) {
      return;
    }
    const onDown = (e: MouseEvent): void => {
      if (modelPillRef.current && !modelPillRef.current.contains(e.target as Node)) {
        setModelPillOpen(false);
      }
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [modelPillOpen]);

  /** OpenRouter-style grouped model picker.
   *  Sections: Auto · Free · Reasoning · Fast. Smooth, searchable, no noise. */
  const renderModelMenu = (close: () => void): JSX.Element => {
    const groups: { section: string; models: typeof models }[] = [
      { section: "Router", models: models.filter((m) => m.auto) },
      { section: "Free", models: models.filter((m) => m.free && !m.auto) },
      { section: "Reasoning", models: models.filter((m) => !m.auto && !m.free && !m.local && (m.label.toLowerCase().includes("kimi") || m.label.toLowerCase().includes("glm"))) },
      { section: "Fast", models: models.filter((m) => !m.auto && !m.free && !m.local && !m.label.toLowerCase().includes("kimi") && !m.label.toLowerCase().includes("glm")) },
    ].filter((g) => g.models.length > 0);

    return (
      <div className="composer-popover-enter composer-popover-enter--right absolute bottom-full right-0 mb-2 z-20 w-[280px] max-h-[420px] overflow-y-auto rounded-2xl border border-bd/[0.11] material-overlay p-1.5 shadow-2xl shadow-black/50">
        {groups.map((g) => (
          <div key={g.section} className="mt-1 first:mt-0">
            <p className="px-2.5 pt-1.5 pb-0.5 text-[10px] uppercase tracking-wider text-tx-mut font-medium">{g.section}</p>
            {g.models.map((m) => (
              <button
                key={m.id}
                type="button"
                onClick={() => { setModel(m.id); close(); }}
                className={`w-full flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-left transition-colors ${
                  model === m.id ? "bg-accent/10" : "hover:bg-bd/[0.06]"
                }`}
              >
                <span className="min-w-0 flex-1">
                  <span className="text-[13px] leading-tight text-tx truncate block">{m.label}</span>
                  <span className="block text-[10px] leading-tight text-tx-mut truncate">{m.hint}</span>
                </span>
                <span className="flex flex-none items-center gap-0.5" title={`Strength ${m.strength ?? 0}/5`}>
                  {[1, 2, 3, 4, 5].map((i) => (
                    <span key={i} className={`h-1.5 w-0.5 rounded-full ${i <= (m.strength ?? 0) ? "bg-accent" : "bg-bd/[0.14]"}`} />
                  ))}
                </span>
                {model === m.id && (
                  <svg className="w-3.5 h-3.5 accent-text flex-none" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M20 6L9 17l-5-5" /></svg>
                )}
              </button>
            ))}
          </div>
        ))}
      </div>
    );
  };

  const send = async (
    mode: "append" | "regenerate" | "edit" = "append",
    override?: string,
  ): Promise<void> => {
    const content = (override ?? input).trim();
    if (sending) {
      return;
    }
    if (mode !== "regenerate" && !content) {
      return;
    }
    // "/build <goal>" routes to the quality-gated Code loop instead of plain chat.
    const buildPrefix = content.toLowerCase();
    if (
      mode === "append" &&
      (buildPrefix === "/build" || buildPrefix.startsWith("/build "))
    ) {
      void runLoop(content.slice(7).trim());
      return;
    }
    const isEdit = mode === "edit" && editIndex !== null;
    const keepMessages = isEdit ? editIndex : undefined;
    const images = override === undefined ? attachments.map((a) => a.dataUrl) : [];
    const snapshot = messages;
    setError(null);
    if (mode !== "regenerate") playSound("send");
    setSending(true);
    sendingRef.current = true;
    if (mode === "regenerate") {
      // Drop trailing assistant replies locally; backend mirrors this.
      setMessages((prev) => {
        const next = [...prev];
        while (next.length && next[next.length - 1].role === "assistant") {
          next.pop();
        }
        return next;
      });
    } else if (isEdit) {
      setMessages((prev) => [
        ...prev.slice(0, keepMessages),
        { role: "user", content },
      ]);
      setEditIndex(null);
      setInput("");
      setAttachments([]);
    } else {
      setMessages((prev) => [...prev, { role: "user", content }]);
      if (override === undefined) {
        setInput("");
        setAttachments([]);
      }
    }
    try {
      // Create the chat lazily on the first message.
      let id = chatId;
      if (!id) {
        const created = await apiFetch(`${API_BASE}/chats`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ model }),
        });
        if (!created.ok) {
          throw new Error("Could not start a chat.");
        }
        id = ((await created.json()) as { id: string }).id;
        onChatCreated(id);
      }
      abortRef.current = new AbortController();
      const response = await apiFetch(`${API_BASE}/chats/${id}/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: abortRef.current.signal,
        body: JSON.stringify({
          content,
          model,
          images,
          web: webOn,
          tools: toolsOn,
          assistant,
          // NOT gated on assistant mode: in plain Chat this was always false,
          // so write_file was never offered and the model correctly answered
          // "I can't create files". Chat should be able to act too.
          allow_actions: allowActions,
          approval_mode: approvalMode,
          // Per-tool opt-outs from the toolbox. Sent as the positive set so the
          // backend never has to know about the UI's disabled-list encoding.
          enabled_tools: enabledToolIds,
          agent_id: activeAgent?.id ?? null,
          tone: vibe,
          mode: isEdit ? "edit" : mode,
          keep_messages: keepMessages,
        }),
      });
      if (!response.ok || !response.body) {
        throw new Error(`HTTP ${response.status}`);
      }

      // Append an empty assistant message and stream tokens (and tool cards)
      // into it. Tool frames arrive before the first text delta.
      const tools: ToolInvocation[] = [];
      let burstFired = false;
      setMessages((prev) => [...prev, { role: "assistant", content: "", tools: [] }]);
      let full = "";
      let reasoning = "";
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let streaming = true;
      const syncAssistant = (): void => {
        setMessages((prev) => {
          const next = [...prev];
          next[next.length - 1] = {
            role: "assistant",
            content: full,
            tools: [...tools],
            reasoning,
          };
          return next;
        });
      };
      while (streaming) {
        const { done, value } = await reader.read();
        if (done) {
          break;
        }
        buffer += decoder.decode(value, { stream: true });
        const events = buffer.split("\n\n");
        buffer = events.pop() ?? "";
        for (const evt of events) {
          const line = evt.trim();
          if (!line.startsWith("data:")) {
            continue;
          }
          try {
            const payload = JSON.parse(line.slice(5).trim()) as {
              delta?: string;
              reasoning?: string;
              done?: boolean;
              tool?: {
                name: string;
                args?: Record<string, unknown>;
                result?: string;
                status: "running" | "done" | "pending" | "skipped";
                call_id?: string;
                risk?: string;
                cost?: number | null;
              };
              usage?: {
                prompt_tokens?: number;
                completion_tokens?: number;
                cost?: number | null;
              } | null;
              recalled?: number;
              omnibrain?: number;
              error?: string;
            };
            if (payload.error) {
              // An error frame (e.g. "Local model unavailable…") can end the
              // stream with no `done` frame. Surface it and finalize here so
              // the reply never sits stuck on "thinking".
              if (!full && !reasoning && tools.length === 0) {
                // Nothing visible streamed yet — explain inside the bubble
                // itself (same **Error:** convention as the loop results
                // above) rather than leaving an empty assistant message.
                full = `**Error:** ${payload.error}`;
                syncAssistant();
              } else {
                // Keep the partial reply; the explanation goes to the red
                // banner above the composer (the existing error surface).
                setError(payload.error);
              }
              streaming = false;
              break;
            }
            if (payload.done) {
              setLastRecalled(payload.recalled ?? 0);
              setLastOmnibrain(payload.omnibrain ?? 0);
            }
            if (payload.tool) {
              const t = payload.tool;
              // Coding just started: fire the composer burst once per reply.
              if (
                !burstFired &&
                fancy &&
                (t.status === "pending" || t.status === "running") &&
                ["write_file", "edit_file", "apply_workspace_edit",
                 "run_python", "run_workspace_shell"].includes(t.name)
              ) {
                burstFired = true;
                window.dispatchEvent(new CustomEvent("infinity:turbo"));
              }
              if (t.status === "pending") {
                tools.push({
                  name: t.name,
                  args: t.args,
                  status: "pending",
                  call_id: t.call_id,
                  risk: t.risk,
                  cost: t.cost,
                });
              } else if (t.status === "running") {
                // A pending card for this call flips to running once approved.
                // Match by call_id when available so parallel same-name tools
                // update the right card.
                let flipped = false;
                if (t.call_id) {
                  for (let i = tools.length - 1; i >= 0; i -= 1) {
                    if (tools[i].call_id === t.call_id && tools[i].status === "pending") {
                      tools[i] = { ...tools[i], status: "running" };
                      flipped = true;
                      break;
                    }
                  }
                }
                if (!flipped) {
                  for (let i = tools.length - 1; i >= 0; i -= 1) {
                    if (tools[i].name === t.name && tools[i].status === "pending") {
                      tools[i] = { ...tools[i], status: "running" };
                      flipped = true;
                      break;
                    }
                  }
                }
                if (!flipped) {
                  tools.push({ name: t.name, args: t.args, status: "running" });
                }
              } else {
                // done | skipped: complete the newest running/pending entry.
                let completed = false;
                if (t.call_id) {
                  for (let i = tools.length - 1; i >= 0; i -= 1) {
                    if (tools[i].call_id === t.call_id && (tools[i].status === "running" || tools[i].status === "pending")) {
                      tools[i] = { ...tools[i], result: t.result, status: t.status };
                      completed = true;
                      break;
                    }
                  }
                }
                if (!completed) {
                  for (let i = tools.length - 1; i >= 0; i -= 1) {
                    const st = tools[i].status;
                    if (tools[i].name === t.name && (st === "running" || st === "pending")) {
                      tools[i] = { ...tools[i], result: t.result, status: t.status };
                      break;
                    }
                  }
                }
                // Mechanical latch on completion; silent on skipped.
                if (t.status === "done") playSound("tool");
              }
              syncAssistant();
            }
            if (payload.done && payload.usage) {
              setLastUsage(payload.usage);
            }
            if (payload.delta) {
              full += payload.delta;
              syncAssistant();
            }
            if (payload.reasoning) {
              reasoning += payload.reasoning;
              syncAssistant();
            }
            if (payload.done) {
              streaming = false;
              // Soft "over to you" chime on clean turn-close. Silent on
              // aborts (handled in the catch above) so Stop stays quiet.
              playSound("turn");
            }
          } catch {
            // Skip malformed SSE frames.
          }
        }
      }

      const artifact = extractArtifact(full);
      if (artifact) {
        onOpenArtifact(artifact);
      }
      onChanged();
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") {
        // User pressed Stop: keep whatever streamed so far, persist it.
        onChanged();
      } else {
        setError(err instanceof Error ? err.message : "Message failed");
        // Restore the pre-send thread so the user can retry cleanly.
        setMessages(snapshot);
        if (mode !== "regenerate" && override === undefined) {
          setInput(content);
        }
      }
    } finally {
      abortRef.current = null;
      setSending(false);
      sendingRef.current = false;
    }
  };

  // Queue-aware submit: mid-generation, Enter/send stacks the message into
  // the follow-up queue instead of dropping it on the floor.
  const submit = (): void => {
    const text = input.trim();
    if (sending) {
      if (!text) {
        return;
      }
      queueIdRef.current += 1;
      setQueue((q) => [...q, { id: queueIdRef.current, text }]);
      setInput("");
      return;
    }
    void send(editIndex !== null ? "edit" : "append");
  };

  // Steer: pull a queued message forward, aborting the current turn.
  const steer = (id: number): void => {
    const item = queue.find((q) => q.id === id);
    if (!item) {
      return;
    }
    setQueue((q) => q.filter((x) => x.id !== id));
    if (sending) {
      steerRef.current = item.text;
      abortRef.current?.abort();
    } else {
      void send("append", item.text);
    }
  };

  const steerInput = (): void => {
    const text = input.trim();
    if (!text) return;
    if (!sending) {
      void send(editIndex !== null ? "edit" : "append", text);
      return;
    }
    setInput("");
    steerRef.current = text;
    abortRef.current?.abort();
  };

  // Drain the queue (steer first) whenever a turn closes.
  useEffect(() => {
    if (sending) {
      return;
    }
    const steerText = steerRef.current;
    if (steerText !== null) {
      steerRef.current = null;
      void send("append", steerText);
      return;
    }
    if (queue.length > 0) {
      const [next, ...rest] = queue;
      setQueue(rest);
      void send("append", next.text);
    }
    // send is recreated each render; queue/sending are the real triggers.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sending, queue]);

  const startEdit = (index: number): void => {
    if (sending) {
      return;
    }
    setEditIndex(index);
    setInput(messages[index]?.content ?? "");
    window.setTimeout(() => document.getElementById(inputId)?.focus(), 0);
  };

  const exportChat = (): void => {
    const lines = messages.map((m) =>
      (m.role === "user" ? "## You\n\n" : "## Infinity Code\n\n") + m.content,
    );
    const blob = new Blob(
      ["# Infinity Code chat\n\n" + lines.join("\n\n---\n\n") + "\n"],
      { type: "text/markdown" },
    );
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "infinity-chat.md";
    a.click();
    URL.revokeObjectURL(url);
  };

  const currentModel = models.find((m) => m.id === model);
  const isEmpty = messages.length === 0;
  useGSAP(
    () => {
      const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      const targets = [
        ".motion-hero-mark",
        ".motion-hero-copy",
        ".motion-hero-suggestion",
        ".motion-composer",
      ];

      if (!fancy || reduceMotion) {
        gsap.set(targets, { clearProps: "opacity,visibility,transform" });
        return;
      }

      const timeline = gsap.timeline({
        defaults: { ease: "power3.out" },
      });

      if (isEmpty) {
        timeline
          .from(".motion-hero-mark", {
            autoAlpha: 0,
            y: 10,
            scale: 0.96,
            duration: 0.42,
          })
          .from(
            ".motion-hero-copy",
            {
              autoAlpha: 0,
              y: 8,
              duration: 0.34,
              stagger: 0.055,
            },
            0.08,
          )
          .from(
            ".motion-hero-suggestion",
            {
              autoAlpha: 0,
              y: 6,
              scale: 0.985,
              duration: 0.3,
              stagger: 0.04,
            },
            0.22,
          );
      }

      timeline.from(
        ".motion-composer",
        {
          autoAlpha: 0,
          y: 12,
          scale: 0.992,
          duration: 0.44,
        },
        isEmpty ? 0.12 : 0,
      );
    },
    { scope: motionRootRef, dependencies: [tabKey, fancy] },
  );

  // Slash-command palette: typing "/" at the start of an empty-ish composer.
  const slashQuery =
    input.startsWith("/") && !input.includes(" ") ? input.slice(1).toLowerCase() : null;
  const slashCommands: {
    key: string;
    label: string;
    hint: string;
    run: () => void;
  }[] = [
    { key: "build", label: "/build", hint: "Run the quality-gated Code loop (grounded, self-checking)", run: () => setInput("/build ") },
    { key: "agent", label: "/agent", hint: "Pick a specialist persona", run: () => setAgentPickerOpen(true) },
    { key: "skill", label: "/skill", hint: "Browse the Skill Vault", run: () => window.dispatchEvent(new CustomEvent("infinity:open-vault")) },
    { key: "web", label: "/web", hint: webOn ? "Turn web search off" : "Turn web search on", run: () => setWebOn((w) => !w) },
    { key: "tools", label: "/tools", hint: "Open the toolbox", run: () => setToolMenuOpen(true) },
    { key: "model", label: "/model", hint: "Choose the model", run: () => setModelPillOpen(true) },
  ];
  const slashMatches =
    slashQuery !== null
      ? slashCommands.filter((c) => c.key.startsWith(slashQuery) || slashQuery === "")
      : [];
  const slashOpen = slashMatches.length > 0 && slashQuery !== null;
  const runSlash = (cmd: (typeof slashCommands)[number]): void => {
    setInput("");
    cmd.run();
  };

  return (
    <div ref={motionRootRef} className="flex flex-1 min-h-0 flex-col">
      <div ref={threadRef} className="flex-1 overflow-y-auto">
        {isEmpty ? (
          <div className="relative h-full flex items-center justify-center px-6">
            {fancy && (
              <div aria-hidden="true" className="hero-glow top-[14%]" />
            )}
            <div className="w-full max-w-xl text-center -mt-10">
              {assistant ? (
                <>
                  <span className="motion-hero-mark mx-auto mb-6 flex h-12 w-12 items-center justify-center rounded-2xl bg-accent/10 accent-text select-none">
                    <svg className="w-6 h-6" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M12 2l2.4 5.5L20 8l-4 4 1 6-5-3-5 3 1-6-4-4 5.6-.5z" />
                    </svg>
                  </span>
                  <h1 className="motion-hero-copy font-display text-[28px] font-normal tracking-[-0.018em] text-tx">
                    Executive Assistant
                  </h1>
                  <p className="motion-hero-copy mt-2.5 text-tx-dim text-sm">
                    I can use your PC to get work done — look at your screen, read files, run code, research the web, and create.
                  </p>
                </>
              ) : (
                <>
                  <span className="motion-hero-mark hero-tile hero-infinity mx-auto mb-6 flex h-16 w-16 items-center justify-center rounded-[18px] select-none" aria-hidden="true">
                    <InfinityMark lit className="h-auto w-9 accent-text" />
                  </span>
                  <h1 className="motion-hero-copy hero-wordmark font-display text-[34px] leading-none font-normal tracking-[-0.018em]">
                    Infinity&nbsp;Code
                  </h1>
                  <p className="motion-hero-copy mt-3 text-tx-dim text-[15px]">
                    {buildGreeting(getDaypart(new Date().getHours()), persona)}
                  </p>
                  <div className="mt-6 flex flex-wrap justify-center gap-2">
                    {getSuggestions(persona.tone).map((s) => (
                      <button
                        key={s}
                        type="button"
                        onClick={() => setInput(s + " ")}
                        className="light-sweep-control motion-hero-suggestion chat-suggestion rounded-full border border-bd/[0.1] px-3.5 py-1.5 text-[13px] text-tx-dim hover:text-tx hover:border-accent/50 transition-colors"
                      >
                        <span className="light-sweep-text">{s}</span>
                      </button>
                    ))}
                  </div>
                </>
              )}
              {assistant && (
                <div className="motion-hero-copy mt-6 grid grid-cols-2 gap-2 text-left">
                  {[
                    ["Screen & Vision", "Review your screen, read images"],
                    ["Files & Code", "Read files, run Python, write output"],
                    ["Web & Research", "Search, browse, cited briefings"],
                    ["Create", "Generate images & speech (needs actions)"],
                  ].map(([t, d]) => (
                    <div
                      key={t}
                      className="rounded-xl border border-bd/[0.07] bg-surface px-3.5 py-3"
                    >
                      <p className="text-[13px] text-tx">{t}</p>
                      <p className="text-[11px] text-tx-mut mt-0.5">{d}</p>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        ) : (
          <div className="max-w-3xl mx-auto px-6 py-8 space-y-6">
            {messages.map((message, index) => (
              <div key={`${message.role}-${message.content.slice(0, 40)}-${index}`} className="message-enter">
                {message.role === "user" ? (
                  <div className="group flex justify-end items-start gap-1.5">
                    <button
                      type="button"
                      onClick={() => startEdit(index)}
                      title="Edit and resend"
                      className="msg-action mt-2 opacity-0 group-hover:opacity-100 transition-opacity"
                    >
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M17 3a2.85 2.83 0 114 4L7.5 20.5 2 22l1.5-5.5z" />
                      </svg>
                    </button>
                    <div className="max-w-[85%] rounded-2xl rounded-br-md bg-surface-2 border border-accent/[0.14] px-4 py-2.5 text-[15px] text-tx whitespace-pre-wrap">
                      {message.content}
                    </div>
                  </div>
                ) : (
                  <div className="group flex gap-3">
                    <img src={heroLogo} alt="" aria-hidden="true"
                      className="flex-none h-6 w-6 mt-0.5 rounded-[7px] select-none" />
                    <div className="min-w-0 flex-1 chat-markdown text-[15px] text-tx leading-[1.7]">
                      {message.reasoning && message.reasoning.length > 0 && (
                        <ReasoningPanel text={message.reasoning} />
                      )}
                      {message.tools && message.tools.length > 0 && (
                        <PlanChecklist content={message.content} tools={message.tools} />
                      )}
                      {message.tools?.map((tool, ti) => (
                        <ToolCard key={ti} tool={tool} onResolve={resolveApproval} />
                      ))}
                      <Markdown
                        components={{
                          code: CodeBlock,
                          pre: (props) => <PreBlock {...props} workspacePath={workspace.path} />,
                        }}
                      >
                        {message.content}
                      </Markdown>
                      {(() => {
                        const artifact = extractArtifact(message.content);
                        if (!artifact) {
                          return null;
                        }
                        return (
                          <button
                            type="button"
                            onClick={() => onOpenArtifact(artifact)}
                            className="mt-3 flex items-center gap-3 rounded-xl border border-bd/[0.08] bg-surface hover:border-accent/40 px-4 py-3 transition-colors w-full text-left group"
                          >
                            <span className="flex-none h-9 w-9 rounded-lg bg-accent/10 flex items-center justify-center accent-text">
                              <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                                <path d="M12 19l7-7 3 3-7 7-3-3z" />
                                <path d="M18 13l-1.5-7.5L2 2l3.5 14.5L13 18l5-5z" />
                                <circle cx="11" cy="11" r="2" />
                              </svg>
                            </span>
                            <span className="min-w-0">
                              <span className="block text-sm text-tx">
                                {artifact.title}
                              </span>
                              <span className="block text-xs text-tx-mut">
                                Open in Infinity Design
                              </span>
                            </span>
                            <svg className="ml-auto w-4 h-4 text-tx-mut group-hover:text-accent transition-colors" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                              <path d="M9 18l6-6-6-6" />
                            </svg>
                          </button>
                        );
                      })()}
                      {message.content.trim().length > 0 && (
                        <div className="mt-2 -ml-1 flex items-center gap-1 opacity-0 group-hover:opacity-100 focus-within:opacity-100 transition-opacity">
                          <CopyMessage text={message.content} />
                          {index === messages.length - 1 && !sending && (
                            <button
                              type="button"
                              onClick={() => void send("regenerate")}
                              className="msg-action"
                              title="Regenerate this reply"
                            >
                              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
                                <path d="M23 4v6h-6" />
                                <path d="M20.49 15a9 9 0 11-2.12-9.36L23 10" />
                              </svg>
                              Retry
                            </button>
                          )}
                          {showMessageCost &&
                            index === messages.length - 1 &&
                            lastUsage?.completion_tokens != null && (
                              <span className="ml-1 text-[10px] tabular-nums text-tx-mut select-none">
                                {(lastUsage.prompt_tokens ?? 0) +
                                  (lastUsage.completion_tokens ?? 0)}{" "}
                                tok
                                {typeof lastUsage.cost === "number"
                                  ? ` · $${lastUsage.cost.toFixed(4)}`
                                  : ""}
                              </span>
                            )}
                          {index === messages.length - 1 && lastRecalled > 0 && (
                            <span
                              title={`Recalled ${lastRecalled} memory item(s) from past chats`}
                              className="ml-1 inline-flex items-center gap-0.5 text-[10px] text-tx-mut select-none"
                            >
                              <svg className="w-2.5 h-2.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                <path d="M12 8V4H8" />
                                <rect x="4" y="8" width="16" height="12" rx="2" />
                                <path d="M2 14h2M20 14h2M15 13v2M9 13v2" />
                              </svg>
                              recalled {lastRecalled}
                            </span>
                          )}
                          {index === messages.length - 1 && lastOmnibrain > 0 && (
                            <span
                              title={`Grounded in ${lastOmnibrain} passage(s) from your OmniBrain (memories, vault, project docs)`}
                              className="ml-1 inline-flex items-center gap-0.5 text-[10px] text-tx-mut select-none"
                            >
                              <svg className="w-2.5 h-2.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                <path d="M12 5a3 3 0 0 0-3 3 3 3 0 0 0-1 5.8A2.5 2.5 0 0 0 10.5 19 2.5 2.5 0 0 0 13 16.5v-9A2.5 2.5 0 0 0 12 5Z" />
                                <path d="M12 5a3 3 0 0 1 3 3 3 3 0 0 1 1 5.8A2.5 2.5 0 0 1 13.5 19" />
                              </svg>
                              brain {lastOmnibrain}
                            </span>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </div>
            ))}
            {sending &&
              (messages.length === 0 ||
                messages[messages.length - 1].role !== "assistant" ||
                messages[messages.length - 1].content === "") && (
                <div className="thinking-row flex items-center gap-2.5">
                  <span className="thinking-mark flex h-7 w-7 flex-none items-center justify-center rounded-lg" aria-hidden="true">
                    <InfinityMark lit active className="h-auto w-4 accent-text" />
                  </span>
                  <span className="thinking-shimmer text-[13px]">Thinking</span>
                </div>
              )}
          </div>
        )}
      </div>

      {/* Composer */}
      <div className="motion-composer composer-shell shrink-0 px-6 py-4">
        <div className="max-w-3xl mx-auto">
          {error && (
            <div className="mb-3 rounded-lg border border-error/20 bg-error/[0.06] text-error text-sm p-3">
              {error}
            </div>
          )}
          {editIndex !== null && (
            <div className="mb-2 flex items-center justify-between rounded-lg border border-accent/30 bg-accent/[0.06] px-3 py-1.5 text-xs accent-text">
              <span>Editing an earlier message — later replies will be replaced.</span>
              <button
                type="button"
                onClick={() => {
                  setEditIndex(null);
                  setInput("");
                }}
                className="hover:underline"
              >
                Cancel
              </button>
            </div>
          )}
          {attachments.length > 0 && (
            <div className="mb-2 flex items-center gap-2">
              {attachments.map((a, i) => (
                <span key={i} className="relative group/att">
                  <img
                    src={a.dataUrl}
                    alt={a.name}
                    className="h-12 w-12 rounded-lg object-cover border border-bd/[0.12]"
                  />
                  <button
                    type="button"
                    onClick={() =>
                      setAttachments((prev) => prev.filter((_, j) => j !== i))
                    }
                    className="absolute -top-1.5 -right-1.5 h-4 w-4 rounded-full bg-surface-2 border border-bd/[0.15] text-tx-dim text-[9px] leading-none opacity-0 group-hover/att:opacity-100 transition-opacity"
                    aria-label="Remove attachment"
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
          )}
          {sending && (
            <div className="mb-1.5 flex items-center gap-2 px-1 text-[12px] text-tx-dim">
              <InfinityMark lit active className="h-auto w-4 accent-text" />
              <span className="thinking-shimmer">Working</span>
            </div>
          )}
          {queue.length > 0 && (
            <div className="queue-enter mb-2 overflow-hidden rounded-xl border border-bd/[0.09] bg-surface/70">
              <button
                type="button"
                onClick={() => setQueueOpen((o) => !o)}
                className="flex w-full items-center gap-2 px-3 py-2 text-[12px] text-tx-dim hover:text-tx transition-colors"
              >
                <svg className={`h-3 w-3 transition-transform ${queueOpen ? "" : "-rotate-90"}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M6 9l6 6 6-6" /></svg>
                Message queued {queue.length}
              </button>
              {queueOpen && queue.map((q) => (
                <div key={q.id} className="queue-item-enter flex items-center gap-2 border-t border-bd/[0.06] px-3 py-2">
                  <svg className="h-3.5 w-3.5 flex-none text-tx-mut" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                    <circle cx="9" cy="6" r="1.5" /><circle cx="15" cy="6" r="1.5" />
                    <circle cx="9" cy="12" r="1.5" /><circle cx="15" cy="12" r="1.5" />
                    <circle cx="9" cy="18" r="1.5" /><circle cx="15" cy="18" r="1.5" />
                  </svg>
                  <span className="min-w-0 flex-1 truncate text-[13px] text-tx">{q.text}</span>
                  <button
                    type="button"
                    onClick={() => steer(q.id)}
                    title="Steer: stop the current turn and send this now"
                    className="flex flex-none items-center gap-1 rounded-md px-1.5 py-0.5 text-[12px] text-tx-dim hover:bg-bd/[0.06] hover:text-tx transition-colors"
                  >
                    <svg className="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M5 12h14" /><path d="M13 6l6 6-6 6" /></svg>
                    Steer
                  </button>
                  <button
                    type="button"
                    onClick={() => setQueue((prev) => prev.filter((x) => x.id !== q.id))}
                    title="Remove from queue"
                    aria-label="Remove from queue"
                    className="flex-none rounded-md p-1 text-tx-mut hover:bg-bd/[0.06] hover:text-tx transition-colors"
                  >
                    <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M3 6h18" /><path d="M8 6V4a2 2 0 012-2h4a2 2 0 012 2v2" /><path d="M19 6l-1 14a2 2 0 01-2 2H8a2 2 0 01-2-2L5 6" /></svg>
                  </button>
                </div>
              ))}
            </div>
          )}
          <div
            className={`composer-frame relative rounded-[18px] border border-bd/[0.1] bg-surface p-3 shadow-[0_10px_34px_rgba(0,0,0,0.18)] ${sending ? "composer-frame--active" : ""}`}
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => {
              e.preventDefault();
              if (e.dataTransfer.files.length) {
                addFiles(e.dataTransfer.files);
              }
            }}
          >
            {slashOpen && (
              <div className="composer-popover-enter absolute bottom-full left-0 mb-2 z-30 w-72 rounded-2xl border border-bd/[0.11] material-overlay p-1.5 shadow-2xl shadow-black/50">
                <p className="px-2.5 pt-1 pb-1 text-[11px] text-tx-mut">Commands</p>
                {slashMatches.map((c, i) => (
                  <button
                    key={c.key}
                    type="button"
                    onMouseDown={(e) => {
                      e.preventDefault();
                      runSlash(c);
                    }}
                    className={`w-full flex items-center gap-3 rounded-lg px-2.5 py-2 text-left transition-colors ${i === 0 ? "bg-bd/[0.05]" : "hover:bg-bd/[0.06]"}`}
                  >
                    <span className="text-sm accent-text font-medium w-16">{c.label}</span>
                    <span className="text-[12px] text-tx-dim truncate">{c.hint}</span>
                  </button>
                ))}
                <p className="px-2.5 pt-1 pb-0.5 text-[10px] text-tx-mut">Enter to run · Esc to dismiss</p>
              </div>
            )}
            <textarea
              id={inputId}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onPaste={(e) => {
                const files = Array.from(e.clipboardData.files).filter((f) =>
                  f.type.startsWith("image/"),
                );
                if (files.length) {
                  e.preventDefault();
                  addFiles(files);
                }
              }}
              onKeyDown={(e) => {
                // Slash-command palette takes Enter/Tab/Escape first.
                if (slashOpen) {
                  if (e.key === "Enter" || e.key === "Tab") {
                    e.preventDefault();
                    runSlash(slashMatches[0]);
                    return;
                  }
                  if (e.key === "Escape") {
                    e.preventDefault();
                    setInput("");
                    return;
                  }
                }
                if (e.key !== "Enter" || e.shiftKey) {
                  return;
                }
                // Send-on-Enter (default) vs Ctrl/Cmd+Enter-to-send.
                const wantsSend = sendOnEnter ? true : e.ctrlKey || e.metaKey;
                if (wantsSend) {
                  e.preventDefault();
                  submit();
                }
              }}
              onInput={(e) => {
                const el = e.currentTarget;
                el.style.height = "auto";
                el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
              }}
              rows={1}
              placeholder={composerPlaceholder}
              className="block w-full bg-transparent border-0 focus:outline-none resize-none text-[15px] leading-6 placeholder-tx-mut px-1 text-tx min-h-[24px] max-h-[200px]"
            />
            <div className="composer-toolbar flex items-center justify-between gap-3 pt-3">
              <div className="composer-toolbar__left flex items-center gap-1.5">
                <div className="relative" ref={composerOptionsRef}>
                  <button
                    type="button"
                    onClick={() => setComposerOptionsOpen((open) => !open)}
                    aria-label="Composer options"
                    aria-expanded={composerOptionsOpen}
                    className={`options-trigger h-8 w-8 rounded-full flex items-center justify-center transition-colors ${
                      composerOptionsOpen
                        ? "bg-bd/[0.08] text-tx"
                        : "text-tx-dim hover:bg-bd/[0.06] hover:text-tx"
                    }`}
                  >
                    <svg className="options-trigger__icon h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
                      <path d="M12 5v14M5 12h14" />
                    </svg>
                  </button>
                  {composerOptionsOpen && (
                    <div className="composer-popover-enter absolute bottom-full left-0 z-30 mb-2 w-[300px] rounded-xl border border-bd/[0.1] bg-surface-2 p-2 shadow-2xl shadow-black/40">
                      <div className="composer-toolbar__controls flex flex-wrap items-center gap-1">

              <input
                ref={fileRef}
                type="file"
                accept="image/*"
                multiple
                className="hidden"
                onChange={(e) => {
                  if (e.target.files) {
                    addFiles(e.target.files);
                    e.target.value = "";
                  }
                }}
              />
              <button
                type="button"
                onClick={() => fileRef.current?.click()}
                title="Attach images (or paste / drag-drop)"
                aria-label="Attach images"
                className="light-sweep-control h-8 rounded-lg px-2.5 text-tx-dim hover:bg-bd/[0.06] hover:text-tx transition-colors flex items-center gap-1.5 text-xs"
              >
                <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M21.44 11.05l-9.19 9.19a6 6 0 01-8.49-8.49l9.19-9.19a4 4 0 015.66 5.66l-9.2 9.19a2 2 0 01-2.83-2.83l8.49-8.48" />
                </svg>
                <span className="light-sweep-text">Attach</span>
              </button>
              {/* MCP servers: which external tool providers are wired in and
                  whether they actually came up. Read-only status; wiring and
                  secrets stay in Settings › Tools. */}
              <div className="relative" ref={mcpRef}>
                <button
                  type="button"
                  onClick={() => setMcpMenuOpen((o) => !o)}
                  aria-haspopup="dialog"
                  aria-expanded={mcpMenuOpen}
                  title="MCP servers"
                  aria-label="MCP servers"
                  className={`light-sweep-control h-8 rounded-lg px-2.5 flex items-center gap-1.5 text-xs transition-colors ${
                    mcpMenuOpen
                      ? "accent-text bg-accent/10"
                      : "text-tx-dim hover:bg-bd/[0.06] hover:text-tx"
                  }`}
                >
                  <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                    <rect x="3" y="3" width="7" height="7" rx="1.5" />
                    <rect x="14" y="14" width="7" height="7" rx="1.5" />
                    <path d="M10 6.5h2.5a2 2 0 012 2V14" />
                    <path d="M6.5 10v3.5a2 2 0 002 2H11" />
                  </svg>
                  <span className="light-sweep-text">MCP</span>
                </button>
                {mcpMenuOpen && (
                  <div
                    role="dialog"
                    aria-label="MCP servers"
                    className="fade-in-up absolute bottom-full left-0 z-30 mb-2 w-[min(320px,calc(100vw-48px))] rounded-2xl border border-bd/[0.11] material-overlay p-1.5 shadow-2xl shadow-black/50"
                  >
                    <div className="flex items-center justify-between gap-2 px-2.5 pt-1.5 pb-1">
                      <p className="text-[11px] text-tx-mut">MCP servers</p>
                      {mcpServers && (
                        <span className="font-mono text-[10px] tabular-nums text-tx-mut">
                          {mcpServers.filter((s) => s.connected).length}/{mcpServers.length} connected
                        </span>
                      )}
                    </div>
                    {mcpServers === null ? (
                      <p className="px-2.5 py-2 text-[11px] text-tx-mut">Checking…</p>
                    ) : mcpServers.length === 0 ? (
                      <p className="px-2.5 py-2 text-[11px] leading-relaxed text-tx-mut">
                        No MCP servers configured. Add one in Settings › Tools to bring in
                        tools from other apps.
                      </p>
                    ) : (
                      <div className="max-h-[280px] overflow-y-auto">
                        {mcpServers.map((s) => (
                          <div
                            key={s.name}
                            className="flex items-center gap-2.5 rounded-lg px-2.5 py-1.5"
                          >
                            <span
                              aria-hidden="true"
                              className={`h-1.5 w-1.5 flex-none rounded-full ${
                                !s.enabled
                                  ? "bg-bd/[0.3]"
                                  : s.connected
                                    ? "bg-accent"
                                    : "bg-error"
                              }`}
                            />
                            <span className="min-w-0 flex-1">
                              <span className="block truncate text-[13px] text-tx">{s.name}</span>
                              <span className="block truncate text-[11px] text-tx-mut">
                                {s.detail}
                                {s.connected && s.tools !== null ? ` · ${s.tools} tools` : ""}
                              </span>
                            </span>
                          </div>
                        ))}
                      </div>
                    )}
                    <button
                      type="button"
                      onClick={() => {
                        setMcpMenuOpen(false);
                        onOpenToolsSettings();
                      }}
                      className="mt-1 w-full rounded-lg px-2.5 py-1.5 text-left text-[11px] text-tx-dim hover:bg-bd/[0.06] hover:text-tx transition-colors"
                    >
                      Manage MCP servers
                    </button>
                  </div>
                )}
              </div>
              <button
                type="button"
                onClick={() => setWebOn((w) => !w)}
                title={webOn ? "Web search ON — answers use live web results" : "Enable live web search"}
                aria-label={webOn ? "Web search on" : "Web search off"}
                aria-pressed={webOn}
                className={`light-sweep-control h-8 rounded-lg px-2.5 transition-colors flex items-center gap-1.5 text-xs ${
                  webOn
                    ? "accent-text bg-accent/10"
                    : "text-tx-dim hover:bg-bd/[0.06] hover:text-tx"
                }`}
              >
                <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                  <circle cx="12" cy="12" r="10" />
                  <path d="M2 12h20" />
                  <path d="M12 2a15.3 15.3 0 014 10 15.3 15.3 0 01-4 10 15.3 15.3 0 01-4-10 15.3 15.3 0 014-10z" />
                </svg>
                <span className="light-sweep-text">Web</span>
              </button>
              <div className="relative" ref={toolMenuRef}>
                <button
                  ref={toolButtonRef}
                  type="button"
                  onClick={() => setToolMenuOpen((open) => !open)}
                  aria-expanded={toolMenuOpen}
                  aria-controls="infinity-toolbox"
                  aria-haspopup="dialog"
                  aria-label={`${assistant || toolsOn ? "Tools active" : "Tools off"}. ${enabledToolCount} of ${toolCount} enabled.`}
                  title={`Tools — ${enabledToolCount} of ${toolCount} enabled`}
                  className={`light-sweep-control tool-trigger h-8 rounded-lg px-2.5 flex items-center gap-1.5 text-xs ${
                    assistant || toolsOn || toolMenuOpen
                      ? "accent-text bg-accent/10"
                      : "text-tx-dim hover:bg-bd/[0.06] hover:text-tx"
                  }`}
                >
                  <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M14.7 6.3a4 4 0 00-5.4 5.4L3 18v3h3l6.3-6.3a4 4 0 005.4-5.4l-2.7 2.7-2-2 2.7-2.7z" />
                  </svg>
                  <span className="light-sweep-text">Tools</span>
                  {enabledToolCount > 0 && (
                    <span className="tool-count font-mono" aria-hidden="true">{enabledToolCount}</span>
                  )}
                </button>

                {toolMenuOpen && (
                  <div
                    id="infinity-toolbox"
                    role="dialog"
                    aria-labelledby="infinity-toolbox-title"
                    className="toolbox-popover fade-in-up absolute bottom-full left-0 z-30 mb-2 w-[min(440px,calc(100vw-48px))] overflow-hidden rounded-2xl border border-bd/[0.11] material-overlay"
                  >
                    <div className="toolbox-popover__header">
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <h2 id="infinity-toolbox-title" className="text-sm font-semibold text-tx">
                            Infinity toolbox
                          </h2>
                          <span className="rounded-md bg-bd/[0.06] px-1.5 py-0.5 font-mono text-[10px] tabular-nums text-tx-mut">
                            {toolCatalog ? `${enabledToolCount}/${toolCount}` : "--"} enabled
                          </span>
                        </div>
                        <p className="mt-1 max-w-[34ch] text-xs leading-relaxed text-tx-mut">
                          {assistant
                            ? "Tools are always ready in Assistant. Anything that writes, spends, or uses MCP still asks first."
                            : "Give this chat precise ways to calculate, browse, inspect, and review."}
                        </p>
                      </div>
                      {assistant ? (
                        <span className="toolbox-status toolbox-status--on">
                          <span className="toolbox-status__dot" /> Always on
                        </span>
                      ) : (
                        <button
                          type="button"
                          role="switch"
                          aria-checked={toolsOn}
                          onClick={() => setToolsOn((enabled) => !enabled)}
                          className={`toolbox-switch ${toolsOn ? "toolbox-switch--on" : ""}`}
                        >
                          <span className="toolbox-switch__track" aria-hidden="true">
                            <span className="toolbox-switch__thumb" />
                          </span>
                          <span>{toolsOn ? "On" : "Off"}</span>
                        </button>
                      )}
                    </div>

                    <div className="toolbox-list">
                      {!toolCatalog ? (
                        <div className="space-y-2 p-4" aria-label="Loading tools">
                          <div className="toolbox-skeleton" />
                          <div className="toolbox-skeleton toolbox-skeleton--short" />
                          <div className="toolbox-skeleton" />
                        </div>
                      ) : (
                        Object.entries(
                          toolCatalog.built_in.reduce<Record<string, ToolCatalogEntry[]>>(
                            (groups, tool) => {
                              const group = TOOL_CATEGORY_LABELS[tool.cat] ?? tool.cat;
                              (groups[group] ??= []).push(tool);
                              return groups;
                            },
                            {},
                          ),
                        ).map(([group, tools]) => {
                          const groupEnabled = tools.filter(
                            (t) => !disabledTools.includes(t.id),
                          ).length;
                          return (
                          <section key={group} className="toolbox-group" aria-labelledby={`tool-group-${group.replace(/\W+/g, "-")}`}>
                            <div className="toolbox-group__heading">
                              <h3 id={`tool-group-${group.replace(/\W+/g, "-")}`}>{group}</h3>
                              <span title={`${groupEnabled} of ${tools.length} enabled`}>
                                {groupEnabled}/{tools.length}
                              </span>
                            </div>
                            <div className="space-y-0.5">
                              {tools.map((tool) => {
                                const action = tool.cat.includes("action");
                                const assistantOnly = tool.cat.startsWith("Files") || tool.cat.startsWith("Create");
                                const on = !disabledTools.includes(tool.id);
                                return (
                                  <button
                                    key={tool.id}
                                    type="button"
                                    role="switch"
                                    aria-checked={on}
                                    onClick={() => toggleTool(tool.id)}
                                    title={on ? `${tool.label} is on — click to turn off` : `${tool.label} is off — click to turn on`}
                                    className={`toolbox-tool w-full text-left cursor-pointer hover:bg-bd/[0.05] rounded-lg transition-opacity ${
                                      on ? "" : "opacity-45"
                                    }`}
                                  >
                                    <span className="toolbox-tool__icon" aria-hidden="true">
                                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                                        {TOOL_META[tool.id]?.icon ?? <path d="M12 3v18M3 12h18" />}
                                      </svg>
                                    </span>
                                    <span className="min-w-0 flex-1">
                                      <span className="flex flex-wrap items-center gap-1.5 text-xs font-medium text-tx">
                                        {tool.label}
                                        {assistantOnly && !assistant && <span className="toolbox-tool__badge">Assistant</span>}
                                        {action && <span className="toolbox-tool__badge toolbox-tool__badge--approval">Approval</span>}
                                      </span>
                                      <span className="mt-0.5 block text-[11px] leading-relaxed text-tx-mut">
                                        {TOOL_DESCRIPTIONS[tool.id] ?? "Available when the task needs it."}
                                      </span>
                                    </span>
                                    <span
                                      className={`relative mt-1 h-4 w-7 flex-none rounded-full transition-colors ${
                                        on ? "bg-accent" : "bg-bd/[0.14]"
                                      }`}
                                      aria-hidden="true"
                                    >
                                      <span
                                        className={`absolute top-0.5 left-0.5 h-3 w-3 rounded-full bg-black transition-transform ${
                                          on ? "translate-x-3" : "translate-x-0"
                                        }`}
                                      />
                                    </span>
                                  </button>
                                );
                              })}
                            </div>
                          </section>
                          );
                        })
                      )}

                      {toolCatalog && (
                        <section className="toolbox-group" aria-labelledby="tool-group-mcp">
                          <div className="toolbox-group__heading">
                            <h3 id="tool-group-mcp">Connected MCP</h3>
                            <span>{toolCatalog.counts.mcp}</span>
                          </div>
                          {toolCatalog.mcp.length > 0 ? (
                            <div className="flex flex-wrap gap-1.5 px-1 py-1">
                              {toolCatalog.mcp.slice(0, 30).map((tool) => (
                                <span key={`${tool.server}-${tool.tool}`} className="toolbox-mcp" title={`From ${tool.server}`}>
                                  {tool.tool}
                                </span>
                              ))}
                            </div>
                          ) : (
                            <p className="px-1 py-1 text-[11px] leading-relaxed text-tx-mut">
                              No MCP servers connected. Add one to bring in tools from other apps and services.
                            </p>
                          )}
                        </section>
                      )}
                    </div>

                    <div className="toolbox-popover__footer">
                      <div className="flex items-center gap-1.5 text-[11px] text-tx-mut">
                        <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                          <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
                          <path d="M9 12l2 2 4-4" />
                        </svg>
                        Risky actions require approval
                      </div>
                      <button
                        type="button"
                        onClick={() => {
                          setToolMenuOpen(false);
                          onOpenToolsSettings();
                        }}
                        className="toolbox-manage"
                      >
                        Manage tools
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                          <path d="M5 12h14M13 6l6 6-6 6" />
                        </svg>
                      </button>
                    </div>
                  </div>
                )}
              </div>
              {assistant && (
                <button
                  type="button"
                  onClick={() => setAllowActions((a) => !a)}
                  title={
                    allowActions
                      ? "Actions ON — each write, paid, or MCP call still asks for your approval"
                      : "Allow real-world actions (each step asks for approval before running)"
                  }
                    className={`light-sweep-control h-8 rounded-lg px-2.5 text-xs font-medium transition-colors flex items-center gap-1.5 ${
                    allowActions
                      ? "text-black bg-accent hover:bg-accent-hover"
                      : "text-tx-dim hover:bg-bd/[0.06] hover:text-tx"
                  }`}
                >
                  <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z" />
                  </svg>
                  <span className="light-sweep-text">Actions</span>
                </button>
              )}
              {messages.length > 0 && (
                <button
                  type="button"
                  onClick={exportChat}
                  title="Export chat as Markdown"
                  aria-label="Export chat as Markdown"
                  className="light-sweep-control h-8 rounded-lg px-2.5 text-tx-dim hover:bg-bd/[0.06] hover:text-tx transition-colors flex items-center gap-1.5 text-xs"
                >
                  <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4" />
                    <path d="M7 10l5 5 5-5" />
                    <path d="M12 15V3" />
                  </svg>
                  <span className="light-sweep-text">Export</span>
                </button>
              )}
                        <button
                          type="button"
                          onClick={() => {
                            setComposerOptionsOpen(false);
                            // 2b: the 200+ picker is replaced by auto-dispatch.
                            // A picked persona can be cleared to return to auto.
                            if (activeAgent) setActiveAgent(null);
                          }}
                          className="h-8 rounded-lg px-2.5 text-xs text-tx-dim hover:bg-bd/[0.06] hover:text-tx transition-colors"
                          title="Teams deploy automatically by demand; click a picked persona to clear it"
                        >
                          {activeAgent
                            ? `Persona: ${activeAgent.name}`
                            : `Auto team: ${teamForPrompt(input)}`}
                        </button>
                        <button
                          type="button"
                          onClick={() => setVibe((current) => current === "chat" ? "work" : "chat")}
                          className="h-8 rounded-lg px-2.5 text-xs text-tx-dim hover:bg-bd/[0.06] hover:text-tx transition-colors"
                        >
                          Mode: {vibe === "chat" ? "Chat" : "Work"}
                        </button>
                        <button
                          type="button"
                          onClick={() => setFancy((current) => !current)}
                          className="h-8 rounded-lg px-2.5 text-xs text-tx-dim hover:bg-bd/[0.06] hover:text-tx transition-colors"
                        >
                          Motion: {fancy ? "On" : "Off"}
                        </button>
                      </div>
                    </div>
                  )}
                </div>
                <ApprovalModePicker
                  mode={approvalMode}
                  open={approvalMenuOpen}
                  containerRef={approvalRef}
                  onToggle={() => setApprovalMenuOpen((open) => !open)}
                  onChange={(next) => {
                    setApprovalMode(next);
                    setApprovalMenuOpen(false);
                    if (next === "full") setAllowActions(true);
                  }}
                />
              </div>

              <div className="composer-toolbar__right flex items-center gap-1.5">
                <div className="relative" ref={modelPillRef}>
                  <button
                    type="button"
                    onClick={() => setModelPillOpen((open) => !open)}
                    title="Switch model"
                    aria-haspopup="listbox"
                    aria-expanded={modelPillOpen}
                    className="light-sweep-control h-8 rounded-lg px-2 text-[12px] font-normal text-tx-dim hover:bg-bd/[0.05] hover:text-tx transition-colors flex items-center gap-1"
                  >
                    <span className="light-sweep-text">{currentModel?.label ?? "Model"}</span>
                    <svg className={`h-3 w-3 text-tx-mut transition-transform ${modelPillOpen ? "rotate-180" : ""}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
                      <path d="M6 9l6 6 6-6" />
                    </svg>
                  </button>
                  {modelPillOpen && renderModelMenu(() => setModelPillOpen(false))}
                </div>
                {sending && input.trim().length > 0 && (
                  <button
                    type="button"
                    onClick={steerInput}
                    aria-label="Steer the current response"
                    title="Stop the current response and apply this instruction now"
                    className="light-sweep-control steer-enter h-8 rounded-lg px-2.5 text-[12px] text-tx-dim hover:bg-bd/[0.06] hover:text-tx transition-colors flex items-center gap-1.5"
                  >
                    <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M4 7h11a5 5 0 010 10h-3" />
                      <path d="M7 4L4 7l3 3" />
                    </svg>
                    <span className="light-sweep-text">Steer</span>
                  </button>
                )}
                {sending && (
                  <button
                    type="button"
                    onClick={stopGeneration}
                    aria-label="Stop"
                    title="Stop generating"
                    className="h-9 w-9 rounded-full border border-bd/[0.12] text-tx-dim hover:text-tx hover:bg-bd/[0.06] flex items-center justify-center active:scale-[0.96] transition-all"
                  >
                    <span className="block w-2.5 h-2.5 rounded-[2px] bg-current" />
                  </button>
                )}
                <button
                  type="button"
                  onClick={submit}
                  disabled={input.trim().length === 0}
                  aria-label={sending ? "Queue message" : "Send"}
                  title={sending ? "Queue until the current turn finishes" : "Send"}
                  className="send-control h-9 w-9 rounded-full bg-accent hover:bg-accent-hover text-black flex items-center justify-center disabled:opacity-25 disabled:cursor-not-allowed"
                >
                  <svg
                    className="w-4 h-4 text-black"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2.5"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    <path d="M12 19V5" />
                    <path d="M5 12l7-7 7 7" />
                  </svg>
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
      <AgentPicker
        open={agentPickerOpen}
        activeId={activeAgent?.id ?? null}
        onClose={() => setAgentPickerOpen(false)}
        onPick={pickAgent}
      />
    </div>
  );
}
