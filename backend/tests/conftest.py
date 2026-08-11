"""Pytest collection config for backend/tests.

stress_test.py is a live-server stress harness (requires uvicorn on :8000),
run as a standalone script - keep it out of pytest collection.
"""
collect_ignore = ["stress_test.py"]
