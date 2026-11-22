"""The index cache's code stamp (src/ui/transcript_index.py code_stamp, measuring_files): the files a run's row and
measures come from, read from the source's imports, so new code there measures every run again and a change to the
viewer's server or pages measures nothing."""
from __future__ import annotations

import json
import subprocess
import sys

from src.ui import transcript_index as ti


def write(root, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def test_the_stamp_covers_what_measuring_imports_and_nothing_of_the_pages():
    names = {p.relative_to(ti.ROOT).as_posix() for p in ti.measuring_files()}
    for inside in ("src/ui/transcript_index.py", "src/ui/transcript_levels.py", "src/measures/cheating_ladder.py",
                   "src/measures/eval_log_events.py", "src/domains/swe/swe_ladder.py", "src/judging/verdict_files.py",
                   "src/configuration/seed_packs.py"):
        assert inside in names, inside
    for outside in ("src/ui/transcript_viewer.py", "src/ui/transcript_summary.py", "src/ui/transcript_board.py"):
        assert outside not in names, outside


def test_every_module_python_loads_for_the_index_is_in_the_stamp():
    """A fresh interpreter that imports the index loads no module of src/ outside measuring_files."""
    code = ("import sys, json; import src.ui.transcript_index as ti; "
            "print(json.dumps(sorted(m.__file__ for n, m in sys.modules.items() "
            "if (n == 'src' or n.startswith('src.')) and getattr(m, '__file__', None))))")
    out = subprocess.run([sys.executable, "-c", code], cwd=ti.ROOT, capture_output=True, text=True, check=True).stdout
    loaded = {ti.Path(f).resolve() for f in json.loads(out)}
    assert loaded and loaded <= set(ti.measuring_files())


def test_the_stamp_follows_imports_through_packages_functions_and_relative_names(tmp_path, monkeypatch):
    write(tmp_path, {
        "src/__init__.py": "",
        "src/index.py": "from src.pkg import ladder\n\ndef late():\n    import src.late\n",
        "src/pkg/__init__.py": "",
        "src/pkg/ladder.py": "from . import helper\nfrom .deep.leaf import VALUE\nimport json\n",
        "src/pkg/helper.py": "",
        "src/pkg/deep/__init__.py": "",
        "src/pkg/deep/leaf.py": "VALUE = 1\n",
        "src/late.py": "",
        "src/page.py": "import src.index\n",
    })
    monkeypatch.setattr(ti, "ROOT", tmp_path)
    monkeypatch.setattr(ti, "MEASURING", "src.index")
    names = [p.relative_to(tmp_path).as_posix() for p in ti.measuring_files()]
    assert names == ["src/__init__.py", "src/index.py", "src/late.py", "src/pkg/__init__.py",
                     "src/pkg/deep/__init__.py", "src/pkg/deep/leaf.py", "src/pkg/helper.py", "src/pkg/ladder.py"]
    before = ti.code_stamp()
    (tmp_path / "src/page.py").write_text("import src.index\nPAGE = 2\n")
    assert ti.code_stamp() == before
    (tmp_path / "src/pkg/deep/leaf.py").write_text("VALUE = 2\n")
    assert ti.code_stamp() != before
