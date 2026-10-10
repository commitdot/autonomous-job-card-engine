import ast
import os
import re
from typing import Dict, Any, List, Optional, Tuple


class SkeletonTransformer(ast.NodeTransformer):
    """
    Transforms a Python AST by replacing function and method bodies with Ellipsis (...)
    while preserving module docstrings, class docstrings, function signatures,
    type annotations, and class structures.
    """

    def __init__(self, preserve_docstrings: bool = True):
        self.preserve_docstrings = preserve_docstrings

    def _truncate_docstring(self, doc: str, max_len: int = 120) -> str:
        if not doc:
            return ""
        lines = doc.strip().splitlines()
        first_line = lines[0].strip() if lines else ""
        if len(first_line) > max_len:
            return first_line[:max_len] + "..."
        return first_line

    def _create_body(self, docstring: Optional[str]) -> List[ast.stmt]:
        body: List[ast.stmt] = []
        if self.preserve_docstrings and docstring:
            short_doc = self._truncate_docstring(docstring)
            body.append(ast.Expr(value=ast.Constant(value=short_doc)))
        body.append(ast.Expr(value=ast.Constant(value=Ellipsis)))
        return body

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        docstring = ast.get_docstring(node)
        node.body = self._create_body(docstring)
        return node

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST:
        docstring = ast.get_docstring(node)
        node.body = self._create_body(docstring)
        return node

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.AST:
        docstring = ast.get_docstring(node)
        new_body: List[ast.stmt] = []
        if self.preserve_docstrings and docstring:
            short_doc = self._truncate_docstring(docstring)
            new_body.append(ast.Expr(value=ast.Constant(value=short_doc)))

        # Preserve class-level attribute annotations, methods, inner classes
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                transformed = self.visit(item)
                if transformed:
                    new_body.append(transformed)
            elif isinstance(item, ast.AnnAssign):
                # Preserves class type annotations: e.g. name: str
                item.value = None
                new_body.append(item)
            elif isinstance(item, ast.Assign):
                # Only preserve simple constant / type assignments
                if len(item.targets) == 1 and isinstance(item.targets[0], ast.Name):
                    if isinstance(item.value, (ast.Constant, ast.Name)):
                        new_body.append(item)

        if not new_body:
            new_body = [ast.Expr(value=ast.Constant(value=Ellipsis))]
        node.body = new_body
        return node


def skeletonize_python_code(source_code: str, preserve_docstrings: bool = True) -> str:
    """
    Parses Python code and produces a compact skeleton with all method/function
    bodies replaced by `...`, retaining imports, class structures, signatures, and type hints.
    """
    try:
        tree = ast.parse(source_code)
        transformer = SkeletonTransformer(preserve_docstrings=preserve_docstrings)
        transformed_tree = transformer.visit(tree)
        ast.fix_missing_locations(transformed_tree)
        return ast.unparse(transformed_tree)
    except Exception:
        # Fallback to regex-based signature extraction if syntax is unparseable
        return _regex_fallback_skeleton(source_code)


def _regex_fallback_skeleton(source: str) -> str:
    """Extracts function and class signatures using regex as a resilient fallback."""
    skeleton_lines = []
    for line in source.splitlines():
        trimmed = line.strip()
        if (
            trimmed.startswith("import ")
            or trimmed.startswith("from ")
            or trimmed.startswith("class ")
            or trimmed.startswith("def ")
            or trimmed.startswith("async def ")
            or trimmed.startswith("@")
        ):
            skeleton_lines.append(line)
        elif trimmed.startswith('"""') or trimmed.startswith("'''"):
            skeleton_lines.append(f"    # {trimmed[:80]}")
    return "\n".join(skeleton_lines)


def skeletonize_markdown(content: str) -> str:
    """Extracts markdown structure (headers and summary lists)."""
    lines = []
    for line in content.splitlines():
        trimmed = line.strip()
        if trimmed.startswith("#") or (trimmed.startswith("- [") and len(trimmed) < 100):
            lines.append(line)
    return "\n".join(lines) if lines else content[:200]


