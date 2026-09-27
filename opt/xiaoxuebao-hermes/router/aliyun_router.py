import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

ENV_PATH = "/opt/xiaoxuebao-hermes/.env"
ALIYUN_DEFAULT_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEEPSEEK_DEFAULT_BASE = "https://api.deepseek.com"
RETRY_STATUSES = {400, 408, 409, 425, 429, 500, 502, 503, 504}
RETRY_MARKERS = (
    "quota",
    "Quota",
    "rate limit",
    "RateLimit",
    "throttl",
    "Throttl",
    "insufficient",
    "exceed",
    "Exceed",
    "temporarily unavailable",
    "Too Many Requests",
)
PRO_KEYWORDS = (
    "MRD",
    "mrd",
    "微小残留",
    "阳性",
    "治疗方案",
    "化疗方案",
    "换药",
    "停药",
    "减量",
    "剂量",
    "多少毫克",
    "阈值",
    "输血",
    "血小板",
    "血红蛋白",
    "发热",
    "发烧",
    "寒战",
    "出血",
    "鼻血",
    "止不住",
    "呼吸困难",
    "抽搐",
    "意识异常",
    "脱水",
    "持续呕吐",
    "PICC",
    "输液港",
    "红肿",
    "流脓",
    "感染",
    "中性粒",
    "ANC",
    "复发",
    "移植",
    "靶向",
    "基因",
    "报告",
    "检查单",
    "AML",
    "ALL",
    "来源",
    "参考",
    "依据",
    "联网",
    "最新",
    "搜索",
)


