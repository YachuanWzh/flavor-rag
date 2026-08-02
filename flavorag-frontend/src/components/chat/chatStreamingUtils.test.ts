import { describe, expect, it } from "vitest";
import { displayedNeighborEvidenceCount } from "./chatStreamingUtils";

describe("streaming retrieval evidence", () => {
  it("uses the meta count before finish delivers sources", () => {
    expect(displayedNeighborEvidenceCount(undefined, 4)).toBe(4);
  });

  it("uses final sources after finish", () => {
    const sources = [
      { neighborOf: ["anchor"] },
      { neighborOf: [] },
      { neighborOf: ["anchor"] },
    ];

    expect(displayedNeighborEvidenceCount(sources, 4)).toBe(2);
  });
});
