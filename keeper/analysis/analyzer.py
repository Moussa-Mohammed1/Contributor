"""Local repository analysis engine.

Before anything is sent to an LLM we analyze the working tree so the prompt
can be grounded in facts: language, framework, structure, git status, build
system, package manager, formatting and linting tools, and test runners.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from keeper.core.exceptions import RepositoryError
from keeper.git.engine import GitEngine, GitStatus

logger = logging.getLogger(__name__)

# File-name based detectors. Order matters: first match wins for framework.
_MANIFEST_HINTS: dict[str, str] = {
    "package.json": "npm/Node.js",
    "pyproject.toml": "Python (pyproject)",
    "requirements.txt": "Python (pip)",
    "setup.py": "Python (setuptools)",
    "Pipfile": "Python (pipenv)",
    "poetry.lock": "Python (poetry)",
    "uv.lock": "Python (uv)",
    "go.mod": "Go (modules)",
    "Cargo.toml": "Rust (cargo)",
    "composer.json": "PHP (composer)",
    "Gemfile": "Ruby (bundler)",
    "pom.xml": "Java (maven)",
    "build.gradle": "Java (gradle)",
    "mix.exs": "Elixir (mix)",
    "pubspec.yaml": "Dart (pub)",
    "stack.yaml": "Haskell (stack)",
    "CMakeLists.txt": "C/C++ (CMake)",
    "Makefile": "Make",
    "docker-compose.yml": "Docker Compose",
    "docker-compose.yaml": "Docker Compose",
    "compose.yml": "Docker Compose",
}

_LANG_BY_EXT: dict[str, str] = {
    ".py": "Python",
    ".ts": "TypeScript",
    ".tsx": "TypeScript/React",
    ".js": "JavaScript",
    ".jsx": "JavaScript/React",
    ".go": "Go",
    ".rs": "Rust",
    ".java": "Java",
    ".kt": "Kotlin",
    ".cs": "C#",
    ".cpp": "C++",
    ".c": "C",
    ".h": "C/C++ header",
    ".rb": "Ruby",
    ".php": "PHP",
    ".swift": "Swift",
    ".lua": "Lua",
    ".sh": "Shell",
    ".ps1": "PowerShell",
    ".sql": "SQL",
    ".vue": "Vue",
    ".svelte": "Svelte",
    ".html": "HTML",
    ".css": "CSS",
    ".scss": "SCSS",
    ".less": "LESS",
    ".md": "Markdown",
    ".json": "JSON",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".toml": "TOML",
    ".proto": "Protobuf",
}

_FORMATTER_HINTS: dict[str, list[str]] = {
    "ruff format": ["pyproject.toml", "ruff.toml"],
    "black": ["pyproject.toml", ".black.toml"],
    "prettier": [".prettierrc", ".prettierrc.json", "prettier.config.js", "package.json"],
    "deno fmt": ["deno.json", "deno.jsonc"],
    "gofmt": ["go.mod"],
    "clang-format": [".clang-format"],
    "rustfmt": ["Cargo.toml"],
    "mix format": ["mix.exs"],
}

_LINTER_HINTS: dict[str, list[str]] = {
    "ruff": ["ruff.toml", "pyproject.toml"],
    "flake8": [".flake8", "setup.cfg"],
    "pylint": [".pylintrc", "pylintrc"],
    "mypy": ["mypy.ini", ".mypy.ini", "pyproject.toml"],
    "eslint": [".eslintrc", ".eslintrc.js", ".eslintrc.json", "package.json"],
    "golangci-lint": [".golangci.yml", ".golangci.yaml"],
    "go vet": ["go.mod"],
    "fmt": ["go.mod"],
    "clippy": ["Cargo.toml"],
    "php-cs-fixer": [".php-cs-fixer.php"],
}

_TEST_HINTS: dict[str, list[str]] = {
    "pytest": ["pytest.ini", "pyproject.toml", "tests"],
    "jest": ["jest.config.js", "package.json", "__tests__"],
    "vitest": ["vitest.config.ts", "vitest.config.js", "package.json"],
    "go test": ["go.mod", "_test.go"],
    "cargo test": ["Cargo.toml", "tests"],
    "phpunit": ["phpunit.xml", "tests"],
    "gradle test": ["build.gradle", "src/test"],
    "maven test": ["pom.xml", "src/test"],
}

_SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build",
    ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache", "target", "vendor",
    ".next", "coverage", "htmlcov", ".idea", ".vscode", ".mypy_cache",
}

_SKIP_FILES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "Gemfile.lock",
    "poetry.lock", "uv.lock", "Cargo.lock", "go.sum",
}

_DOC_EXTS = {".md", ".rst", ".txt", ".adoc", "readme"}


@dataclass(slots=True)
class AnalysisReport:
    """Result of a local repository analysis."""

    path: str
    exists: bool
    is_git: bool
    languages: list[str] = field(default_factory=list)
    package_manager: str | None = None
    build_system: str | None = None
    framework: str | None = None
    formatters: list[str] = field(default_factory=list)
    linters: list[str] = field(default_factory=list)
    test_runners: list[str] = field(default_factory=list)
    has_tests: bool = False
    folders: list[str] = field(default_factory=list)
    file_count: int = 0
    git_status: GitStatus | None = None
    ignore_patterns: list[str] = field(default_factory=list)
    branch: str | None = None
    doc_files: list[str] = field(default_factory=list)

    def to_prompt_json(self) -> dict:
        return {
            "repository_path": self.path,
            "exists": self.exists,
            "is_git_repo": self.is_git,
            "languages": self.languages,
            "package_manager": self.package_manager,
            "build_system": self.build_system,
            "framework": self.framework,
            "formatters": self.formatters,
            "linters": self.linters,
            "test_runners": self.test_runners,
            "has_tests": self.has_tests,
            "top_level_folders": self.folders,
            "file_count": self.file_count,
            "branch": self.branch,
            "git_clean": not self.git_status.dirty if self.git_status else None,
            "documents": self.doc_files[:20],
        }


def _has_any(entries: list[str], hints: list[str]) -> bool:
    for hint in hints:
        if hint in {e.lower() for e in entries}:
            return True
        if hint.endswith("_test.go") or hint.startswith("_test."):
            if any(hint[1:-1] in e for e in entries):
                return True
    return False


class RepositoryAnalyzer:
    """Analyzes a repository on disk without calling any external service."""

    def __init__(self, git: GitEngine | None = None) -> None:
        self._git = git or GitEngine()

    def analyze(self, path: str, *, ignore_patterns: list[str] | None = None) -> AnalysisReport:
        root = Path(path)
        ignore_patterns = ignore_patterns or []
        report = AnalysisReport(path=str(root), exists=root.exists(), is_git=self._git.is_git_repo(root))
        if not report.exists:
            raise RepositoryError(f"Repository path does not exist: {path}")

        self._scan_files(root, report, ignore_patterns)
        self._detect_signals(root, report)
        report.git_status = self._git.status(root)
        report.branch = self._git.current_branch(self._git.open_repo(root)) if report.is_git else None
        return report

    # ------------------------------------------------------------------

    def _scan_files(self, root: Path, report: AnalysisReport, ignore_patterns: list[str]) -> None:
        lang_counts: dict[str, int] = {}
        counts_by_ext: dict[str, int] = {}
        folders: set[str] = set()
        doc_files: list[str] = []
        file_count = 0

        for item in root.rglob("*"):
            try:
                rel = item.relative_to(root)
            except ValueError:
                continue
            parts = set(rel.parts[:-1])
            if parts & _SKIP_DIRS:
                continue
            if any(p in ignore_patterns for p in rel.parts):
                continue
            if item.is_dir():
                if len(rel.parts) == 1:
                    folders.add(item.name)
                continue
            file_count += 1
            ext = item.suffix.lower()
            name = item.name.lower()
            if name in _SKIP_FILES:
                continue
            counts_by_ext[ext] = counts_by_ext.get(ext, 0) + 1
            lang = _LANG_BY_EXT.get(ext)
            if lang:
                lang_counts[lang] = lang_counts.get(lang, 0) + 1
            if ext in _DOC_EXTS or name in {"readme", "license", "contributing", "changelog"}:
                doc_files.append(str(rel))

        report.file_count = file_count
        report.folders = sorted(folders)
        report.doc_files = sorted(doc_files)
        report.languages = [lang for lang, _ in sorted(lang_counts.items(), key=lambda kv: -kv[1])]

    def _detect_signals(self, root: Path, report: AnalysisReport) -> None:
        """Detect package manager, build system, framework and tooling."""
        entries = sorted(p.name.lower() for p in root.iterdir() if p.is_file())

        for manifest, label in _MANIFEST_HINTS.items():
            if manifest.lower() in entries:
                if report.package_manager is None:
                    report.package_manager = label
                break

        for manifest, label in _MANIFEST_HINTS.items():
            if manifest.lower() in entries:
                report.build_system = label
                break

        source_roots = {p.name.lower() for p in root.iterdir() if p.is_dir()}
        if "src" in source_roots or "source" in source_roots:
            report.framework = "src-layout project"
        elif report.languages:
            report.framework = report.languages[0]

        for tool, hints in _FORMATTER_HINTS.items():
            if _has_any(entries, hints):
                report.formatters.append(tool)
        for tool, hints in _LINTER_HINTS.items():
            if _has_any(entries, hints):
                report.linters.append(tool)

        all_names = set(entries) | {p.name.lower() for p in root.rglob("*") if p.is_file()}
        for runner, hints in _TEST_HINTS.items():
            if _has_any(list(all_names), hints):
                report.test_runners.append(runner)
                report.has_tests = True

        report.formatters = sorted(set(report.formatters))
        report.linters = sorted(set(report.linters))
        report.test_runners = sorted(set(report.test_runners))
        return None