import ast
from pathlib import Path

RECORDS = ("docs", "README.md", "CHANGELOG.md")
TESTS = Path(__file__).parent
Parts = tuple[str, ...]


def segments(text: str) -> Parts:
    return tuple(part for part in text.split("/") if part and part != ".")


class Scan:
    def __init__(self, source: Path) -> None:
        self.source = source
        self.tree = ast.parse(source.read_text(encoding="utf-8"))
        self.names: dict[str, Parts] = {"PLUGIN_ROOT": ()}
        self.hits: set[tuple[int, str]] = set()
        self.dynamic: set[tuple[int, str]] = set()
        assigns = sorted(
            (node for node in ast.walk(self.tree) if isinstance(node, ast.Assign | ast.AnnAssign)),
            key=lambda node: node.lineno,
        )
        for _ in range(2):
            for node in assigns:
                self.assign(node)
        for node in ast.walk(self.tree):
            self.visit(node)

    def assign(self, node: ast.Assign | ast.AnnAssign) -> None:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if node.value is None or len(targets) != 1 or not isinstance(targets[0], ast.Name):
            return
        found = self.path(node.value)
        if found is not None:
            self.names[targets[0].id] = found

    def path(self, node: ast.expr) -> Parts | None:
        match node:
            case ast.Name(id=name):
                return self.names.get(name)
            case ast.Call(func=ast.Name(id="Path"), args=[ast.Name(id="__file__"), *_]):
                return ("hooks", "tests", self.source.name)
            case ast.Call(func=ast.Name(id="Path"), args=[first, *_]):
                return self.path(first)
            case ast.Call(func=ast.Attribute(value=base, attr="resolve" | "absolute")):
                return self.path(base)
            case ast.Call(func=ast.Attribute(value=base, attr="joinpath"), args=args):
                return self.joined(base, args, node)
        return self.step(node)

    def step(self, node: ast.expr) -> Parts | None:
        match node:
            case ast.Attribute(value=base, attr="parent"):
                found = self.path(base)
                return found[:-1] if found else None
            case ast.Subscript(
                value=ast.Attribute(value=base, attr="parents"), slice=ast.Constant(value=int(up))
            ):
                found = self.path(base)
                return found[: len(found) - up - 1] if found and len(found) > up else None
            case ast.BinOp(left=base, op=ast.Div(), right=right):
                return self.joined(base, [right], node)
        return None

    def joined(self, base: ast.expr, tail: list[ast.expr], node: ast.expr) -> Parts | None:
        found = self.path(base)
        if found is None:
            return None
        for part in tail:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                found = (*found, *segments(part.value))
            else:
                self.dynamic.add((node.lineno, "/".join(found)))
        return found

    def hit(self, node: ast.expr, found: Parts | None) -> None:
        if found and found[0] in RECORDS:
            self.hits.add((node.lineno, "/".join(found)))

    def visit(self, node: ast.AST) -> None:
        if isinstance(node, ast.BinOp | ast.Attribute | ast.Subscript | ast.Name):
            self.hit(node, self.path(node))
        if not isinstance(node, ast.Call):
            return
        self.hit(node, self.path(node))
        match node.func:
            case ast.Attribute(value=base, attr="glob" | "rglob" | "iterdir" as how):
                found = self.path(base)
                if found is None:
                    return
                tail = segments(self.pattern(node)) if how != "iterdir" else ()
                if found or tail:
                    self.hit(node, (*found, *tail))
                else:
                    self.hits.add((node.lineno, "*"))
            case ast.Name(id="Path" | "open"):
                first = node.args[0] if node.args else None
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    relative = segments(first.value)
                    if relative and relative[0] in RECORDS:
                        self.hits.add((node.lineno, "/".join(relative)))

    def pattern(self, node: ast.Call) -> str:
        first = node.args[0] if node.args else None
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return first.value
        self.dynamic.add((node.lineno, "glob"))
        return "*"


def reads() -> dict[str, tuple[set[tuple[int, str]], set[tuple[int, str]]]]:
    scans = {path.name: Scan(path) for path in sorted(TESTS.glob("*.py"))}
    return {name: (scan.hits, scan.dynamic) for name, scan in scans.items() if scan.hits}
