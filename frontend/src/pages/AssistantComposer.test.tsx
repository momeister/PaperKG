import { createRef, useState } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AssistantComposer, type AssistantComposerHandle } from "./AssistantComposer";
import { AnswerText } from "./AnswerText";
import { api } from "../api";
import { citationIds, citationMetasFor, isSentenceBoundary } from "./assistantHelpers";
import type { VerificationSource, CitationLink } from "../types";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("keeps keystrokes in the composer and preserves a new draft during a pending answer", () => {
  let parentRenders = 0;
  const submitted = vi.fn();
  const ref = createRef<AssistantComposerHandle>();
  function Workspace() {
    parentRenders++;
    const [pending, setPending] = useState(false);
    return <form onSubmit={event => { event.preventDefault(); ref.current?.submit(); }}>
      <AssistantComposer ref={ref} papers={[]} disabled={pending} onSelectPaper={() => {}} onSubmit={text => { submitted(text); ref.current?.setDraft(""); setPending(true); }} />
    </form>;
  }
  render(<Workspace />);
  const input = screen.getByPlaceholderText(/Frage stellen/);
  for (const value of ["a", "ab", "abc"]) fireEvent.change(input, { target: { value } });
  expect(parentRenders).toBe(1);
  fireEvent.click(screen.getByRole("button", { name: "Senden" }));
  expect(submitted).toHaveBeenCalledWith("abc");
  fireEvent.change(input, { target: { value: "next draft" } });
  expect(input).toHaveValue("next draft");
  expect(parentRenders).toBe(2);
  expect(screen.getByRole("button", { name: "Senden" })).toBeDisabled();
});

it("does not interpret IME Enter as command submission", () => {
  const submit = vi.fn();
  render(<AssistantComposer papers={[]} disabled={false} onSelectPaper={() => {}} onSubmit={submit} />);
  const input = screen.getByPlaceholderText(/Frage stellen/);
  fireEvent.change(input, { target: { value: "/new" } });
  fireEvent.keyDown(input, { key: "Enter", isComposing: true });
  expect(submit).not.toHaveBeenCalled();
});

it("keeps separate drafts when switching assistant sessions", () => {
  const props = { papers: [], disabled: false, onSelectPaper: () => {}, onSubmit: vi.fn() };
  const { rerender } = render(<AssistantComposer {...props} sessionId="session-one" />);
  const input = screen.getByPlaceholderText(/Frage stellen/);
  fireEvent.change(input, { target: { value: "first draft" } });
  rerender(<AssistantComposer {...props} sessionId="session-two" />);
  expect(input).toHaveValue("");
  fireEvent.change(input, { target: { value: "second draft" } });
  rerender(<AssistantComposer {...props} sessionId="session-one" />);
  expect(input).toHaveValue("first draft");
  rerender(<AssistantComposer {...props} sessionId="session-two" />);
  expect(input).toHaveValue("second draft");
});

it("resolves comma-containing IDs by stable occurrence and evidence IDs", () => {
  const pid = "Paper, with, commas";
  const source: VerificationSource = { paper_id: pid, title: "T", pdf_available: true, evidence: ["e1", "e2"].map(evidence_id => ({ paper_id: pid, evidence_id, kind: "passage", reference_text: "Original passage.", pdf_excerpt: "Original passage.", found_in_pdf_text: true, matched_terms: [] })) };
  const links: CitationLink[] = ["e2","e1","e2"].map(evidence_id => ({ claim_id: "claim-2", citation: pid, paper_id: pid, evidence_id, citation_start: 24, citation_end: 45, confidence: "high" }));
  expect(citationIds(pid, [pid])).toEqual([pid]);
  expect(citationIds(`${pid}; p2`, [pid,"p2"])).toEqual([pid,"p2"]);
  expect(citationMetasFor([source], pid, "irrelevant prior sentence", links, 24).map(m => m.evidenceId)).toEqual(["e2","e1"]);
  expect(citationMetasFor([source], pid, "Original passage", links, 0)).toEqual([]);
});

it("opening uncertain legacy citations never starts a model check", async () => {
  const check = vi.spyOn(api, "claimCheck");
  const source: VerificationSource = { paper_id: "p", title: "T", pdf_available: true, evidence: [{ paper_id:"p", kind:"claim", reference_text:"Legacy", pdf_excerpt:"", matched_terms:[], found_in_pdf_text:false }] };
  render(<AnswerText answer="Legacy claim [p]." citationLinks={[]} onCitationClick={() => {}} getCitationMeta={() => ({source, evidenceIndex:0, approximate:true, confidence:"low"})} />);
  await Promise.resolve();
  expect(check).not.toHaveBeenCalled();
});

it("keeps German abbreviations and decimal fractions in their sentence", () => {
  const text = "Dies gilt z. B. bei 4,7 Prozent. Danach folgt ein neuer Satz.";
  expect(isSentenceBoundary(text, text.indexOf("z.")+1)).toBe(false);
  expect(isSentenceBoundary(text, text.indexOf("B.")+1)).toBe(false);
  expect(isSentenceBoundary(text, text.indexOf("Prozent.")+7)).toBe(true);
});


it("highlights the complete bound claim without borrowing adjacent sentences", () => {
  const context = "Dies gilt z. B. für RA. Die Einschränkung bleibt wichtig.";
  const answer = `Vorherige Aussage ohne diesen Beleg.\n\n${context} [p]\n\nNächste Aussage.`;
  const source: VerificationSource = { paper_id:"p", title:"T", pdf_available:true, evidence:[{paper_id:"p",evidence_id:"e",kind:"passage",reference_text:"Original",pdf_excerpt:"Original",matched_terms:[],found_in_pdf_text:true}] };
  const links: CitationLink[] = [{claim_id:"c",paper_id:"p",citation:"p",evidence_id:"e",citation_start:answer.indexOf("[p]"),citation_end:answer.indexOf("[p]")+3,context,verification_status:"supported"}];
  const {container} = render(<AnswerText answer={answer} citationLinks={links} onCitationClick={() => {}} getCitationMeta={() => ({source,evidenceIndex:0,verificationStatus:"supported"})} />);
  fireEvent.click(container.querySelector(".citation-link")!);
  expect(container.querySelector(".citation-context-highlight")?.textContent).toBe(context);
});


it("preserves a command draft when an answer is still pending", () => {
  const submit = vi.fn();
  render(<AssistantComposer papers={[]} disabled onSelectPaper={() => {}} onSubmit={submit} />);
  const input = screen.getByPlaceholderText(/Frage stellen/);
  fireEvent.change(input, {target:{value:"/help"}});
  fireEvent.keyDown(input, {key:"Enter"});
  expect(input).toHaveValue("/help");
  expect(submit).not.toHaveBeenCalled();
});
