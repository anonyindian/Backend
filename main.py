import shlex
import time
from asyncio.log import logger
from shutil import which
from subprocess import DEVNULL, STDOUT, Popen, run
from sys import platform
from typing import Any, Dict, List

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import FileResponse, UJSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.cors import CORSMiddleware

from app import __version__
from app.api import main_router
from app.core import TMDB, MongoDB, RCloneAPI, build_config
from app.core.cron import fetch_metadata
from app.settings import settings
from app.utils import time_formatter
from scripts.install_rclone import download_rclone

if not settings.MONGODB_DOMAIN:
    logger.error("No MongoDB domain found! Exiting.")
    exit()
if not settings.MONGODB_USERNAME:
    logger.error("No MongoDB username found! Exiting.")
    exit()
if not settings.MONGODB_PASSWORD:
    logger.error("No MongoDB password found! Exiting.")
    exit()

start_time = time.time()
mongo = MongoDB(settings.MONGODB_DOMAIN,
                settings.MONGODB_USERNAME, settings.MONGODB_PASSWORD)
rclone = {}


def restart_rclone():
    if platform in ["win32", "cygwin", "msys"]:
        run(
            shlex.split(
                f"powershell.exe Stop-Process -Id (Get-NetTCPConnection -LocalPort {settings.RCLONE_LISTEN_PORT}).OwningProcess -Force"
            ),
            stdout=DEVNULL,
            stderr=STDOUT,
        )
    elif platform in ["linux", "linux2"]:
        run(
            shlex.split(
                f"bash kill $(lsof -t -i:{settings.RCLONE_LISTEN_PORT})"),
            stdout=DEVNULL,
            stderr=STDOUT,
        )
    elif platform in ["darwin"]:
        run(
            shlex.split(f"kill $(lsof -t -i:{settings.RCLONE_LISTEN_PORT})"),
            stdout=DEVNULL,
            stderr=STDOUT,
        )
    else:
        exit("Unsupported platform")
    rclone_bin = which("rclone")
    Popen(
        shlex.split(
            f"{rclone_bin} rcd --rc-no-auth --rc-addr localhost:{settings.RCLONE_LISTEN_PORT} --config rclone.conf", posix=(not platform in ["win32", "cygwin", "msys"])
        )
    )


def rclone_setup(categories: List[Dict[str, Any]]):
    rclone_conf = ""
    for item in mongo.config["rclone"]:
        rclone_conf += f"\n\n{item}"
    with open("rclone.conf", "w+") as w:
        w.write(rclone_conf)

    restart_rclone()

    for category in categories:
        rclone[id] = RCloneAPI(category)


def metadata_setup():
    tmdb = TMDB(api_key=mongo.get_tmbd_api_key())
    fetch_metadata(tmdb)


def startup():
    logger.info("Starting up...")

    logger.debug("Initializing core modules...")

    if mongo.get_is_config_init() is True:
        categories = mongo.get_categories()
        rclone_setup(categories)
        if mongo.get_is_metadata_init() is False:
            metadata_setup()
        logger.debug("Done.")
    else:
        # logic for first time setup
        pass


app = FastAPI(
    title="DesterLib",
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    exception_handlers={
        StarletteHTTPException: lambda req, exc: UJSONResponse(
            status_code=404, content={"ok": False, "message": "Are you lost?"}
        ),
        500: lambda req, exc: UJSONResponse(
            status_code=500,
            content={
                "ok": False,
                "message": "Internal server error",
                "error_msg": str(exc),
            },
        ),
    },
)


@app.exception_handler(StarletteHTTPException)
async def _spa_server(req: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return FileResponse("build/index.html", media_type="text/html")
    else:
        return await http_exception_handler(req, exc)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(main_router, prefix=settings.API_V1_STR)
app.mount("/", StaticFiles(directory="build/", html=True), name="static")

startup()
if __name__ == "__main__":
    uvicorn.run("main:app", host="localhost", port=settings.PORT, reload=True)
