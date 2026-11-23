"""Друкує короткий індекс коду (функції з рядками й першим рядком опису) — для швидкої навігації та нотатки «Карта коду»."""
import ast, pathlib, sys
root = pathlib.Path(__file__).resolve().parent.parent
files = sys.argv[1:] or ["sc/edit.py", "sc/edit_fx.py", "sc/pipeline.py", "sc/brands.py", "sc/vocals.py",
                         "sc/analysis.py", "sc/sources.py", "sc/render.py", "sc/core.py", "app.py"]
for f in files:
    src = (root / f).read_text(encoding="utf-8")
    print(f"\n## {f}  ({len(src.splitlines())} рядків)")
    for node in ast.parse(src).body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            doc = (ast.get_docstring(node) or "").strip().splitlines()
            kind = "class " if isinstance(node, ast.ClassDef) else ""
            print(f"- `{kind}{node.name}` {node.lineno}–{node.end_lineno}" + (f" — {doc[0][:110]}" if doc else ""))
