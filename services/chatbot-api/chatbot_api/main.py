from fastapi import FastAPI

app = FastAPI(title="prism-chatbot-api", version="0.1.0")


@app.get("/healthz")
def health() -> dict[str, str]:
    return {"status": "ok"}
