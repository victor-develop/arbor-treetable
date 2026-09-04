import { describe, expect, it } from "vitest";
import { slugifyLabel, uniqueField } from "./columns";

describe("slugifyLabel", () => {
  it("lowercases and collapses non-alphanumeric runs to _", () => {
    expect(slugifyLabel("Due Date")).toBe("due_date");
    expect(slugifyLabel("Cost ($ USD)")).toBe("cost_usd");
    expect(slugifyLabel("  spaced   out  ")).toBe("spaced_out");
  });

  it("trims leading/trailing underscores", () => {
    expect(slugifyLabel("!urgent!")).toBe("urgent");
  });

  it("returns empty string when nothing survives", () => {
    expect(slugifyLabel("🎉🎉")).toBe("");
  });
});

describe("uniqueField", () => {
  it("uses the plain slug when free", () => {
    expect(uniqueField(["a", "b"], "Due Date")).toBe("due_date");
  });

  it("suffixes _2, _3… on collision", () => {
    expect(uniqueField(["due_date"], "Due Date")).toBe("due_date_2");
    expect(uniqueField(["due_date", "due_date_2"], "Due Date")).toBe("due_date_3");
  });

  it("falls back to 'column' for an all-symbol label", () => {
    expect(uniqueField([], "🎉")).toBe("column");
    expect(uniqueField(["column"], "🎉")).toBe("column_2");
  });
});
