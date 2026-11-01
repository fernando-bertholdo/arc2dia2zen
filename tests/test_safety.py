"""Static guarantees about the package source."""

import ast
import json
from pathlib import Path

import fixturegen

PKG = Path(__file__).resolve().parent.parent / "arc2dia2zen"
SOURCES = sorted(PKG.glob("*.py"))

NETWORK_MODULES = {"socket", "ssl", "http", "urllib.request", "urllib3", "requests", "ftplib", "smtplib",
                   "telnetlib", "xmlrpc", "asyncio", "httpx", "aiohttp"}
STDLIB_OK = {"__future__", "argparse", "ast", "collections", "copy", "dataclasses", "hashlib", "html", "itertools",
             "json", "os", "pathlib", "re", "shutil", "subprocess", "sys", "time", "typing", "urllib.parse", "uuid"}


def imported_modules(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            yield node.module
            if node.module == "html":
                pass


def test_no_network_modules_and_stdlib_only():
    for src in SOURCES:
        for mod in imported_modules(src):
            assert mod not in NETWORK_MODULES and mod.split(".")[0] not in {"socket", "ssl", "requests"}, (src, mod)
            if mod.startswith("html"):
                continue
            assert mod in STDLIB_OK, f"{src.name} imports {mod}"


def test_never_deletes_files():
    banned = ("os.remove", "os.unlink", "shutil.rmtree", ".unlink(", "os.rmdir", ".rmdir(", "send2trash")
    for src in SOURCES:
        text = src.read_text(encoding="utf-8")
        for word in banned:
            assert word not in text, f"{src.name} uses {word}"


def test_only_known_subprocesses():
    for src in SOURCES:
        text = src.read_text(encoding="utf-8")
        if "subprocess.run(" in text:
            assert src.name in {"swap.py", "dia.py"}, src.name


def test_example_file_matches_generator():
    assert json.loads(fixturegen.EXAMPLE_PATH.read_text(encoding="utf-8")) == fixturegen.rich_sidebar()


def test_fixtures_use_reserved_domains_only():
    raw = json.dumps(fixturegen.rich_sidebar())
    import re

    hosts = set(re.findall(r"https?://([^/\"?]+)", raw))
    assert hosts and all(h.endswith((".example.com", ".example.org", ".example.net")) for h in hosts), hosts
