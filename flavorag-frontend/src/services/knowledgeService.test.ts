import { beforeEach, describe, expect, it, vi } from "vitest";

const { postMock } = vi.hoisted(() => ({
  postMock: vi.fn(),
}));

vi.mock("./api", () => ({
  api: {
    post: postMock,
  },
}));

import { createKnowledgeBase } from "./knowledgeService";

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