def skeletonize_generic(content: str, max_lines: int = 50) -> str:
    """Extracts interface/class definitions from JS/TS or generic files."""
    lines = []
    for line in content.splitlines():
        trimmed = line.strip()
        if any(
            trimmed.startswith(kw)
            for kw in [
                "import ",
                "export ",
                "interface ",
                "type ",
                "class ",
                "function ",
                "const ",
                "enum ",
                "public ",
                "private ",
                "protected ",
            ]
        ):
            lines.append(line)
    if not lines:
        return "\n".join(content.splitlines()[:max_lines])
    return "\n".join(lines)


class ASTSkeletonizer:
    """
    Manages cached AST skeletonization of repository files to dramatically
    reduce LLM prompt context size while preserving critical interface and type contracts.
    """

    def __init__(self, workspace_root: str = "."):
        self.workspace_root = os.path.abspath(workspace_root)
        self._cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}

    def skeletonize_file(self, file_path: str) -> Dict[str, Any]:
        """
        Extracts the skeleton of a given file. Uses caching based on file mtime.
        """
        abs_path = os.path.abspath(
            file_path if os.path.isabs(file_path) else os.path.join(self.workspace_root, file_path)
        )
        rel_path = os.path.relpath(abs_path, self.workspace_root).replace("\\", "/")

        if not os.path.exists(abs_path):
            return {
                "file_path": rel_path,
                "skeleton": f"# File not found: {rel_path}",
                "original_chars": 0,
                "skeleton_chars": 0,
                "reduction_pct": 0.0,
                "is_skeletonized": False,
            }

        mtime = os.path.getmtime(abs_path)
        if rel_path in self._cache and self._cache[rel_path][0] == mtime:
            return self._cache[rel_path][1]

        try:
            with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except Exception as e:
            return {
                "file_path": rel_path,
                "skeleton": f"# Failed to read {rel_path}: {e}",
                "original_chars": 0,
                "skeleton_chars": 0,
                "reduction_pct": 0.0,
                "is_skeletonized": False,
            }

        orig_len = len(content)

        if rel_path.endswith(".py"):
            skeleton_text = skeletonize_python_code(content)
        elif rel_path.endswith(".md"):
            skeleton_text = skeletonize_markdown(content)
        elif rel_path.endswith((".ts", ".tsx", ".js", ".jsx")):
            skeleton_text = skeletonize_generic(content)
        else:
            skeleton_text = skeletonize_generic(content, max_lines=40)

        skel_len = len(skeleton_text)
        reduction = round((1 - (skel_len / max(1, orig_len))) * 100, 2) if orig_len > 0 else 0.0

        result = {
            "file_path": rel_path,
            "skeleton": skeleton_text,
            "original_chars": orig_len,
            "skeleton_chars": skel_len,
            "reduction_pct": max(0.0, reduction),
            "is_skeletonized": True,
        }

        self._cache[rel_path] = (mtime, result)
        return result

    def format_dependencies_context(
        self,
        file_paths: List[str],
        max_total_chars: int = 16000,
    ) -> str:
        """
        Builds a structured dependencies context block using AST skeletons,
        guaranteeing it stays strictly within the character/token budget.
        """
        if not file_paths:
            return ""

        blocks = []
        total_chars = 0

        for path in file_paths:
            res = self.skeletonize_file(path)
            header = f"### [AST SKELETON] File: {res['file_path']} (Saved {res['reduction_pct']}% tokens)\n```python\n"
            footer = "\n```\n"
            block_content = header + res["skeleton"] + footer

            if total_chars + len(block_content) > max_total_chars:
                # Add truncated notice and break
                blocks.append(
                    f"### [AST SKELETON] {res['file_path']} (Truncated due to context budget)\n"
                )
                break

            blocks.append(block_content)
            total_chars += len(block_content)

        if not blocks:
            return ""

        return "\n--- REFERENCED REPOSITORY DEPENDENCY INTERFACES (AST SKELETONS) ---\n" + "\n".join(
            blocks
        )
