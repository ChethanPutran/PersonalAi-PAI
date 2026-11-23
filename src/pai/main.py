"""PAI development/server runner."""
import uvicorn

from pai.config.loader import load_config


def main() -> None:
    config = load_config()

    # uvicorn treats these as mutually exclusive; reject early with a
    # clear message instead of getting the silent fallback.
    if config.server.reload and (config.server.workers or 1) > 1:
        raise SystemExit(
            "server.reload and server.workers>1 are mutually exclusive; "
            "pick one."
        )

    uvicorn.run(
        "pai.server.app:create_app",   # import string, not an object
        factory=True,                  # tell uvicorn to *call* it
        host=config.api.host,
        port=config.api.port,
        reload=config.server.reload,
        workers=config.server.workers,
        access_log=False,
    )


if __name__ == "__main__":
    main()