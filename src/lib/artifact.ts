/**
 * Detects a renderable "design" artifact inside an assistant message — a
 * self-contained HTML page or an SVG — so it can be shown live in the
 * Infinity Design panel (the app's take on Claude Artifacts).
 */

export type ArtifactKind = "html" | "svg";

export interface Artifact {
  kind: ArtifactKind;
  code: string;
  title: string;
}

const HTML_FENCE = /```(?:html|htm)\s*\n([\s\S]*?)```/i;
const SVG_FENCE = /```(?:svg|xml)\s*\n([\s\S]*?)```/i;
const ANY_FENCE = /```[a-z]*\s*\n([\s\S]*?)```/i;

function looksLikeHtmlDoc(text: string): boolean {
  const t = text.trim().toLowerCase();
  return (
    t.startsWith("<!doctype html") ||
    t.startsWith("<html") ||
    (t.includes("<body") && t.includes("</body>")) ||
    (t.includes("<div") && t.includes("<style"))
  );
}

function looksLikeSvg(text: string): boolean {
  const t = text.trim().toLowerCase();
  return t.startsWith("<svg") && t.includes("</svg>");
}

/** Extract the first renderable artifact from raw markdown, or null. */
export function extractArtifact(content: string): Artifact | null {
  if (!content) {
    return null;
  }

  const html = content.match(HTML_FENCE);
  if (html && html[1].trim()) {
    return { kind: "html", code: html[1].trim(), title: "HTML page" };
  }

  const svg = content.match(SVG_FENCE);
  if (svg && svg[1].trim()) {
    return { kind: "svg", code: svg[1].trim(), title: "SVG graphic" };
  }

  // A generic fenced block whose contents look like HTML/SVG.
  const any = content.match(ANY_FENCE);
  if (any && any[1].trim()) {
    const code = any[1].trim();
    if (looksLikeSvg(code)) {
      return { kind: "svg", code, title: "SVG graphic" };
    }
    if (looksLikeHtmlDoc(code)) {
      return { kind: "html", code, title: "HTML page" };
    }
  }

  // Unfenced full HTML/SVG document.
  if (looksLikeSvg(content)) {
    return { kind: "svg", code: content.trim(), title: "SVG graphic" };
  }
  if (looksLikeHtmlDoc(content)) {
    return { kind: "html", code: content.trim(), title: "HTML page" };
  }

  return null;
}

/** Wrap an artifact into a full HTML document suitable for an iframe srcDoc. */
export function toSrcDoc(artifact: Artifact): string {
  if (artifact.kind === "svg") {
    return `<!doctype html><html><head><meta charset="utf-8"><style>html,body{margin:0;height:100%;display:grid;place-items:center;background:#0a0a0b}</style></head><body>${artifact.code}</body></html>`;
  }
  const code = artifact.code;
  const hasDoctype = /^\s*<!doctype/i.test(code) || /^\s*<html/i.test(code);
  return hasDoctype ? code : `<!doctype html><html><head><meta charset="utf-8"></head><body>${code}</body></html>`;
}
