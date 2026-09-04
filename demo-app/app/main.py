from fastapi import FastAPI

app = FastAPI(title="Vigil Demo App")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
