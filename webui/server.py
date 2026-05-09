import uvicorn
from webui.app import create_app


def run(host: str = "127.0.0.1", port: int = 5480) -> None:
    uvicorn.run(create_app(), host=host, port=port)


if __name__ == "__main__":
    run()
