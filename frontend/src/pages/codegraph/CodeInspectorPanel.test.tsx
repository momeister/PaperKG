import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CodeInspectorPanel } from "./CodeInspectorPanel";
import type { CodeBlueprint, CodeFacts, CodeNodeDetail } from "../../types";

/**
 * Der Inspektor sagt, was ein Symbol tut — und wie sicher das ist. Drei Dinge
 * dürfen hier nicht verrutschen:
 *
 *   * `weakest_edge: "guessed"` heisst, dass die Aufruferzahl auf geratenen
 *     Kanten steht. Eine nackte Zahl wäre dann eine Behauptung.
 *   * `pure: null` heisst „unentschieden", nicht „nein" — es darf keine
 *     „nebenwirkungsfrei"-Pille erscheinen.
 *   * `stale` heisst, die Zeilennummern zeigen ins Leere. Das muss man sehen.
 */
vi.mock("../../api", () => ({
  api: { codegraph: { blueprint: vi.fn(), source: vi.fn() } },
}));

const { api } = await import("../../api");

function facts(extra: Partial<CodeFacts> = {}): CodeFacts {
  return {
    signature: "def apply_discount(total, pct)",
    params: [{ name: "total", type_name: "float", default: null }],
    returns: "float",
    throws: [],
    side_effects: [],
    complexity: 2,
    loc: 2,
    max_nesting: 1,
    callers: 17,
    callees: 0,
    ...extra,
  };
}

function detail(extra: Partial<CodeNodeDetail> = {}): CodeNodeDetail {
  return {
    id: "a1b2c3d4e5f60718",
    name: "apply_discount",
    qualified: "pricing.apply_discount",
    kind: "function",
    lang: "python",
    path: "src/pricing.py",
    span: { start_byte: 0, end_byte: 60, start_line: 1, end_line: 2 },
    parent: null,
    doc: null,
    facts: facts(),
    metrics: {
      pagerank: 0.0123,
      fan_in: 17,
      fan_out: 0,
      reach_depth: 3,
      churn: 4,
      risk: 1,
      authors: 2,
      last_touched: null,
      coverage: null,
      hits: null,
      relevance: 0.62,
    },
    ...extra,
  };
}

function blueprint(focus: CodeNodeDetail): CodeBlueprint {
  return { focus, callers: [], callees: [], children: [] };
}

function renderInspector() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <CodeInspectorPanel
        projectId="cp_1"
        nodeId="a1b2c3d4e5f60718"
        onOpen={() => {}}
        onFocus={() => {}}
        onShowOnMap={() => {}}
        onPathTo={() => {}}
      />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.mocked(api.codegraph.source).mockResolvedValue({
    path: "src/pricing.py",
    text: "def apply_discount(total, pct):\n    return total * (1 - pct)\n",
    stale: false,
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("CodeInspectorPanel", () => {
  it("schreibt die Aufruferzahl nie nackt hin, wenn der schwächste Beleg geraten ist", async () => {
    vi.mocked(api.codegraph.blueprint).mockResolvedValue(
      blueprint(detail({ facts: facts({ weakest_edge: "guessed" }) })),
    );
    renderInspector();

    await waitFor(() => expect(screen.getByLabelText("Aufrufer")).toBeTruthy());
    expect(screen.getByLabelText("Aufrufer").textContent).toContain("vermutet");
    expect(screen.getByText(/stützt sich auch auf geratene Kanten/)).toBeTruthy();
  });

  it("lässt die Fussnote weg, wenn jede Kante belegt ist", async () => {
    vi.mocked(api.codegraph.blueprint).mockResolvedValue(
      blueprint(detail({ facts: facts({ weakest_edge: "verified" }) })),
    );
    renderInspector();

    await waitFor(() => expect(screen.getByLabelText("Aufrufer")).toBeTruthy());
    expect(screen.queryByText(/stützt sich auch auf geratene Kanten/)).toBeNull();
  });

  it("nennt `pure: null` nicht nebenwirkungsfrei", async () => {
    // Option<bool> heisst hier ausdrücklich „konnte nicht entschieden werden".
    vi.mocked(api.codegraph.blueprint).mockResolvedValue(
      blueprint(detail({ facts: facts({ pure: null }) })),
    );
    renderInspector();

    await waitFor(() => expect(screen.getByText("Was hinein- und herausgeht")).toBeTruthy());
    expect(screen.queryByText("nebenwirkungsfrei")).toBeNull();
    expect(screen.getByText(/Statisch nicht entscheidbar/)).toBeTruthy();
  });

  it("stellt Eingang und Ausgang getrennt dar", async () => {
    vi.mocked(api.codegraph.blueprint).mockResolvedValue(
      blueprint(detail({ facts: facts({ returns: "float", throws: ["ValueError"] }) })),
    );
    renderInspector();

    await waitFor(() => expect(screen.getByText("hinein")).toBeTruthy());

    // Getrennt heisst getrennt: der Parametertyp steht links, der Rückgabetyp
    // rechts. Beide heissen hier „float" — genau deshalb wird je Seite geprüft
    // und nicht global gesucht.
    const inSide = document.querySelector(".cgp-flow-side--in")!;
    const outSide = document.querySelector(".cgp-flow-side--out")!;
    expect(inSide.textContent).toContain("total");
    expect(inSide.textContent).toContain("float");
    expect(outSide.textContent).toContain("float");
    expect(outSide.textContent).not.toContain("total");
    expect(outSide.textContent).toContain("wirft ValueError");
  });

  it("sagt auch ohne Docstring, wofür das Symbol da ist", async () => {
    // Der Fall, der vorher eine leere Fläche war: kein Kommentar im Code.
    vi.mocked(api.codegraph.blueprint).mockResolvedValue(blueprint(detail({ doc: null })));
    renderInspector();

    await waitFor(() => expect(screen.getByText(/kein Kommentar zu diesem Symbol/)).toBeTruthy());
    expect(screen.getByText(/Laut Index/)).toBeTruthy();
  });

  it("nennt `pure: true` nebenwirkungsfrei", async () => {
    vi.mocked(api.codegraph.blueprint).mockResolvedValue(
      blueprint(detail({ facts: facts({ pure: true }) })),
    );
    renderInspector();
    await waitFor(() => expect(screen.getByText("nebenwirkungsfrei")).toBeTruthy());
  });

  it("warnt, wenn die Datei sich seit dem Indizieren geändert hat", async () => {
    vi.mocked(api.codegraph.blueprint).mockResolvedValue(blueprint(detail()));
    vi.mocked(api.codegraph.source).mockResolvedValue({
      path: "src/pricing.py",
      text: "# alles anders\n",
      stale: true,
    });
    renderInspector();

    await waitFor(() =>
      expect(screen.getByText(/Zeilennummern im Graphen passen nicht mehr/)).toBeTruthy(),
    );
  });
});