def load_env_file(path: str) -> Dict[str, str]:
    data: Dict[str, str] = {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for raw in handle:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                data[key.strip()] = value.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return data


def env(name: str, default: str = "") -> str:
    file_env = load_env_file(ENV_PATH)
    return os.getenv(name) or file_env.get(name, default)


def csv_env(name: str, default: str) -> List[str]:
    return [part.strip() for part in env(name, default).split(",") if part.strip()]


def has_image_part(value: Any) -> bool:
    if isinstance(value, dict):
        if value.get("type") in {"image_url", "input_image"}:
            return True
        if "image_url" in value or "input_image" in value:
            return True
        return any(has_image_part(v) for v in value.values())
    if isinstance(value, list):
        return any(has_image_part(item) for item in value)
    return False


def request_has_image(payload: Dict[str, Any]) -> bool:
    for message in payload.get("messages", []):
        if has_image_part(message.get("content")):
            return True
    return False


def content_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        if isinstance(value.get("text"), str):
            return value["text"]
        return " ".join(content_text(v) for v in value.values())
    if isinstance(value, list):
        return " ".join(content_text(item) for item in value)
    return ""


def task_text(payload: Dict[str, Any]) -> str:
    texts: List[str] = []
    fallback: List[str] = []
    for message in payload.get("messages", []):
        text = content_text(message.get("content", ""))
        if not text:
            continue
        role = str(message.get("role", "user"))
        fallback.append(text)
        if role not in {"system", "developer"}:
            texts.append(text)
    return "\n".join(texts or fallback)


def should_use_pro(payload: Dict[str, Any]) -> bool:
    text = task_text(payload)
    if len(text) >= int(env("DEEPSEEK_PRO_CHAR_THRESHOLD", "1400")):
        return True
    if len([m for m in payload.get("messages", []) if m.get("role") != "system"]) >= 6:
        return True
    return any(keyword in text for keyword in PRO_KEYWORDS)


def can_retry(status_code: int, body: str) -> bool:
    if status_code in RETRY_STATUSES:
        return True
    return any(marker in body for marker in RETRY_MARKERS)


def deepseek_models(payload: Dict[str, Any]) -> List[str]:
    fast = env("DEEPSEEK_FAST_MODEL", "deepseek-v4-flash")
    pro = env("DEEPSEEK_PRO_MODEL", "deepseek-v4-pro")
    ordered = [pro, fast] if should_use_pro(payload) else [fast, pro]
    result: List[str] = []
    for model in ordered:
        if model and model not in result:
            result.append(model)
    return result


def text_provider_config() -> Tuple[str, List[str]]:
    provider = env("XIAOXUEBAO_TEXT_PROVIDER", "deepseek").strip().lower()
    if provider in {"aliyun", "dashscope", "bailian"}:
        return "aliyun-bailian", csv_env("ALIYUN_TEXT_MODELS", "qwen-plus,qwen-turbo,qwen-max")
    return "deepseek", deepseek_models({"messages": [{"role": "user", "content": "你好"}]})


def upstream_for_payload(payload: Dict[str, Any]) -> Tuple[str, str, str, List[str], str]:
    if request_has_image(payload):
        key = env("DASHSCOPE_API_KEY") or env("ALIYUN_API_KEY")
        base = env("ALIYUN_BASE_URL", ALIYUN_DEFAULT_BASE).rstrip("/")
        models = csv_env("ALIYUN_VISION_MODELS", "qwen3-vl-flash,qwen-vl-plus,qwen-vl-max")
        return "aliyun-bailian", "vision", base, models, key
    text_provider, _ = text_provider_config()
    if text_provider == "aliyun-bailian":
        key = env("DASHSCOPE_API_KEY") or env("ALIYUN_API_KEY")
        base = env("ALIYUN_BASE_URL", ALIYUN_DEFAULT_BASE).rstrip("/")
        models = csv_env("ALIYUN_TEXT_MODELS", "qwen-plus,qwen-turbo,qwen-max")
        return "aliyun-bailian", "text", base, models, key
    key = env("DEEPSEEK_API_KEY")
    base = env("DEEPSEEK_BASE_URL", DEEPSEEK_DEFAULT_BASE).rstrip("/")
    return "deepseek", "text-pro" if should_use_pro(payload) else "text-flash", base, deepseek_models(payload), key


def auth(authorization: Optional[str]) -> None:
    expected = env("ROUTER_API_KEY")
    if expected and authorization != "Bearer " + expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


app = FastAPI(title="Xiaoxuebao Multi-Provider Model Router")


@app.get("/health")
async def health() -> Dict[str, Any]:
    text_provider, text_models = text_provider_config()
    return {
        "status": "ok",
        "router": "xiaoxuebao-model-router",
        "text_provider": text_provider,
        "text_models": text_models,
        "vision_provider": "aliyun-bailian",
        "vision_models": csv_env("ALIYUN_VISION_MODELS", "qwen3-vl-flash,qwen-vl-plus,qwen-vl-max"),
    }


@app.get("/v1/models")
async def models(authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    auth(authorization)
    items = []
    text_provider, text_model_names = text_provider_config()
    for model in text_model_names:
        if model:
            items.append({"id": model, "object": "model", "owned_by": text_provider})
    for model in csv_env("ALIYUN_VISION_MODELS", "qwen3-vl-flash,qwen-vl-plus,qwen-vl-max"):
        items.append({"id": model, "object": "model", "owned_by": "aliyun-bailian-vision"})
    items.append({"id": "xiaoxuebao-text-auto", "object": "model", "owned_by": "xiaoxuebao-router"})
    items.append({"id": "xiaoxuebao-vision-auto", "object": "model", "owned_by": "xiaoxuebao-router"})
    return {"object": "list", "data": items}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request, authorization: Optional[str] = Header(default=None)):
    auth(authorization)
    payload = await request.json()
    provider, route, base, candidates, upstream_key = upstream_for_payload(payload)
    if not upstream_key:
        raise HTTPException(status_code=500, detail=f"{provider} upstream key missing")
    if not candidates:
        raise HTTPException(status_code=500, detail="No candidate models configured")

    headers = {"Authorization": "Bearer " + upstream_key, "Content-Type": "application/json"}
    stream = bool(payload.get("stream"))
    timeout = httpx.Timeout(connect=20.0, read=300.0, write=60.0, pool=20.0)
    errors: List[Dict[str, Any]] = []

    if stream:
        async def generate():
            async with httpx.AsyncClient(timeout=timeout) as client:
                for model in candidates:
                    attempt = dict(payload)
                    attempt["model"] = model
                    async with client.stream("POST", base + "/chat/completions", headers=headers, json=attempt) as response:
                        if response.status_code == 200:
                            async for chunk in response.aiter_bytes():
                                yield chunk
                            return
                        body = (await response.aread()).decode("utf-8", "replace")
                        errors.append({"provider": provider, "model": model, "status": response.status_code, "body": body[:300]})
                        if not can_retry(response.status_code, body):
                            break
                yield (
                    "data: "
                    + json.dumps({"error": "all candidate models failed", "details": errors}, ensure_ascii=False)
                    + "\n\n"
                ).encode("utf-8")
                yield b"data: [DONE]\n\n"

        return StreamingResponse(generate(), media_type="text/event-stream")

    async with httpx.AsyncClient(timeout=timeout) as client:
        for model in candidates:
            attempt = dict(payload)
            attempt["model"] = model
            response = await client.post(base + "/chat/completions", headers=headers, json=attempt)
            text = response.text
            if response.status_code < 400:
                try:
                    data = response.json()
                except Exception:
                    return JSONResponse(status_code=response.status_code, content={"raw": text})
                data.setdefault("xiaoxuebao_router", {})["selected_model"] = model
                data["xiaoxuebao_router"]["provider"] = provider
                data["xiaoxuebao_router"]["route"] = route
                data["xiaoxuebao_router"]["fallback_candidates"] = candidates
                return JSONResponse(status_code=response.status_code, content=data)
            errors.append({"provider": provider, "model": model, "status": response.status_code, "body": text[:300]})
            if not can_retry(response.status_code, text):
                break
    return JSONResponse(status_code=502, content={"error": "all candidate models failed", "details": errors})
