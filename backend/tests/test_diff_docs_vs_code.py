"""Tests for the doc-drift guard at scripts/diff_docs_vs_code.py.

Exercises the helper functions in isolation rather than spawning the
script as a subprocess — the script is intentionally a single-file
module with no third-party deps so we can import it directly.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
SCRIPT_PATH = SCRIPTS_DIR / "diff_docs_vs_code.py"


def _load_script():
    """Load the script as a module (it isn't a package)."""
    spec = importlib.util.spec_from_file_location("diff_docs_vs_code", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None, "script not found"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def script():
    return _load_script()


def test_normalize_collapses_path_param_names(script):
    # ``{id}`` and ``{source_id}`` are the same template for diff purposes.
    assert script._normalize("/data-sources/{source_id}") == script._normalize(
        "/data-sources/{id}"
    )
    assert script._normalize("/reports/{report_id}/items") == script._normalize(
        "/reports/{x}/items"
    )


def test_normalize_strips_trailing_slash(script):
    assert script._normalize("/foo/") == "/foo"
    assert script._normalize("/") == "/"


def test_extract_path_decorator_method_and_path(script):
    # ast round-trip on a minimal module
    import ast

    src = """
from fastapi import APIRouter
router = APIRouter(prefix="/things")

@router.get("/{thing_id}")
def get_thing(thing_id: int):
    pass

@router.post("")
def create_thing():
    pass
"""
    tree = ast.parse(src)
    paths = [
        script._extract_path(d)
        for fn in ast.walk(tree)
        if isinstance(fn, ast.FunctionDef)
        for d in fn.decorator_list
    ]
    paths = [p for p in paths if p is not None]
    assert ("get", "/{thing_id}") in paths
    assert ("post", "") in paths


def test_join_path_glues_prefix_and_path(script):
    assert script._join_path("/data-sources", "/{id}") == "/data-sources/{id}"
    assert script._join_path("/data-sources", "") == "/data-sources"
    assert script._join_path("", "/foo") == "/foo"
    assert script._join_path("", "") == "/"
    # Normalise double slashes
    assert script._join_path("/data-sources/", "/{id}") == "/data-sources/{id}"


def test_api_paths_in_readme_parses_method_and_path(script):
    sample = """
## API 端点

### 认证

| 方法 | 路径 | 功能 |
|------|------|------|
| POST | `/auth/login` | 登录 |
| GET | `/auth/me` | 当前用户 |

### 报表

| 方法 | 路径 | 功能 |
|------|------|------|
| GET | `/reports` | list |
"""
    found = script.api_paths_in_readme(sample)
    assert "POST /auth/login" in found
    assert "GET /auth/me" in found
    assert "GET /reports" in found
    # The header row's "方法" cell should not be parsed as a method
    assert "方法 /路径" not in found


def test_api_paths_in_readme_strips_section_at_eof(script):
    """README may end mid-section (no next `## ` heading)."""
    sample = "## API 端点\n\n| GET | `/foo` | ok |\n"
    found = script.api_paths_in_readme(sample)
    assert "GET /foo" in found


def test_env_vars_in_deploy_extracts_backtick_names(script):
    sample = """
## 环境变量配置

| 变量名 | 默认值 |
|--------|--------|
| `FOO_BAR` | baz |
| `JWT_SECRET_KEY` | empty |
"""
    found = script.env_vars_in_deploy(sample)
    assert "FOO_BAR" in found
    assert "JWT_SECRET_KEY" in found


def test_env_vars_in_deploy_excludes_prose_mentions(script):
    """A name mentioned only outside the env-var section is not 'documented'."""
    sample = """
## 环境变量配置

| 变量名 | 默认值 |
|--------|--------|
| `FOO_BAR` | baz |

## 生产环境检查清单

- [ ] 设置 `JWT_SECRET_KEY` 为随机长字符串
"""
    found = script.env_vars_in_deploy(sample)
    assert "FOO_BAR" in found
    assert "JWT_SECRET_KEY" not in found


def test_collect_router_paths_finds_real_routes(script):
    """Smoke test against the actual routers/ dir — guard against renames."""
    paths = script.collect_router_paths()
    assert len(paths) >= 30, f"expected >=30 router paths, got {len(paths)}"
    # Sanity: known routes must be present
    assert "POST /auth/login" in paths
    assert "GET /reports" in paths
    assert "POST /explorer/query" in paths


def test_collect_settings_fields_returns_expected_count(script):
    fields = script.collect_settings_fields()
    # 45 fields as of the 11.x batches; the test fails if someone adds a
    # field without updating DEPLOY.md AND skips the script — a cheap
    # backstop on top of the CI gate.
    assert len(fields) >= 40, f"expected >=40 Settings fields, got {len(fields)}"
    assert "APP_NAME" in fields
    assert "JWT_SECRET_KEY" in fields


def test_full_check_passes_against_current_docs(script):
    """End-to-end: current docs + current code = no drift."""
    missing_env = script.check_env_vars(strict=True, quiet=True)
    missing_paths = script.check_router_paths(strict=True, quiet=True)
    assert missing_env == [], f"DEPLOY.md missing env vars: {missing_env}"
    assert missing_paths == [], f"README.md missing routes: {missing_paths}"