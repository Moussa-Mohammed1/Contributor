"""Safe Git operations built on GitPython plus subprocess for network ops.

Design principles:
  * never overwrite user work: every mutation is staged on a clean base,
  * everything is time-boxed (timeouts on network operations),
  * every operation is explainable through structured logging.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from git import InvalidGitRepositoryError, NoSuchPathError, Repo
from git.exc import GitCommandError

from keeper.core.exceptions import (
    ConflictError,
    DirtyRepositoryError,
    GitOperationError,
    RepositoryError,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class GitStatus:
    """Snapshot of a repository working tree."""

    is_repo: bool = False
    branch: str | None = None
    staged: list[str] = field(default_factory=list)
    unstaged: list[str] = field(default_factory=list)
    untracked: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    stash_count: int = 0
    merge_state: bool = False

    @property
    def dirty(self) -> bool:
        return bool(self.staged or self.unstaged or self.untracked or self.conflicts)

    @property
    def safe(self) -> bool:
        return self.is_repo and not self.conflicts and not self.merge_state and not self.dirty


@dataclass(slots=True)
class LastCommit:
    hash: str
    message: str
    author: str
    when: datetime | None


class GitEngine:
    """Thin, safe wrapper around git for one or many repositories."""

    def __init__(self, git_binary: str = "git", network_timeout: int = 60) -> None:
        self._git_binary = git_binary
        self._network_timeout = network_timeout

    # ------------------------------------------------------------------
    # Low-level subprocess helper (used for network and timeout control)
    # ------------------------------------------------------------------

    def _run(self, cwd: Path, args: list[str], *, timeout: int | None = None,
             check: bool = False) -> subprocess.CompletedProcess[str]:
        cmd = [self._git_binary, *args]
        try:
            result = subprocess.run(
                cmd,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=timeout or self._network_timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise GitOperationError(
                f"git {args[0]} timed out after {timeout or self._network_timeout}s", " ".join(args)
            ) from exc
        except OSError as exc:
            raise GitOperationError(f"git binary failed to run: {exc}", " ".join(args)) from exc
        if check and result.returncode != 0:
            raise GitOperationError(
                f"git {' '.join(args)} failed: {result.stderr.strip() or result.stdout.strip()}",
                " ".join(args),
                result.stderr,
            )
        return result

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def open_repo(self, path: str | Path) -> Repo:
        """Open a repository or raise RepositoryError."""
        try:
            return Repo(str(path))
        except (InvalidGitRepositoryError, NoSuchPathError) as exc:
            raise RepositoryError(f"Not a git repository: {path}") from exc

    @staticmethod
    def is_git_repo(path: str | Path) -> bool:
        return (Path(path) / ".git").exists()

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def status(self, path: str | Path) -> GitStatus:
        repo_path = Path(path)
        result = GitStatus(is_repo=self.is_git_repo(repo_path))
        if not result.is_repo:
            return result
        try:
            repo = self.open_repo(repo_path)
            result.branch = self.current_branch(repo)
            result.stash_count = len([s for s in repo.git.stash("list").splitlines() if s.strip()])
            result.merge_state = bool(repo.index.unmerged_blobs()) or self._in_merge_state(repo)
            porcelain = repo.git.status("--porcelain=v1", with_extended_output=False)
            for line in porcelain.splitlines():
                if not line.strip():
                    continue
                raw = line.rstrip("\n")
                code = raw[:2] if len(raw) >= 2 else raw
                file_path = raw[3:]
                if code == "??":
                    result.untracked.append(file_path)
                elif code in ("UU", "AA", "DD", "AU", "UA", "DU", "UD"):
                    result.conflicts.append(file_path)
                elif code[0] in "MADRCT":
                    result.staged.append(file_path)
                elif code[1] in "MDRT":
                    result.unstaged.append(file_path)
                elif code[1] in "?":
                    result.untracked.append(file_path)
        except GitCommandError as exc:
            logger.warning("git status failed in %s: %s", repo_path, exc)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to inspect git status in %s", repo_path)
        return result

    def _in_merge_state(self, repo: Repo) -> bool:
        git_dir = Path(repo.git_dir)
        return any((git_dir / f).exists() for f in ("MERGE_HEAD", "REBASE_HEAD", "CHERRY_PICK_HEAD"))

    def require_clean(self, path: str | Path) -> GitStatus:
        """Raise DirtyRepositoryError / ConflictError when the tree is not safe."""
        status = self.status(path)
        if not status.is_repo:
            raise RepositoryError(f"Not a git repository: {path}")
        if status.conflicts:
            raise ConflictError(
                f"Repository {path} has unresolved conflicts: {', '.join(status.conflicts)}"
            )
        if status.merge_state:
            raise ConflictError(f"Repository {path} is in a merge/rebase/cherry-pick state")
        if status.dirty:
            raise DirtyRepositoryError(
                f"Repository {path} is dirty "
                f"({len(status.staged)} staged, {len(status.unstaged)} unstaged, "
                f"{len(status.untracked)} untracked). Refusing to operate."
            )
        return status

    # ------------------------------------------------------------------
    # Branch handling
    # ------------------------------------------------------------------

    def current_branch(self, repo: Repo) -> str | None:
        try:
            if repo.head.is_detached:
                return None
            return repo.active_branch.name
        except (TypeError, ValueError):  # noqa: BUG001
            return None

    def default_branch(self, path: str | Path) -> str:
        """Detect the default branch: current, remote HEAD, or main."""
        try:
            repo = self.open_repo(path)
            current = self.current_branch(repo)
            if current:
                return current
            if repo.remotes:
                symbolic = repo.git.symbolic_ref("refs/remotes/origin/HEAD", with_stderr=True)
                short = symbolic.split("/", 2)[-1].strip()
                if short:
                    return short
            for candidate in ("main", "master", "develop"):
                if candidate in repo.branches:
                    return candidate
        except Exception:  # noqa: BLE001
            logger.debug("Could not detect default branch for %s", path, exc_info=True)
        return "main"

    def switch_to(self, path: str | Path, branch: str) -> None:
        """Checkout the configured branch if the repo is on another branch.

        No-op when already on the branch. Detached HEAD is checked out.
        """
        try:
            repo = self.open_repo(path)
            if self.current_branch(repo) == branch:
                return
            if branch in repo.branches:
                repo.git.checkout(branch)
            else:
                repo.git.checkout("-b", branch)
            logger.info("Switched %s to branch %s", path, branch)
        except GitCommandError as exc:
            raise GitOperationError(f"Failed to switch branch in {path}: {exc}") from exc

    # ------------------------------------------------------------------
    # Staging / committing
    # ------------------------------------------------------------------

    def stage(self, path: str | Path, files: list[str]) -> None:
        repo_path = Path(path)
        if not files:
            return
        repo = self.open_repo(repo_path)
        repo.index.add([str(repo_path / f) for f in files])
        logger.debug("Staged %d files in %s", len(files), repo_path)

    def commit(self, path: str | Path, message: str, *, author_name: str | None = None,
               author_email: str | None = None) -> str:
        repo = self.open_repo(path)
        actor = None
        if author_name and author_email:
            from git import Actor

            actor = Actor(author_name, author_email)
        try:
            repo.git.add("-A", ".")
            commit = repo.index.commit(message, author=actor, committer=actor)
            hash_ = str(commit.hexsha)
            logger.info("Committed %s in %s: %s", hash_[:10], path, message.splitlines()[0])
            return hash_
        except GitCommandError as exc:
            raise GitOperationError(f"Commit failed in {path}: {exc}") from exc

    def restore(self, path: str | Path, files: list[str]) -> None:
        """Discard changes in tracked files and remove created-but-untracked ones."""
        repo_path = Path(path)
        tracked = [f for f in files if not (repo_path / f).exists() or self._is_tracked(repo_path, f)]
        untracked = [f for f in files if f not in tracked]
        if tracked:
            self._run(repo_path, ["restore", "--", *tracked], check=False)
            logger.info("Restored %d tracked files in %s", len(tracked), repo_path)
        if untracked:
            for file_name in untracked:
                try:
                    (repo_path / file_name).unlink(missing_ok=True)
                except OSError as exc:
                    logger.warning("Could not remove untracked file %s: %s", file_name, exc)
            logger.info("Removed %d untracked files in %s", len(untracked), repo_path)

    def reset_index(self, path: str | Path) -> None:
        """Unstage everything without touching the working tree."""
        repo_path = Path(path)
        self._run(repo_path, ["reset", "--", "."], check=False)

    def _is_tracked(self, repo_path: Path, file_name: str) -> bool:
        result = self._run(repo_path, ["ls-files", "--error-unmatch", "--", file_name], check=False)
        return result.returncode == 0

    # ------------------------------------------------------------------
    # Push
    # ------------------------------------------------------------------

    def push(self, path: str | Path, branch: str | None = None, *, timeout: int | None = None) -> None:
        """Push the current (or given) branch to its upstream remote.

        Raises GitOperationError with remote output when the push fails.
        """
        repo_path = Path(path)
        args = ["push"]
        if branch:
            args.append(branch)
        result = self._run(repo_path, args, timeout=timeout)
        if result.returncode != 0:
            logger.error("Push failed in %s: %s", repo_path, result.stderr.strip())
            raise GitOperationError(
                f"Push failed for {repo_path}: {result.stderr.strip() or result.stdout.strip()}",
                "push", result.stderr,
            )
        logger.info("Pushed %s to remote", repo_path)

    def has_remote(self, path: str | Path) -> bool:
        try:
            repo = self.open_repo(path)
            return bool(repo.remotes)
        except RepositoryError:
            return False

    def remote_url(self, path: str | Path) -> str | None:
        try:
            repo = self.open_repo(path)
            if repo.remotes:
                return repo.remotes[0].url
        except RepositoryError:
            pass
        return None

    def unpushed_commits(self, path: str | Path) -> int:
        repo_path = Path(path)
        result = self._run(
            repo_path, ["log", "--branches", "--not", "--remotes", "--oneline"], check=False
        )
        return len([line for line in result.stdout.splitlines() if line.strip()])

    def remote_reachable(self, path: str | Path, *, timeout: int = 15) -> bool:
        """Lightweight connectivity check against the origin remote."""
        repo_path = Path(path)
        url = self.remote_url(path)
        if not url:
            return False
        result = self._run(repo_path, ["ls-remote", "--heads", "origin"], timeout=timeout)
        return result.returncode == 0

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    def last_commit(self, path: str | Path) -> LastCommit | None:
        repo_path = Path(path)
        result = self._run(
            repo_path,
            ["log", "-1", "--format=%H%x00%an%x00%ct%x00%s"],
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        hash_, author, ts, message = result.stdout.strip().split("\x00", 3)
        when = datetime.fromtimestamp(int(ts), tz=UTC)
        return LastCommit(hash=hash_, message=message, author=author, when=when)

    def recent_commits(self, path: str | Path, *, since: datetime | None = None,
                       limit: int = 50) -> list[LastCommit]:
        """Return recent commits, optionally filtered by ``since``."""
        repo_path = Path(path)
        args = ["log", "-n", str(limit), "--format=%H%x00%an%x00%ct%x00%s"]
        if since is not None:
            args = ["log", "-n", str(limit), "--since", since.astimezone().isoformat(),
                    "--format=%H%x00%an%x00%ct%x00%s"]
        result = self._run(repo_path, args, check=False)
        commits: list[LastCommit] = []
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            hash_, author, ts, message = line.split("\x00", 3)
            commits.append(
                LastCommit(
                    hash=hash_,
                    author=author,
                    message=message,
                    when=datetime.fromtimestamp(int(ts), tz=UTC),
                )
            )
        return commits

    def find_commit_by_message(self, path: str | Path, message: str, *, since: datetime | None = None) -> str | None:
        """Crash recovery: locate a commit whose subject matches ``message``.

        Used after an unclean shutdown to detect whether a commit that was
        marked RUNNING actually landed.
        """
        repo_path = Path(path)
        subject = message.splitlines()[0][:60]
        args = ["log", "--all", f"--grep={subject}", "--format=%H%x00%s", "-n", "5"]
        if since is not None:
            args = ["log", "--all", f"--grep={subject}", f"--since={since.isoformat()}",
                    "--format=%H%x00%s", "-n", "5"]
        result = self._run(repo_path, args, check=False)
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            hash_, subject_line = line.split("\x00", 1)
            if subject_line.startswith(subject[:40]):
                return hash_
        return None

    def files_in_commit(self, path: str | Path, hash_: str) -> list[str]:
        repo_path = Path(path)
        result = self._run(
            repo_path, ["show", "--format=", "--name-only", hash_], check=False
        )
        return [line for line in result.stdout.splitlines() if line.strip()]
