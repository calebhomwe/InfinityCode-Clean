import { useEffect, useState } from "react";
import Markdown from "react-markdown";
import { apiFetch, API_BASE  } from "../lib/api";

const TOPICS = ["all", "reference", "blender", "godot", "comfyui", "webdev", "general"] as const;
type TopicFilter = (typeof TOPICS)[number];

export interface Skill {
  name: string;
  topic: string;
  source_url: string | null;
  verified: boolean;
  success_rate: number;
  steps_count: number;
  last_used: string | null;
}

export default function VaultBrowser(): JSX.Element {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState<string>("");
  const [topicFilter, setTopicFilter] = useState<TopicFilter>("all");
  const [expandedName, setExpandedName] = useState<string | null>(null);
  const [content, setContent] = useState<Record<string, string>>({});
  const [copied, setCopied] = useState<string | null>(null);

  const toggle = (name: string): void => {
    const next = expandedName === name ? null : name;
    setExpandedName(next);
    if (next && content[next] === undefined) {
      setContent((c) => ({ ...c, [next]: "" })); // mark loading
      void apiFetch(`${API_BASE}/skills/${encodeURIComponent(next)}`)
        .then((r) => (r.ok ? r.json() : null))
        .then((d) => {
          const body =
            (d && (d.content as string)) ||
            (d && Array.isArray(d.steps) && d.steps.length
              ? d.steps.map((s: unknown) => (typeof s === "string" ? s : JSON.stringify(s))).join("\n")
              : "");
          setContent((c) => ({ ...c, [next]: body || "_(no content)_" }));
        })
        .catch(() => setContent((c) => ({ ...c, [next]: "_(could not load)_" })));
    }
  };

  useEffect(() => {
    let mounted = true;
    const fetchSkills = async (): Promise<void> => {
      try {
        const response = await apiFetch(`${API_BASE}/skills`);
        if (!response.ok) {
          throw new Error(`HTTP ${response.status} from /skills`);
        }
        const data = (await response.json()) as Skill[];
        if (mounted) {
          setSkills(data);
          setError(null);
        }
      } catch (err) {
        if (mounted) {
          setError(
            err instanceof Error ? err.message : "Failed to fetch skills"
          );
        }
      } finally {
        if (mounted) {
          setLoading(false);
        }
      }
    };
    void fetchSkills();
    return () => {
      mounted = false;
    };
  }, []);

  const visible = skills.filter((skill) => {
    const matchesTopic =
      topicFilter === "all" ||
      skill.topic.toLowerCase().includes(topicFilter);
    const matchesSearch =
      search.trim() === "" ||
      skill.name.toLowerCase().includes(search.trim().toLowerCase());
    return matchesTopic && matchesSearch;
  });

  return (
    <div>
      <div className="flex gap-2 mb-3">
        <input
          type="text"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search skills..."
          className="flex-1 rounded-lg bg-surface border border-bd/[0.08] px-3 py-2 text-sm text-tx placeholder-tx-mut focus:outline-none focus:border-accent/50"
        />
        <select
          value={topicFilter}
          onChange={(event) => setTopicFilter(event.target.value as TopicFilter)}
          className="rounded-lg bg-surface border border-bd/[0.08] px-3 py-2 text-sm text-tx focus:outline-none focus:border-accent/50"
        >
          {TOPICS.map((topic) => (
            <option key={topic} value={topic}>
              {topic}
            </option>
          ))}
        </select>
      </div>

      {loading && (
        <div className="space-y-2">
          <div className="skeleton h-12" />
          <div className="skeleton h-12" />
        </div>
      )}
      {error && (
        <div className="rounded-lg border border-error/20 bg-error/[0.06] text-error text-sm p-3">
          {error}
        </div>
      )}
      {!loading && !error && visible.length === 0 && (
        <p className="text-sm text-tx-mut">
          No skills yet. Ingest a tutorial to teach the system something.
        </p>
      )}

      <ul className="space-y-2">
        {visible.map((skill) => {
          const expanded = expandedName === skill.name;
          return (
            <li
              key={skill.name}
              role="button"
              tabIndex={0}
              aria-expanded={expanded}
              className="rounded-lg border border-bd/[0.06] bg-surface hover:border-bd/[0.12] p-3 cursor-pointer transition-colors focus-visible:border-accent/60"
              onClick={() => toggle(skill.name)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  toggle(skill.name);
                }
              }}
            >
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium text-tx truncate">
                  {skill.name}
                </span>
                <span className="text-[10px] px-2 py-0.5 rounded-full bg-bd/[0.06] text-tx-dim shrink-0">
                  {skill.topic}
                </span>
                {skill.verified && (
                  <span className="text-success text-xs shrink-0" title="Verified">
                    
                  </span>
                )}
              </div>
              {skill.topic !== "reference" && (
                <div className="mt-2 h-1 rounded-full bg-bd/[0.06] overflow-hidden">
                  <div
                    className="h-full rounded-full bg-accent"
                    style={{ width: `${Math.min(100, skill.success_rate * 100)}%` }}
                  />
                </div>
              )}
              {expanded && (
                <div className="mt-3 pt-3 border-t border-bd/[0.06]" onClick={(e) => e.stopPropagation()}>
                  {skill.source_url && (
                    <p className="text-xs text-tx-mut truncate mb-2">
                      Source:{" "}
                      <a href={skill.source_url} target="_blank" rel="noreferrer" className="accent-text hover:underline">
                        {skill.source_url}
                      </a>
                    </p>
                  )}
                  {content[skill.name] === undefined || content[skill.name] === "" ? (
                    <div className="text-xs text-tx-mut py-2">Loading…</div>
                  ) : (
                    <>
                      <div className="flex justify-end mb-1">
                        <button
                          type="button"
                          onClick={() => {
                            void navigator.clipboard.writeText(content[skill.name]);
                            setCopied(skill.name);
                            setTimeout(() => setCopied(null), 1500);
                          }}
                          className="text-[11px] text-tx-dim hover:text-tx rounded-md border border-bd/[0.1] px-2 py-1 transition-colors"
                        >
                          {copied === skill.name ? "Copied" : "Copy"}
                        </button>
                      </div>
                      <div className="chat-markdown max-h-[46vh] overflow-auto text-[13px] text-tx-dim leading-relaxed rounded-lg bg-bg border border-bd/[0.06] p-3">
                        <Markdown>{content[skill.name]}</Markdown>
                      </div>
                    </>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
