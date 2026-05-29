import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MetricCard } from "./MetricCard";

describe("MetricCard", () => {
  it("renders a metric label and value", () => {
    render(<MetricCard label="Papers" value={25} tone="blue" />);

    expect(screen.getByLabelText("Papers")).toBeInTheDocument();
    expect(screen.getByText("25")).toBeInTheDocument();
  });
});
