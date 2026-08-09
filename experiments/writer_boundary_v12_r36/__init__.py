"""R3.6 single-shot capability probe call layer.

Imports stay explicit at call sites so running builder/executor modules cannot
pre-import each other through the package initializer.
"""

__all__: list[str] = []
