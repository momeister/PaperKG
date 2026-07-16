"""Notizen: CRUD, Append, Zitate, Versionen, AI-Threads/-Edit/-Ask, Assets.

Split out of api/product_main.py. Behaviour unchanged. Patchbare Namen laufen
ueber pm.<name>: llm_router, _slug (bleibt in product_main als geteilter Helfer).
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import api.product_main as pm  # patchable llm_router + geteilte Helfer
from storage.metadata_db import MetadataDB
from storage.path_safety import ensure_safe_path

DEFAULT_METADATA_DB_PATH = "data/metadata.duckdb"
DEFAULT_GRAPH_DB_PATH = "data/graphs/global_kg"
DEFAULT_NOTE_ASSET_DIR = "data/note_assets"

router = APIRouter()


class NotePayload(BaseModel):
    title: str = Field(default="Neue Notiz", min_length=1, max_length=180)
    markdown: str = Field(default="", max_length=200000)


class NotePatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=180)
    markdown: str | None = Field(default=None, max_length=200000)


class NoteAppendRequest(BaseModel):
    markdown: str = Field(min_length=1, max_length=80000)
    title: str | None = Field(default=None, max_length=180)
    citations: list[dict[str, Any]] = []


class NoteAiEditRequest(BaseModel):
    selected_text: str = Field(min_length=1, max_length=16000)
    instruction: str = Field(min_length=1, max_length=800)
    provider: str | None = None
    model: str | None = None
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH
    graph_db_path: str = DEFAULT_GRAPH_DB_PATH
    use_kg_evidence: bool = True


class NoteAiThreadRequest(NoteAiEditRequest):
    anchor_start: int | None = Field(default=None, ge=0)
    anchor_end: int | None = Field(default=None, ge=0)
    anchor_quote: str | None = Field(default=None, max_length=2000)


class NoteAiMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1200)
    provider: str | None = None
    model: str | None = None
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH
    graph_db_path: str = DEFAULT_GRAPH_DB_PATH
    use_kg_evidence: bool = True


class NoteAskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1200)
    provider: str | None = None
    model: str | None = None
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH
    graph_db_path: str = DEFAULT_GRAPH_DB_PATH
    use_kg_evidence: bool = True


class NoteAiThreadPatch(BaseModel):
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH
    ui_state: dict[str, Any] | None = None


@router.get("/projects/{project_id}/notes")
def list_project_notes(
    project_id: str,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        notes = [db.get_note(str(note.get("id"))) or note for note in db.list_notes(project_id=project_id, limit=1000)]
    return {"items": [_note_summary(note) for note in notes], "total": len(notes)}


@router.post("/projects/{project_id}/notes")
def create_project_note(
    project_id: str,
    payload: NotePayload,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        note = db.create_note(project_id=project_id, title=payload.title, markdown=payload.markdown)
    return {"note": _note_view(note)}


@router.get("/notes/{note_id}")
def get_note(note_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        note = db.get_note(note_id)
    if note is None:
        raise HTTPException(status_code=404, detail=f"Note not found: {note_id}")
    return {"note": _note_view(note)}


@router.patch("/notes/{note_id}")
def patch_note(
    note_id: str,
    payload: NotePatch,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        note = db.update_note(note_id, title=payload.title, markdown=payload.markdown)
    if note is None:
        raise HTTPException(status_code=404, detail=f"Note not found: {note_id}")
    return {"note": _note_view(note)}


@router.delete("/notes/{note_id}")
def delete_note(note_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        deleted = db.delete_note(note_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Note not found: {note_id}")
    return {"deleted": True}


@router.post("/notes/{note_id}/append")
def append_note(
    note_id: str,
    payload: NoteAppendRequest,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        note = db.append_note_markdown(
            note_id,
            markdown=payload.markdown,
            title=payload.title,
            citations=payload.citations,
        )
    if note is None:
        raise HTTPException(status_code=404, detail=f"Note not found: {note_id}")
    return {"note": _note_view(note)}


@router.delete("/notes/{note_id}/citations/{citation_id}")
def delete_note_citation(
    note_id: str,
    citation_id: str,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        deleted = db.delete_note_citation(note_id, citation_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Citation not found: {citation_id}")
    return {"deleted": True, "id": citation_id}


@router.post("/notes/{note_id}/versions/restore-latest")
def restore_latest_note_version(note_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        note = db.restore_latest_note_version(note_id)
    if note is None:
        raise HTTPException(status_code=404, detail=f"Note not found: {note_id}")
    return {"note": _note_view(note)}


@router.get("/notes/{note_id}/ai-threads")
def list_note_ai_threads(
    note_id: str,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        if db.get_note(note_id) is None:
            raise HTTPException(status_code=404, detail=f"Note not found: {note_id}")
        threads = db.list_note_ai_threads(note_id, limit=limit)
    return {"items": threads, "total": len(threads)}


@router.post("/notes/{note_id}/ai-threads")
def create_note_ai_thread(note_id: str, request: NoteAiThreadRequest) -> dict[str, Any]:
    thread = _create_note_ai_thread(note_id, request)
    return {
        "thread": thread,
        "replacement_text": thread.get("replacement_text") or thread.get("response_text") or "",
        "answer": thread.get("answer_payload") or {},
        "model": _note_ai_model(request),
    }


@router.patch("/notes/{note_id}/ai-threads/{thread_id}")
def patch_note_ai_thread(note_id: str, thread_id: str, request: NoteAiThreadPatch) -> dict[str, Any]:
    with MetadataDB(request.metadata_db_path) as db:
        thread = db.get_note_ai_thread(thread_id)
        if thread is None or str(thread.get("note_id")) != note_id:
            raise HTTPException(status_code=404, detail=f"AI thread not found: {thread_id}")
        updated = db.update_note_ai_thread(thread_id, ui_state=request.ui_state or {})
    return {"thread": updated}


@router.delete("/notes/{note_id}/ai-threads/{thread_id}")
def delete_note_ai_thread(
    note_id: str,
    thread_id: str,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
) -> dict[str, Any]:
    return _delete_note_ai_thread(note_id, thread_id, metadata_db_path)


@router.post("/notes/{note_id}/ai-threads/{thread_id}/delete")
def delete_note_ai_thread_action(
    note_id: str,
    thread_id: str,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
) -> dict[str, Any]:
    return _delete_note_ai_thread(note_id, thread_id, metadata_db_path)


def _delete_note_ai_thread(note_id: str, thread_id: str, metadata_db_path: str) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        thread = db.get_note_ai_thread(thread_id)
        if thread is None or str(thread.get("note_id")) != note_id:
            raise HTTPException(status_code=404, detail=f"AI thread not found: {thread_id}")
        db.delete_note_ai_thread(thread_id)
    return {"deleted": True}


@router.delete("/notes/{note_id}/ai-threads")
def delete_note_ai_threads(
    note_id: str,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
) -> dict[str, Any]:
    return _delete_note_ai_threads(note_id, metadata_db_path)


@router.post("/notes/{note_id}/ai-threads/delete-all")
def delete_note_ai_threads_action(
    note_id: str,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
) -> dict[str, Any]:
    return _delete_note_ai_threads(note_id, metadata_db_path)


def _delete_note_ai_threads(note_id: str, metadata_db_path: str) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        if db.get_note(note_id) is None:
            raise HTTPException(status_code=404, detail=f"Note not found: {note_id}")
        deleted = db.delete_note_ai_threads(note_id)
    return {"deleted": deleted}


@router.post("/notes/{note_id}/ai-threads/{thread_id}/messages")
def append_note_ai_message(note_id: str, thread_id: str, request: NoteAiMessageRequest) -> dict[str, Any]:
    with MetadataDB(request.metadata_db_path) as db:
        thread = db.get_note_ai_thread(thread_id)
        if thread is None or str(thread.get("note_id")) != note_id:
            raise HTTPException(status_code=404, detail=f"AI thread not found: {thread_id}")

    selected = str(thread.get("selected_text") or "").strip()
    evidence_request = NoteAiEditRequest(
        selected_text=selected or str(thread.get("anchor_quote") or "Auswahl"),
        instruction=request.message,
        provider=request.provider,
        model=request.model,
        metadata_db_path=request.metadata_db_path,
        graph_db_path=request.graph_db_path,
        use_kg_evidence=request.use_kg_evidence,
    )
    answer_payload = _note_evidence_payload(evidence_request) if request.use_kg_evidence else {}
    response = _run_note_ai_chat(
        selected_text=selected,
        instruction=request.message,
        evidence_block=_note_evidence_prompt(answer_payload),
        provider=request.provider,
        model=request.model,
        prior_messages=thread.get("messages") if isinstance(thread.get("messages"), list) else [],
    )
    with MetadataDB(request.metadata_db_path) as db:
        user_message = db.add_note_ai_message(thread_id, note_id, "user", request.message.strip())
        assistant_message = db.add_note_ai_message(thread_id, note_id, "assistant", response)
        updated = db.update_note_ai_thread(thread_id, response_text=response, replacement_text=response)
        thread = updated or db.get_note_ai_thread(thread_id)
    return {
        "thread": thread,
        "user_message": user_message,
        "assistant_message": assistant_message,
        "replacement_text": response,
        "answer": answer_payload,
        "model": _note_ai_model(request),
    }


@router.post("/notes/{note_id}/ai-edit")
def note_ai_edit(note_id: str, request: NoteAiEditRequest) -> dict[str, Any]:
    thread = _create_note_ai_thread(
        note_id,
        NoteAiThreadRequest(
            selected_text=request.selected_text,
            instruction=request.instruction,
            provider=request.provider,
            model=request.model,
            metadata_db_path=request.metadata_db_path,
            graph_db_path=request.graph_db_path,
            use_kg_evidence=request.use_kg_evidence,
        ),
    )
    return {
        "thread": thread,
        "replacement_text": thread.get("replacement_text") or thread.get("response_text") or "",
        "answer": thread.get("answer_payload") or {},
        "model": _note_ai_model(request),
    }


@router.post("/notes/{note_id}/ask")
def ask_note(note_id: str, request: NoteAskRequest) -> dict[str, Any]:
    with MetadataDB(request.metadata_db_path) as db:
        note = db.get_note(note_id)
    if note is None:
        raise HTTPException(status_code=404, detail=f"Note not found: {note_id}")

    markdown = str(note.get("markdown") or "").strip()
    if not markdown:
        raise HTTPException(status_code=400, detail="Note is empty.")

    evidence_request = NoteAiEditRequest(
        selected_text=_note_ai_context(markdown),
        instruction=request.question,
        provider=request.provider,
        model=request.model,
        metadata_db_path=request.metadata_db_path,
        graph_db_path=request.graph_db_path,
        use_kg_evidence=request.use_kg_evidence,
    )
    answer_payload = _note_evidence_payload(evidence_request) if request.use_kg_evidence else {}
    response = _run_note_ai_chat(
        selected_text=_note_ai_context(markdown),
        instruction=request.question,
        evidence_block=_note_evidence_prompt(answer_payload),
        provider=request.provider,
        model=request.model,
        subject_label="Ganze Notiz",
    )
    with MetadataDB(request.metadata_db_path) as db:
        thread = db.add_note_ai_thread(
            note_id=note_id,
            selected_text="",
            instruction=request.question.strip(),
            response_text=response,
            replacement_text=response,
            answer_payload=answer_payload,
            anchor_quote="",
            ui_state={"collapsed": True, "scope": "note"},
        )
    return {
        "thread": thread,
        "replacement_text": response,
        "answer": answer_payload,
        "model": _note_ai_model(request),
    }


def _create_note_ai_thread(note_id: str, request: NoteAiThreadRequest) -> dict[str, Any]:
    with MetadataDB(request.metadata_db_path) as db:
        if db.get_note(note_id) is None:
            raise HTTPException(status_code=404, detail=f"Note not found: {note_id}")

    answer_payload = _note_evidence_payload(request) if request.use_kg_evidence else {}
    evidence_block = _note_evidence_prompt(answer_payload)
    instruction = request.instruction.strip()
    selected = request.selected_text.strip()
    replacement = _run_note_ai_chat(
        selected_text=selected,
        instruction=instruction,
        evidence_block=evidence_block,
        provider=request.provider,
        model=request.model,
    )
    with MetadataDB(request.metadata_db_path) as db:
        thread = db.add_note_ai_thread(
            note_id=note_id,
            selected_text=selected,
            instruction=instruction,
            response_text=replacement,
            replacement_text=replacement,
            answer_payload=answer_payload,
            anchor_start=request.anchor_start,
            anchor_end=request.anchor_end,
            anchor_quote=request.anchor_quote or selected[:2000],
            ui_state={"collapsed": True},
        )
    return thread


@router.post("/notes/{note_id}/assets")
async def upload_note_asset(
    note_id: str,
    request: Request,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
    note_asset_dir: str = DEFAULT_NOTE_ASSET_DIR,
) -> dict[str, Any]:
    content = await request.body()
    if not content:
        raise HTTPException(status_code=400, detail="Upload body is empty.")
    content_type = request.headers.get("content-type") or "application/octet-stream"
    if not content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Only image assets are supported for notes.")

    with MetadataDB(metadata_db_path) as db:
        if db.get_note(note_id) is None:
            raise HTTPException(status_code=404, detail=f"Note not found: {note_id}")

    filename = _safe_asset_filename(request.headers.get("x-filename") or "note-image")
    target_dir = ensure_safe_path(note_asset_dir, what="note asset dir") / pm._slug(note_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / f"{datetime.now().strftime('%Y%m%d%H%M%S%f')}_{filename}"
    target_path.write_bytes(content)

    with MetadataDB(metadata_db_path) as db:
        asset = db.add_note_asset(note_id, filename=filename, content_type=content_type, asset_path=str(target_path))
    return {"asset": {**asset, "url": f"/notes/assets/{asset['id']}"}}


@router.get("/notes/assets/{asset_id}")
def note_asset(
    asset_id: str,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
    note_asset_dir: str = DEFAULT_NOTE_ASSET_DIR,
):
    with MetadataDB(metadata_db_path) as db:
        asset = db.get_note_asset(asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail=f"Asset not found: {asset_id}")

    asset_path = Path(str(asset.get("asset_path") or "")).resolve()
    base_path = Path(note_asset_dir).resolve()
    if base_path not in [asset_path, *asset_path.parents] or not asset_path.exists():
        raise HTTPException(status_code=404, detail=f"Asset file not found: {asset_id}")
    return FileResponse(
        path=str(asset_path),
        media_type=str(asset.get("content_type") or "application/octet-stream"),
        filename=str(asset.get("filename") or asset_path.name),
    )


def _note_summary(note: dict[str, Any]) -> dict[str, Any]:
    markdown = str(note.get("markdown") or "")
    return {
        "id": note.get("id"),
        "project_id": note.get("project_id"),
        "title": note.get("title") or "Neue Notiz",
        "markdown": markdown,
        "excerpt": _note_excerpt(markdown),
        "citation_count": len(note.get("citations") or []),
        "asset_count": len(note.get("assets") or []),
        "created_timestamp": note.get("created_timestamp"),
        "updated_timestamp": note.get("updated_timestamp"),
    }


def _note_view(note: dict[str, Any]) -> dict[str, Any]:
    citations = [dict(item) for item in note.get("citations") or []]
    assets = [{**dict(item), "url": f"/notes/assets/{item.get('id')}"} for item in note.get("assets") or []]
    return {
        **_note_summary({**note, "citations": citations, "assets": assets}),
        "citations": citations,
        "assets": assets,
    }


def _note_excerpt(markdown: str) -> str:
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", markdown)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"[#>*_`|~-]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:180]


def _note_evidence_payload(request: NoteAiEditRequest) -> dict[str, Any]:
    if not _instruction_needs_evidence(request.instruction):
        return {}
    retriever = pm._parallel_retriever(request.metadata_db_path, request.graph_db_path)
    hits = retriever.search(f"{request.selected_text} {request.instruction}", limit=6)
    sources: dict[str, dict[str, Any]] = {}
    evidence: list[dict[str, Any]] = []
    for hit in hits:
        source = hit.source.to_dict()
        sources[source["paper_id"]] = source
        for item in hit.evidence[:3]:
            evidence.append(item.to_dict())
            if len(evidence) >= 12:
                break
        if len(evidence) >= 12:
            break
    return {"sources": list(sources.values()), "evidence": evidence}


def _run_note_ai_chat(
    selected_text: str,
    instruction: str,
    evidence_block: str,
    provider: str | None = None,
    model: str | None = None,
    prior_messages: list[dict[str, Any]] | None = None,
    subject_label: str = "Markierter Text",
) -> str:
    overrides: dict[str, Any] = {
        "temperature": 0.18,
        "top_p": 0.9,
        "max_tokens": min(2400, max(450, len(selected_text) // 2 + 500)),
    }
    if model:
        overrides["model"] = model
    messages: list[dict[str, str]] = [
        {
            "role": "system",
            "content": (
                "Du bist ein lokaler wissenschaftlicher Markdown-Schreibassistent. "
                "Bearbeite nur den bereitgestellten Text und den bisherigen Verlauf zu diesem Kontext. "
                "Gib direkt Markdown zurueck. Nutze ausschliesslich bereitgestellte KG-Evidenz, "
                "wenn du neue Belege ergaenzt, und zitiere dann mit den angegebenen Paper-IDs in eckigen Klammern. "
                "WICHTIG: Markdown-Links der Form [Zx - Titel](sciencekg://citation/...) sind Quellenanker "
                "und duerfen niemals entfernt, gekuerzt oder umformuliert werden. Wenn du einen Satz oder ein "
                "Zitat behaeltst, kuerzt oder umschreibst, uebernimm seinen sciencekg://-Link zeichengenau "
                "(Linktext UND URL) an der passenden Stelle deiner Antwort. Auch Blockzitate (> ...) mit "
                "solchen Links behalten ihre Quellenzeile. "
                "Der Editor stellt genau diesen Markdown-Umfang dar, sonst nichts: Ueberschriften # bis ###### "
                "(je ein Leerzeichen danach), Listen mit - oder 1. (Verschachtelung ausschliesslich durch "
                "Einrueckung mit Tabs oder Leerzeichen, keine Leerzeile zwischen Listenpunkten), einstufige "
                "Blockzitate mit > , Fettschrift **x**, Kursiv *x*, Inline-Code `x`, Links [text](url), ein "
                "Bild ![alt](url) als eigener Absatz, GFM-Pipe-Tabellen, Hervorhebung ==x==, sowie --- oder "
                '<hr class="dashed"> als Trennlinie. Verwende KEINE Fenced-Code-Bloecke (```); nutze fuer Code '
                "stattdessen Inline-Code. Erfinde keine anderen Markdown-Konstrukte."
            ),
        }
    ]
    for item in (prior_messages or [])[-8:]:
        role = "assistant" if item.get("role") == "assistant" else "user"
        content = str(item.get("content") or "").strip()
        if content:
            messages.append({"role": role, "content": content})
    messages.append(
        {
            "role": "user",
            "content": (
                f"Aufgabe: {instruction.strip()}\n\n"
                f"{subject_label}:\n{selected_text.strip()}\n\n"
                f"{evidence_block}"
            ),
        }
    )
    try:
        response = pm.llm_router.chat(messages, provider=provider, overrides=overrides)
        if _note_ai_response_needs_retry(response, selected_text):
            retry_overrides = _note_ai_retry_overrides(overrides)
            retry_messages = [
                *messages,
                {
                    "role": "user",
                    "content": (
                        "Die vorige Antwort war leer oder hat nur den markierten Text wiederholt. "
                        "Antworte jetzt direkt auf die Aufgabe. Wiederhole den markierten Text nicht. "
                        "Denke nicht lange intern nach. Gib sofort die finale Antwort aus. "
                        "Wenn eine Zusammenfassung verlangt wird, schreibe 2-4 kurze Saetze in einfacher Sprache."
                    ),
                },
            ]
            response = pm.llm_router.chat(retry_messages, provider=provider, overrides=retry_overrides)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AI edit failed: {exc}") from exc
    response_text = str(response or "").strip()
    if _note_ai_response_needs_retry(response_text, selected_text):
        raise HTTPException(
            status_code=502,
            detail="AI edit failed: provider returned an empty or unchanged answer.",
        )
    return response_text


def _note_ai_response_needs_retry(response: Any, selected_text: str) -> bool:
    response_text = str(response or "").strip()
    if not response_text:
        return True
    selected = _normalize_note_ai_echo_text(selected_text)
    answer = _normalize_note_ai_echo_text(response_text)
    if not selected or len(selected) < 24:
        return False
    if answer == selected:
        return True
    if len(answer) >= int(len(selected) * 0.9) and (answer in selected or selected in answer):
        return True
    return False


def _note_ai_retry_overrides(overrides: dict[str, Any]) -> dict[str, Any]:
    retry = dict(overrides)
    retry["temperature"] = min(float(retry.get("temperature", 0.18)), 0.08)
    retry["max_tokens"] = max(int(retry.get("max_tokens") or 0) * 4, 2048)
    extra = dict(retry.get("extra") or {})
    extra["include_reasoning"] = False
    extra["chat_template_kwargs"] = {"enable_thinking": False, "thinking": False}
    retry["extra"] = extra
    return retry


def _normalize_note_ai_echo_text(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip().lower()
    text = re.sub(r"^[>*\-\s]+", "", text)
    return text


def _note_ai_model(request: NoteAiEditRequest | NoteAiMessageRequest | NoteAskRequest) -> str:
    return request.model or pm.llm_router.provider_default_model(request.provider)


def _note_ai_context(markdown: str, max_chars: int = 16000) -> str:
    text = re.sub(r"\s+", " ", str(markdown or "")).strip()
    return text[:max_chars]


def _instruction_needs_evidence(instruction: str) -> bool:
    text = instruction.lower()
    return any(token in text for token in ["beleg", "beweis", "evidence", "quelle", "zitat", "citation", "argument"])


def _note_evidence_prompt(answer_payload: dict[str, Any]) -> str:
    evidence = answer_payload.get("evidence") if isinstance(answer_payload, dict) else None
    sources = answer_payload.get("sources") if isinstance(answer_payload, dict) else None
    if not evidence:
        return "Keine zusaetzliche KG-Evidenz bereitgestellt."
    titles = {
        str(source.get("paper_id")): str(source.get("title") or source.get("paper_id"))
        for source in (sources or [])
        if isinstance(source, dict)
    }
    lines = ["Lokale KG-Evidenz, die du verwenden darfst:"]
    for index, item in enumerate(evidence[:12], start=1):
        if not isinstance(item, dict):
            continue
        paper_id = str(item.get("paper_id") or "")
        text = re.sub(r"\s+", " ", str(item.get("text") or "")).strip()
        lines.append(f"{index}. [{paper_id}] {titles.get(paper_id, paper_id)} | {item.get('kind')}: {text}")
    return "\n".join(lines)


def _safe_asset_filename(filename: str) -> str:
    raw = Path(filename).name.strip() or "note-image"
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(raw).stem).strip("-") or "note-image"
    suffix = Path(raw).suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}:
        suffix = ".png"
    return f"{stem[:80]}{suffix}"
