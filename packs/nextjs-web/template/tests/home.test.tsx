// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import Home from "@/app/page";

describe("home page", () => {
  it("has one main landmark and a top-level heading", () => {
    render(<Home />);
    expect(screen.getByRole("main")).toBeTruthy();
    expect(screen.getByRole("heading", { level: 1 })).toBeTruthy();
  });
});
