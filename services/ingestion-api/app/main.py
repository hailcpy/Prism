from fastapi import FastAPI

app = FastAPI(title="olive-ingestion-api", version="0.1.0")


@app.get("/healthz")
def health():
    return {"status": "ok"}
