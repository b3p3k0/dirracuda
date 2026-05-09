from fastapi import FastAPI


def health() -> dict:
    return {"status": "ok"}


def create_app() -> FastAPI:
    app = FastAPI(
        title="Dirracuda Web UI",
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
    )
    app.get("/health")(health)
    return app
