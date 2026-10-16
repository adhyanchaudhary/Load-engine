"""Only administrators configure project paths, executable recipes and images."""
import hashlib
from pathlib import Path
from guardrails.diagnostics.io import read_json,ArtifactError
from guardrails.repair.models import RepairPolicy


def fingerprint(model):return hashlib.sha256(model.model_dump_json().encode()).hexdigest()


def load_policy(path):
    if path is None:raise ArtifactError('Set a trusted GUARDRAILS_REPAIR_POLICY_FILE to approve a local project')
    policy=RepairPolicy.model_validate(read_json(Path(path)))
    for project in policy.projects.values():
        for command in (project.recipe.build_command,project.recipe.test_command,project.recipe.application_command):
            if command[0]!=project.recipe.python_executable or '-c' in command:
                raise ArtifactError('MVP supports fixed offline Python recipes without inline shell/code arguments')
    return policy
