import { ArrowLeft, Plus } from "lucide-react";
import { useEffect, useState } from "react";
import { fetchChatModels, type ChatModel } from "../hooks/useChats";

/** Model registry surface: lists every model the backend can route to, in the
 *  same compact row language as the chat picker (label + strength + hint). */
export default function ModelsView({ onClose }: { onClose: () => void }): JSX.Element {
  const [models, setModels] = useState<ChatModel[]>([]);
  const [defaultId, setDefaultId] = useState("");
  const [failed, setFailed] = useState(false);
  const [addOpen, setAddOpen] = useState(false);

  useEffect(() => {
    let ok = true;
    void fetchChatModels()
      .then((data) => {
        if (ok) {
          setModels(data.models);
          setDefaultId(data.default);
        }
      })
      .catch(() => {
        if (ok) setFailed(true);
      });
    return () => {
      ok = false;
    };
  }, []);

  return (
    <section className="models-view" aria-labelledby="models-heading">
      <div className="models-content">
        <div className="models-heading-row">
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <button
              type="button"
              className="models-add-button"
              onClick={onClose}
              aria-label="Back to chat"
            >
              <ArrowLeft aria-hidden="true" />
              Back
            </button>
            <h1 id="models-heading">Models</h1>
          </div>
          <button
            type="button"
            className="models-add-button"
            onClick={() => setAddOpen(true)}
          >
            <Plus aria-hidden="true" />
            Add
          </button>
        </div>

        {failed && (
          <p className="mt-4 text-[12px] text-tx-mut">
            Could not reach the backend model list. Is Infinity Code running?
          </p>
        )}

        <div className="mt-4 flex flex-col gap-2">
          {models.map((m) => (
            <div
              key={m.id}
              className="rounded-lg border border-bd/[0.08] bg-surface-2/40 px-4 py-3"
            >
              <div className="flex items-center gap-2">
                <span className="text-[13px] text-tx">{m.label}</span>
                <span
                  className="flex flex-none items-center gap-0.5"
                  title={`Strength ${m.strength ?? 0}/5`}
                >
                  {[1, 2, 3, 4, 5].map((i) => (
                    <span
                      key={i}
                      className={`h-2 w-0.5 rounded-full ${i <= (m.strength ?? 0) ? "bg-accent" : "bg-bd/[0.14]"}`}
                    />
                  ))}
                </span>
                {m.auto && (
                  <span className="rounded border border-bd/[0.12] px-1.5 py-0.5 text-[10px] text-tx-mut">
                    Router
                  </span>
                )}
                {m.local && (
                  <span className="rounded border border-bd/[0.12] px-1.5 py-0.5 text-[10px] text-tx-mut">
                    Local GPU
                  </span>
                )}
                {m.free && (
                  <span className="rounded border border-accent/30 px-1.5 py-0.5 text-[10px] accent-text">
                    Free
                  </span>
                )}
                {m.id === defaultId && (
                  <span className="rounded border border-bd/[0.12] px-1.5 py-0.5 text-[10px] text-tx-mut">
                    Default
                  </span>
                )}
              </div>
              <p className="mt-1 text-[11px] text-tx-mut">{m.hint}</p>
              <p className="mt-0.5 text-[10px] text-tx-mut/70">{m.id}</p>
            </div>
          ))}
        </div>

        {addOpen && (
          <div className="models-dialog-backdrop" role="presentation">
            <div className="models-dialog" aria-labelledby="models-dialog-title">
              <div className="models-dialog__header">
                <h2 id="models-dialog-title">Add models</h2>
                <button type="button" onClick={() => setAddOpen(false)} aria-label="Close add model dialog">
                  ×
                </button>
              </div>
              <p style={{ fontSize: 13, lineHeight: 1.5, color: "#b9b9b9" }}>
                Infinity routes through your provider keys (OpenRouter, DashScope,
                DeepSeek, Moonshot, NVIDIA NIM). Add or edit keys in
                Settings → Providers — every model your keys unlock appears in
                this list and in the chat picker.
              </p>
              <div className="models-dialog__actions">
                <button type="button" onClick={() => setAddOpen(false)}>Close</button>
              </div>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
