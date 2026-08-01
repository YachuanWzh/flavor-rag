import { describe, expect, it } from "vitest";
import { parseApiDateTime } from "./dateTime";

describe("parseApiDateTime", () => {
  it("treats legacy naive API timestamps as UTC", () => {
    expect(parseApiDateTime("2026-08-01 04:45:31")?.toISOString()).toBe(
      "2026-08-01T04:45:31.000Z",
    );
  });

  it("preserves explicit UTC timestamps", () => {
    expect(parseApiDateTime("2026-08-01T04:45:31Z")?.toISOString()).toBe(
      "2026-08-01T04:45:31.000Z",
    );
  });

  it("normalizes explicit offsets without applying a second conversion", () => {
    expect(parseApiDateTime("2026-08-01T12:45:31+08:00")?.toISOString()).toBe(
      "2026-08-01T04:45:31.000Z",
    );
  });
});
