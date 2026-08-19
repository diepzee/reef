/**
 * The reef mark and its single-colour glyph.
 *
 * Almost entirely drawing, so what is worth pinning is what a screen reader
 * gets: the mark is meaningful and must announce itself, and the decorative
 * glyph must not — a wordmark read out twice, once as an image and once as
 * text beside it, is worse than one that stays quiet.
 */

import { afterEach, expect, test } from "bun:test";
import { cleanup, render, screen } from "@testing-library/react";

import { FrondGlyph, ReefMark } from "./ReefMark";

afterEach(cleanup);

test("the mark announces itself as reef", () => {
  render(<ReefMark />);
  expect(screen.getByRole("img", { name: "reef" })).toBeDefined();
});

test("the mark takes the size it is given", () => {
  const { container } = render(<ReefMark size={64} />);
  const svg = container.querySelector("svg")!;
  expect(svg.getAttribute("width")).toBe("64");
  expect(svg.getAttribute("height")).toBe("64");
});

test("the mark carries a class through, so layouts can place it", () => {
  const { container } = render(<ReefMark className="side-brand-mark" />);
  expect(container.querySelector(".side-brand-mark")).not.toBeNull();
});

test("the mark has no baked background", () => {
  const { container } = render(<ReefMark />);
  expect(container.querySelector("rect")).toBeNull();
});

test("the frond glyph is decoration and stays out of the reading order", () => {
  const { container } = render(<FrondGlyph color="#0aa" size={26} />);
  const svg = container.querySelector("svg")!;
  // Either hidden or unlabelled — what it must not be is an announced image
  // sitting next to the word it duplicates.
  expect(svg.getAttribute("role")).not.toBe("img");
});

test("the frond glyph paints in the colour it is handed", () => {
  const { container } = render(<FrondGlyph color="#0aa" />);
  expect(container.innerHTML).toContain("#0aa");
});
