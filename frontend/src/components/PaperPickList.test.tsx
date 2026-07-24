import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PaperPickList } from "./PaperPickList";
import type { Paper } from "../types";

afterEach(() => cleanup());

const papers: Paper[] = [
  {
    id: "doaj:1",
    title: "Hypoxia-Inducible Expression of Annexin A6",
    abstract: "Ein sehr langer Abstract, der die Liste früher auseinandergezogen hat.",
    source: "doaj",
    source_id: "1",
    year: 2022,
    doi: "10.1/a",
    landing_page_url: "https://example.org/a",
    has_full_text: true
  },
  {
    id: "arxiv:2",
    title: "Molecular Chaperone GRP94",
    abstract: "Zweiter Abstract.",
    source: "arxiv",
    source_id: "2",
    year: 2021,
    has_full_text: false
  }
];

describe("PaperPickList", () => {
  it("shows one row per paper with title and source badge, abstract hidden until expanded", () => {
    render(<PaperPickList title="Treffer" papers={papers} onDownload={vi.fn()} />);

    expect(screen.getByText("Hypoxia-Inducible Expression of Annexin A6")).toBeTruthy();
    expect(screen.getByText("DOAJ")).toBeTruthy();
    expect(screen.getByText("arXiv")).toBeTruthy();
    expect(screen.queryByText(/auseinandergezogen/)).toBeNull();

    fireEvent.click(screen.getByText("Hypoxia-Inducible Expression of Annexin A6"));
    expect(screen.getByText(/auseinandergezogen/)).toBeTruthy();
  });

  it("links to the original so a paper without a free PDF stays reachable", () => {
    render(<PaperPickList title="Treffer" papers={papers} onDownload={vi.fn()} />);

    fireEvent.click(screen.getByText("Molecular Chaperone GRP94"));
    // Ohne Landing-Page/DOI gibt es keinen Link — hier prüfen wir das Paper mit Landing-Page.
    expect(screen.queryByText("Original öffnen")).toBeNull();

    fireEvent.click(screen.getByText("Hypoxia-Inducible Expression of Annexin A6"));
    const link = screen.getByRole("link", { name: /Original öffnen/ });
    expect(link.getAttribute("href")).toBe("https://example.org/a");
  });

  it("toggles the whole list with Alle/Keine", () => {
    render(<PaperPickList title="Treffer" papers={papers} onDownload={vi.fn()} />);

    const boxes = () => screen.getAllByRole("checkbox") as HTMLInputElement[];
    expect(boxes().every((box) => !box.checked)).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: "Alle" }));
    expect(boxes().every((box) => box.checked)).toBe(true);
    expect(screen.getByText("2 laden")).toBeTruthy();

    // Der Rückweg fehlte früher: "Alle" war ohne Gegenstück.
    fireEvent.click(screen.getByRole("button", { name: "Keine" }));
    expect(boxes().every((box) => !box.checked)).toBe(true);
  });

  it("downloads the selection, or the full list when nothing is selected", () => {
    const onDownload = vi.fn();
    render(<PaperPickList title="Treffer" papers={papers} onDownload={onDownload} showMetadataAction />);

    fireEvent.click(screen.getByRole("button", { name: /Alle laden/ }));
    expect(onDownload).toHaveBeenLastCalledWith(papers, true);

    fireEvent.click(screen.getAllByRole("checkbox")[1]);
    fireEvent.click(screen.getByRole("button", { name: /1 laden/ }));
    expect(onDownload).toHaveBeenLastCalledWith([papers[1]], true);

    fireEvent.click(screen.getByRole("button", { name: /Metadaten/ }));
    expect(onDownload).toHaveBeenLastCalledWith([papers[1]], false);
  });

  it("collapses the list, which the old Treffer panel could not do", () => {
    render(<PaperPickList title="Treffer" papers={papers} onDownload={vi.fn()} />);

    const toggle = screen.getByRole("button", { name: /Treffer/ });
    fireEvent.click(toggle);
    expect(screen.queryByText("Molecular Chaperone GRP94")).toBeNull();
    expect(screen.queryByRole("button", { name: "Alle" })).toBeNull();

    fireEvent.click(toggle);
    expect(screen.getByText("Molecular Chaperone GRP94")).toBeTruthy();
  });
});
