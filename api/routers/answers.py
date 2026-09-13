"""Progress only SSE: publish the checked final answer atomically."""

import asyncio
import json
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from api.phase4_main import AnswerRequest, _query_answer

router = APIRouter()


@router.post("/query/answer/stream")
async def stream_answer(request: AnswerRequest):
    async def events():
        queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def progress(stage, message):
            loop.call_soon_threadsafe(
                queue.put_nowait,
                {"type": "progress", "stage": stage, "message": message},
            )

        async def run():
            try:
                result = await asyncio.to_thread(_query_answer, request, progress)
                await queue.put({"type": "answer", "answer": result})
            except Exception as exc:
                await queue.put({"type": "error", "message": str(exc)})
            finally:
                await queue.put(None)

        task = asyncio.create_task(run())
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), 10)
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
                    continue
                if event is None:
                    break
                yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
        finally:
            # A provider request already in a worker thread may finish, but no
            # draft or late answer is published after client disconnection.
            task.cancel()

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
