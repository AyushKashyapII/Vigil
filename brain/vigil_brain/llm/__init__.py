"""The one place in this codebase that isn't deterministic -- proposes
query rewrites via an LLM. Its output is never trusted directly:
vigil_brain.sandbox.verify_rewrite is what actually proves a rewrite is
correct and faster before anything acts on it.
"""
