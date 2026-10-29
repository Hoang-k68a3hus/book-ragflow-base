import ast
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_PACKAGE_ROOT = _PROJECT_ROOT / "rag" / "source_understanding"


def test_source_understanding_uses_rag_package_namespace():
    from rag.source_understanding.adapters import DeepDocSourceAdapter
    from rag.source_understanding.artifacts import CanonicalArtifactStore
    from rag.source_understanding.pipeline import SourceUnderstandingPipeline
    from rag.source_understanding.retrieval_units import RetrievalUnitProjector
    from rag.source_understanding.schemas import CanonicalDocument

    assert DeepDocSourceAdapter.__module__.startswith("rag.source_understanding")
    assert CanonicalArtifactStore.__module__.startswith("rag.source_understanding")
    assert RetrievalUnitProjector.__module__.startswith("rag.source_understanding")
    assert SourceUnderstandingPipeline.__module__.startswith("rag.source_understanding")
    assert CanonicalDocument.__module__.startswith("rag.source_understanding")


def test_source_understanding_has_no_stale_top_level_imports():
    stale_imports = []
    for path in _PACKAGE_ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "from source_understanding" in text or "import source_understanding" in text:
            stale_imports.append(path.relative_to(_PROJECT_ROOT).as_posix())

    assert stale_imports == []


def test_source_understanding_core_does_not_depend_on_runtime_services():
    forbidden = ("api", "common.settings", "rag.svr", "rag.utils")
    violations = []
    for path in _PACKAGE_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            module = None
            if isinstance(node, ast.ImportFrom):
                module = node.module
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(forbidden):
                        violations.append((path.relative_to(_PROJECT_ROOT).as_posix(), alias.name))
            if module and module.startswith(forbidden):
                violations.append((path.relative_to(_PROJECT_ROOT).as_posix(), module))

    assert violations == []
