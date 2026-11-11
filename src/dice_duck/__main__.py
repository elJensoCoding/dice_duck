"""`python -m dice_duck` startet den Server."""

import argparse

import uvicorn


def main() -> None:
    ap = argparse.ArgumentParser(description="Dice Duck Würfelserver")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8077)
    args = ap.parse_args()
    uvicorn.run("dice_duck.api:create_app", factory=True, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
