from __future__ import annotations

from fastapi import FastAPI

from codingagent import __version__
from codingagent.config import Settings

from .deps import Container
from .routes import router


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.load()
    app = FastAPI(
        title="Coding Agent",
        version=__version__,
        description="手写 ReAct Coding Agent + 上下文策略受控实验的对外接口",
    )
    app.state.container = Container.build(settings)
    app.include_router(router)
    return app
