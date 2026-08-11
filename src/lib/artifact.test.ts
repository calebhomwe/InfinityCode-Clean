import { describe, it, expect } from "vitest";
import { extractArtifact, toSrcDoc, type Artifact } from "./artifact";

describe("extractArtifact", () => {
  it("returns null for empty input", () => {
    expect(extractArtifact("")).toBeNull();
    expect(extractArtifact("   ")).toBeNull();
  });

  it("returns null for plain text with no code fences", () => {
    expect(extractArtifact("Hello world, just a normal message.")).toBeNull();
  });

  it("extracts an HTML fenced block", () => {
    const content = "Here is the page:\n```html\n<!doctype html><html><body><h1>Hi</h1></body></html>\n```\n";
    const result = extractArtifact(content);
    expect(result).not.toBeNull();
    expect(result!.kind).toBe("html");
    expect(result!.code).toContain("<h1>Hi</h1>");
    expect(result!.title).toBe("HTML page");
  });

  it("extracts an SVG fenced block", () => {
    const content = "```svg\n<svg xmlns='http://www.w3.org/2000/svg'><circle r='10'/></svg>\n```";
    const result = extractArtifact(content);
    expect(result).not.toBeNull();
    expect(result!.kind).toBe("svg");
    expect(result!.code).toContain("<circle");
  });

  it("detects SVG inside a generic fenced block", () => {
    const content = "```xml\n<svg xmlns='http://www.w3.org/2000/svg'><rect width='100' height='100'/></svg>\n```";
    const result = extractArtifact(content);
    expect(result).not.toBeNull();
    expect(result!.kind).toBe("svg");
  });

  it("detects unfenced HTML document", () => {
    const content = "<!doctype html><html><body><p>Hello</p></body></html>";
    const result = extractArtifact(content);
    expect(result).not.toBeNull();
    expect(result!.kind).toBe("html");
    expect(result!.code).toContain("<p>Hello</p>");
  });

  it("detects unfenced SVG", () => {
    const content = "<svg xmlns='http://www.w3.org/2000/svg'><text>Hi</text></svg>";
    const result = extractArtifact(content);
    expect(result).not.toBeNull();
    expect(result!.kind).toBe("svg");
  });

  it("detects HTML with div+style pattern in generic fence", () => {
    const content = "```\n<div class='app'><style>.app{color:red}</style></div>\n```";
    const result = extractArtifact(content);
    expect(result).not.toBeNull();
    expect(result!.kind).toBe("html");
  });

  it("prefers HTML fence over generic detection", () => {
    const content = "```html\n<div>test</div>\n```";
    const result = extractArtifact(content);
    expect(result).not.toBeNull();
    expect(result!.kind).toBe("html");
  });
});

describe("toSrcDoc", () => {
  it("wraps SVG in a full HTML document with dark background", () => {
    const artifact: Artifact = {
      kind: "svg",
      code: "<svg><circle r='5'/></svg>",
      title: "SVG graphic",
    };
    const doc = toSrcDoc(artifact);
    expect(doc).toContain("<!doctype html>");
    expect(doc).toContain("background:#0a0a0b");
    expect(doc).toContain("<svg><circle r='5'/></svg>");
  });

  it("passes through HTML that already has a doctype", () => {
    const artifact: Artifact = {
      kind: "html",
      code: "<!doctype html><html><body>Test</body></html>",
      title: "HTML page",
    };
    expect(toSrcDoc(artifact)).toBe(artifact.code);
  });

  it("wraps bare HTML fragments in a document", () => {
    const artifact: Artifact = {
      kind: "html",
      code: "<h1>Hello</h1>",
      title: "HTML page",
    };
    const doc = toSrcDoc(artifact);
    expect(doc).toContain("<!doctype html>");
    expect(doc).toContain("<h1>Hello</h1>");
  });

  it("passes through HTML starting with <html", () => {
    const artifact: Artifact = {
      kind: "html",
      code: "<html><body>Bare</body></html>",
      title: "HTML page",
    };
    expect(toSrcDoc(artifact)).toBe(artifact.code);
  });
});
