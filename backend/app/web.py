"""Production entry point: serve the built React site and API from one address."""

from fastapi.staticfiles import StaticFiles

from . import config, db
from .main import app


@app.get("/healthz", include_in_schema=False)
def healthcheck():
    connection = db.get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    finally:
        connection.close()
    return {"status": "ok"}


# Register last so /api and /docs keep reaching FastAPI. The UI uses hash routes.
app.mount("/", StaticFiles(directory=config.BACKEND_DIR.parent / "frontend" / "dist", html=True), name="site")
