import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "./api";

describe("api client", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("loads projects from the product API", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ projects: [{ id: "demo", name: "demo", paper_ids: [], paper_count: 0 }] }), {
        status: 200,
        headers: { "content-type": "application/json" }
      })
    );

    const result = await api.getProjects();

    expect(result.projects[0].id).toBe("demo");
    expect(fetchMock.mock.calls[0][0]).toContain("/projects");
  });

  it("appends markdown to a note", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ note: { id: "n1", project_id: "demo", title: "Note", markdown: "Text" } }), {
        status: 200,
        headers: { "content-type": "application/json" }
      })
    );

    const result = await api.appendNote("n1", { markdown: "Text" });

    expect(result.note.id).toBe("n1");
    expect(fetchMock.mock.calls[0][0]).toContain("/notes/n1/append");
    expect(fetchMock.mock.calls[0][1]?.method).toBe("POST");
  });

  it("deletes note AI threads via POST action routes", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ deleted: true }), {
        status: 200,
        headers: { "content-type": "application/json" }
      })
    );

    await api.deleteNoteAiThread("n1", "thread-1");

    expect(fetchMock.mock.calls[0][0]).toContain("/notes/n1/ai-threads/thread-1/delete");
    expect(fetchMock.mock.calls[0][1]?.method).toBe("POST");
  });
});
