from fastapi import FastAPI

from mortgage_refinance.app import app as mortgage_app


app = FastAPI(title="Mortgage Refinance IVR")
app.mount("/", mortgage_app)

__all__ = ["app"]
