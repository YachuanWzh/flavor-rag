import { beforeEach, describe, expect, it, vi } from "vitest";

const { postMock } = vi.hoisted(() => ({
  postMock: vi.fn(),
}));

vi.mock("./api", () => ({
  api: {
    post: postMock,
  },
}));

import { createKnowledgeBase, pasteClipboardDocument } from "./knowledgeService";

describe("createKnowledgeBase", () => {
  beforeEach(() => {
    postMock.mockReset();
    postMock.mockResolvedValue({ id: "kb-1" });
  });

  it("lets the server choose the configured embedding model by default", async () => {
    await createKnowledgeBase("flavor-rag");

    const form = postMock.mock.calls[0][1] as FormData;
    expect(form.get("name")).toBe("flavor-rag");
    expect(form.has("embedding_model")).toBe(false);
  });

  it("still sends an explicitly selected embedding model", async () => {
    await createKnowledgeBase("custom", "vendor/custom-embedding");

    const form = postMock.mock.calls[0][1] as FormData;
    expect(form.get("embedding_model")).toBe("vendor/custom-embedding");
  });
});

describe("pasteClipboardDocument", () => {
  beforeEach(() => {
    postMock.mockReset();
    postMock.mockResolvedValue({ id: "doc-1" });
  });

  it("sends pasted content and chunk options as multipart data", async () => {
    await pasteClipboardDocument(
      "kb-1",
      {
        content: "# 标题\n\n正文",
        docName: "产品说明",
        imageReferences: [{
          id: "rich-image-1",
          url: "https://cdn.example.com/diagram.png",
          urls: [
            "https://cdn.example.com/diagram.png",
            "https://backup.example.com/diagram.png",
          ],
          alt: "流程图",
        }],
      },
      { strategy: "SEMANTIC", chunkSize: 600, overlap: 100 },
    );

    expect(postMock.mock.calls[0][0]).toBe("/api/knowledge-base/kb-1/docs/paste");
    const form = postMock.mock.calls[0][1] as FormData;
    expect(form.get("content")).toBe("# 标题\n\n正文");
    expect(form.get("doc_name")).toBe("产品说明");
    expect(form.get("chunk_strategy")).toBe("SEMANTIC");
    expect(form.get("chunk_size")).toBe("600");
    expect(form.get("overlap")).toBe("100");
    expect(JSON.parse(String(form.get("image_references")))).toEqual([{
      id: "rich-image-1",
      url: "https://cdn.example.com/diagram.png",
      urls: [
        "https://cdn.example.com/diagram.png",
        "https://backup.example.com/diagram.png",
      ],
      alt: "流程图",
    }]);
  });
});
