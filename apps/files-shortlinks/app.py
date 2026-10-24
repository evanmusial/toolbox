"""Cloud Run entry point for files.musial.io short links."""

from shortlinks import create_app


app = create_app()
