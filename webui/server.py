import argparse

import uvicorn
from webui.app import create_app


def run(host: str = "127.0.0.1", port: int = 5480) -> None:
    uvicorn.run(create_app(), host=host, port=port)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5480)
    args = parser.parse_args()
    run(args.host, args.port)
