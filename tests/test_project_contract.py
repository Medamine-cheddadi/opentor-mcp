"""Lightweight packaging and documentation compatibility contracts."""

import re
import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_URL = "https://github.com/Medamine-cheddadi/opentor-mcp"


def test_mcp_dependency_has_a_floor_that_supports_native_content_results():
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = project["project"]["dependencies"]
    mcp = next(Requirement(item) for item in dependencies if Requirement(item).name == "mcp")
    lower_bounds = [Version(spec.version) for spec in mcp.specifier if spec.operator in {">=", ">"}]

    assert lower_bounds, "the MCP SDK must have an explicit compatible lower bound"
    assert max(lower_bounds) >= Version("1.20.0")


def test_readme_documents_javascript_as_disabled_by_default_opt_in():
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    javascript_section = re.search(
        r"TOR_ALLOW_JAVASCRIPT.{0,400}", readme, flags=re.IGNORECASE | re.DOTALL
    )

    assert javascript_section, "README must document TOR_ALLOW_JAVASCRIPT"
    description = javascript_section.group(0).lower()
    assert "false" in description or "disabled" in description


def test_readme_advertised_tool_total_matches_its_tool_table_rows():
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    advertised = re.search(r"Tools\s*\((\d+)\s+total\)", readme, flags=re.IGNORECASE)
    documented_names = set(re.findall(r"\|\s*`(tor_[a-z0-9_]+)`\s*\|", readme))

    assert advertised, "README should advertise the tool count"
    assert int(advertised.group(1)) == len(documented_names)


def test_sdist_configuration_excludes_local_crawl_artifacts():
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    sdist = project["tool"]["hatch"]["build"]["targets"]["sdist"]
    excluded = set(sdist["exclude"])

    assert {
        "/crawl_data*",
        "/knowledge_base",
        "/thread_content",
        "/crawler*.py",
        "/content_reader*.py",
        "/top*_urls.json",
        "/sessions",
        "/archives",
        "/**/*.log",
        "/**/*.png",
    } <= excluded


def test_public_package_metadata_matches_the_github_project():
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]

    assert project["name"] == "opentor-mcp"
    assert project["urls"]["Repository"] == REPOSITORY_URL
    assert project["urls"]["Issues"] == f"{REPOSITORY_URL}/issues"
    assert project["license"] == {"file": "LICENSE"}
    assert "autonomous" not in project["description"].lower()
    assert {"mcp", "tor", "playwright"} <= set(project["keywords"])
    assert any("Development Status :: 3 - Alpha" in item for item in project["classifiers"])


def test_open_source_community_files_exist():
    required_files = [
        "LICENSE",
        "CONTRIBUTING.md",
        "SECURITY.md",
        "CODE_OF_CONDUCT.md",
        ".github/pull_request_template.md",
        ".github/ISSUE_TEMPLATE/bug_report.yml",
        ".github/ISSUE_TEMPLATE/feature_request.yml",
        ".github/ISSUE_TEMPLATE/config.yml",
    ]

    missing = [path for path in required_files if not (PROJECT_ROOT / path).is_file()]
    assert missing == []


def test_readme_is_ready_for_the_public_repository():
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    assert readme.startswith("# OpenTor MCP")
    assert REPOSITORY_URL in readme
    assert "git clone <this-repo>" not in readme
    assert "/path/to/tor-mcp" not in readme
    assert "not Tor Browser" in readme
    assert "responsible" in readme.lower()
    assert "CONTRIBUTING.md" in readme
    assert "SECURITY.md" in readme


def test_public_copy_does_not_claim_unsupervised_autonomy():
    public_files = [
        PROJECT_ROOT / "README.md",
        PROJECT_ROOT / "pyproject.toml",
        PROJECT_ROOT / "src/tor_mcp/__init__.py",
        PROJECT_ROOT / "src/tor_mcp/server.py",
    ]
    copy = "\n".join(path.read_text(encoding="utf-8") for path in public_files).lower()

    assert "fully autonomous" not in copy


def test_ci_uses_current_node_runtime_actions():
    workflow = (PROJECT_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "actions/checkout@v7" in workflow
    assert "actions/setup-python@v7" in workflow


def test_local_account_store_is_excluded_from_public_artifacts():
    ignore_rules = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    workflow = (PROJECT_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    excluded = set(project["tool"]["hatch"]["build"]["targets"]["sdist"]["exclude"])

    assert "accounts.json" in ignore_rules
    assert "accounts.json" in workflow
    assert "/accounts.json" in excluded
