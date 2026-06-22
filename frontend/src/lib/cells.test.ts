import { describe, expect, it } from "vitest";
import {
  isValidForType,
  normalizeValue,
  toggleOption,
  unknownSelections,
  valuesEqual,
} from "./cells";

describe("normalizeValue", () => {
  it("single-select stores a 1-element array (WEB_UI-026/-034)", () => {
    expect(normalizeValue("single-select-split", "done")).toEqual(["done"]);
    expect(normalizeValue("single-select-split", ["todo", "done"])).toEqual(["todo"]);
  });
  it("multi-select stores an array, empty when cleared (WEB_UI-032)", () => {
    expect(normalizeValue("multi-select-split", ["a", "b"])).toEqual(["a", "b"]);
    expect(normalizeValue("multi-select-split", "")).toEqual([]);
  });
  it("number coerces and clears (WEB_UI-024)", () => {
    expect(normalizeValue("number", "500")).toBe(500);
    expect(normalizeValue("number", "")).toBeNull();
  });
});

describe("toggleOption", () => {
  it("single replaces selection (WEB_UI-034)", () => {
    expect(toggleOption("single-select-split", ["todo"], "done")).toEqual(["done"]);
  });
  it("multi adds then removes, preserving order (WEB_UI-028/-029)", () => {
    expect(toggleOption("multi-select-split", ["urgent"], "backend")).toEqual([
      "urgent",
      "backend",
    ]);
    expect(toggleOption("multi-select-split", ["urgent", "backend"], "urgent")).toEqual([
      "backend",
    ]);
  });
});

describe("valuesEqual (no-op guard)", () => {
  it("treats empty-ish values as equal (WEB_UI-018)", () => {
    expect(valuesEqual("", null)).toBe(true);
    expect(valuesEqual("v1", "v1")).toBe(true);
    expect(valuesEqual(["a"], ["a"])).toBe(true);
    expect(valuesEqual("v1", "v2")).toBe(false);
  });
});

describe("isValidForType", () => {
  it("rejects non-numeric for number columns (WEB_UI-019)", () => {
    expect(isValidForType("number", "abc")).toBe(false);
    expect(isValidForType("number", "12")).toBe(true);
    expect(isValidForType("number", "")).toBe(true);
  });
});

describe("unknownSelections", () => {
  it("surfaces values not in current options (WEB_UI-033)", () => {
    const opts = { groups: [{ label: "S", options: ["todo", "done"] }] };
    expect(unknownSelections(["archived"], opts)).toEqual(["archived"]);
    expect(unknownSelections(["done"], opts)).toEqual([]);
  });
});
