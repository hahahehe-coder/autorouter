"""fake_newapi.py — 极简 mock new-api,只接 POST ,回一段假响应 + 真 usage。
本机测试时启动它替代 127.0.0.1:3000,验证 auto_router 的回灌链路。"""
import os
import time
import json
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI()


@app.get("/v1/models")
async def list_models():
    """假的模型列表,供配置看板测试拉取。"""
    return {
        "object": "list",
        "data": [
            {"id": "gpt-4o", "object": "model"},
            {"id": "gpt-4o-mini", "object": "model"},
            {"id": "MiniMax-M3", "object": "model"},
            {"id": "glm-5.2", "object": "model"},
            {"id": "Qwen-Turbo", "object": "model"},
            {"id": "deepseek-chat", "object": "model"},
        ]
    }


@app.post("/v1/chat/completions")
async def chat(request: Request):
    body = await request.json()
    model = body.get("model", "?")
    is_stream = body.get("stream", False)

    if is_stream:
        async def gen():
            for chunk_text in ["hello", " from ", model]:
                yield f"data: {json.dumps({'id':'x','object':'chat.completion.chunk','model':model,'choices':[{'index':0,'delta':{'content':chunk_text},'finish_reason':None}]})}\n\n"
            yield "data: [DONE]\n\n"
        from fastapi.responses import StreamingResponse
        return StreamingResponse(gen(), media_type="text/event-stream")

    return JSONResponse({
        "id": "x",
        "object": "chat.completion",
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": f"echo: you asked model={model}"},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    })


@app.post("/v1/messages")
async def anthropic_messages(request: Request):
    """Anthropic Messages API — 假装回一段 content block。"""
    body = await request.json()
    model = body.get("model", "?")
    return JSONResponse({
        "id": "msg_x",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": f"echo(messages): model={model}"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 8, "output_tokens": 6},
    })


@app.post("/v1/responses")
async def openai_responses(request: Request):
    """OpenAI Responses API — 假装回一段 output。"""
    body = await request.json()
    model = body.get("model", "?")
    return JSONResponse({
        "id": "resp_x",
        "object": "response",
        "model": model,
        "output": [{
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": f"echo(responses): model={model}"}],
        }],
        "usage": {"input_tokens": 9, "output_tokens": 7, "total_tokens": 16},
    })


@app.get("/health")
async def h():
    return {"status": "fake-newapi"}


if __name__ == "__main__":
    uvicorn.run(app, host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "3000")))
