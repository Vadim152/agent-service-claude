from __future__ import annotations

from zipfile import ZipFile

from agents.repo_scanner_agent import RepoScannerAgent


class _StepIndexStoreStub:
    def __init__(self) -> None:
        self.saved_project_root: str | None = None
        self.saved_steps = []

    def save_steps(self, project_root: str, steps) -> None:  # noqa: ANN001
        self.saved_project_root = project_root
        self.saved_steps = list(steps)


class _EmbeddingsStoreStub:
    def __init__(self) -> None:
        self.indexed_project_root: str | None = None
        self.indexed_steps = []

    def index_steps(self, project_root: str, steps) -> None:  # noqa: ANN001
        self.indexed_project_root = project_root
        self.indexed_steps = list(steps)


def _step_source(step_text: str) -> str:
    return f"""
        package steps

        class UiSteps {{
            @Given("{step_text}")
            fun openSite() {{}}
        }}
    """.strip()


def test_scan_repository_collects_steps_from_project_and_dependency_roots(tmp_path) -> None:
    project_root = tmp_path / "project"
    dep_root = tmp_path / "dep-src"
    project_root.mkdir()
    dep_root.mkdir()

    (project_root / "LocalSteps.kt").write_text(_step_source("open local app"), encoding="utf-8")
    (project_root / "LocalBindings.kt").write_text(_step_source("skip local binding"), encoding="utf-8")
    (dep_root / "FrameworkBindings.kt").write_text(_step_source("open dependency app"), encoding="utf-8")

    jar_path = tmp_path / "dep-sources.jar"
    with ZipFile(jar_path, "w") as archive:
        archive.writestr("com/example/FrameworkBindings.kt", _step_source("open jar app"))

    step_store = _StepIndexStoreStub()
    embeddings_store = _EmbeddingsStoreStub()
    scanner = RepoScannerAgent(step_store, embeddings_store)

    result = scanner.scan_repository(
        str(project_root),
        additional_roots=[str(dep_root), str(jar_path)],
    )

    patterns = {step.pattern for step in step_store.saved_steps}
    assert "open local app" in patterns
    assert "open dependency app" in patterns
    assert "open jar app" in patterns
    assert "skip local binding" not in patterns

    assert result["stepsCount"] == len(step_store.saved_steps)
    assert embeddings_store.indexed_project_root == str(project_root)

    local_steps = [step for step in step_store.saved_steps if step.pattern == "open local app"]
    dependency_steps = [step for step in step_store.saved_steps if step.pattern != "open local app"]

    assert local_steps and all(not step.id.startswith("dep[") for step in local_steps)
    assert dependency_steps and all(step.id.startswith("dep[") for step in dependency_steps)
    assert any(
        "dep-sources.jar!/com/example/FrameworkBindings.kt" in (step.implementation.file or "")
        for step in dependency_steps
    )


def test_scan_repository_ignores_binary_only_jars(tmp_path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "LocalSteps.kt").write_text(_step_source("open local app"), encoding="utf-8")

    binary_jar = tmp_path / "dep-binary.jar"
    with ZipFile(binary_jar, "w") as archive:
        archive.writestr("com/example/BinarySteps.class", b"\xca\xfe\xba\xbe")

    step_store = _StepIndexStoreStub()
    embeddings_store = _EmbeddingsStoreStub()
    scanner = RepoScannerAgent(step_store, embeddings_store)

    result = scanner.scan_repository(
        str(project_root),
        additional_roots=[str(binary_jar)],
    )

    patterns = [step.pattern for step in step_store.saved_steps]
    assert patterns == ["open local app"]
    assert result["stepsCount"] == 1
