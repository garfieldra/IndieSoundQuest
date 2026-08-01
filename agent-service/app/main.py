from fastapi import FastAPI

app = FastAPI(title="IndieSoundQuest Agent Service", version="0.1.0")


@app.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "UP"}


@app.get("/health/ready")
async def ready() -> dict[str, str]:
    # 后续将校验 Java 内部接口、Milvus 和模型 Provider 的就绪状态。
    return {"status": "UP", "mode": "skeleton"}
