from pathlib import Path
import tomllib


def test_pyproject_toml_and_requirements_txt_synchronized():
    base_dir = Path(__file__).resolve().parent.parent.parent
    pyproject_path = base_dir / "pyproject.toml"
    requirements_path = base_dir / "requirements.txt"

    assert pyproject_path.exists(), "pyproject.toml does not exist"
    assert requirements_path.exists(), "requirements.txt does not exist"

    with open(pyproject_path, "rb") as f:
        pyproject_data = tomllib.load(f)

    project = pyproject_data.get("project", {})
    assert project.get("requires-python") == "==3.14.8", (
        f"Expected requires-python == '==3.14.8', got '{project.get('requires-python')}'"
    )

    pyproject_deps = set(project.get("dependencies", []))
    dev_deps = set(
        project.get("optional-dependencies", {}).get("dev", [])
    )
    all_expected_deps = pyproject_deps | dev_deps

    with open(requirements_path, "r", encoding="utf-8") as f:
        requirements_lines = [
            line.strip()
            for line in f
            if line.strip() and not line.strip().startswith("#")
        ]

    req_deps = set(requirements_lines)

    assert all_expected_deps == req_deps, (
        f"Mismatch between pyproject.toml and requirements.txt.\n"
        f"Missing in requirements.txt: {all_expected_deps - req_deps}\n"
        f"Extra in requirements.txt: {req_deps - all_expected_deps}"
    )
