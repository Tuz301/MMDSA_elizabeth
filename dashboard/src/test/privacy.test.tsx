/**
 * The dashboard's own privacy and error contracts.
 *
 * The important one is the first: a record whose identifier fields the
 * server pruned must render as its programme code, not as a blank — because
 * for an LGA coordinator or state manager that is the normal, designed view.
 */

import { describe, expect, it } from "vitest";

import { ApiError } from "../api/client";
import { deadlineLabel, displayName } from "../lib/format";

describe("displayName", () => {
  it("falls back to the programme code when the server pruned the name", () => {
    expect(displayName({}, "CL-0001")).toBe("CL-0001");
  });

  it("uses the name when the role may read it", () => {
    expect(displayName({ full_name: "Amina" }, "CL-0001")).toBe("Amina");
  });

  it("uses an infant given name when present", () => {
    expect(displayName({ given_name: "Baby" }, "B2345678")).toBe("Baby");
  });
});

describe("ApiError normalisation shape", () => {
  it("keeps field errors addressable for inline form display", () => {
    const error = new ApiError(400, "consent_given_at: required", {
      consent_given_at: ["required"],
    });
    expect(error.fieldErrors.consent_given_at).toEqual(["required"]);
    expect(error.status).toBe(400);
  });
});

describe("deadlineLabel", () => {
  const now = new Date("2026-09-08T12:00:00Z");

  it("counts down to a future deadline", () => {
    expect(deadlineLabel("2026-09-08T15:00:00Z", now)).toBe("3 h left");
  });

  it("counts up past a missed deadline", () => {
    expect(deadlineLabel("2026-09-08T07:00:00Z", now)).toBe("5 h overdue");
  });

  it("switches to days when the horizon is long", () => {
    expect(deadlineLabel("2026-09-12T12:00:00Z", now)).toBe("4 d left");
  });
});
