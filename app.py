"""Compatibility entry point: python -m uvicorn app:app."""
from local_annotation.app import create_app

app = create_app()
