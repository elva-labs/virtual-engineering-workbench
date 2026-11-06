"""Exercise routine deployments with fake AWS/CDK tools and real configuration patching."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class UpdateDeploymentTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in (
            "deploy.sh", "scripts/deploy-regions.sh", "scripts/diff-summary.py",
            "backend/infra/config.py", "backend/infra/constants.py", "backend/cdk-backend.json",
            "frontend/infrastructure/cdk.json",
        ):
            destination = self.root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(ROOT / name, destination)
        activate = self.root / "backend/.venv/bin/activate"
        activate.parent.mkdir(parents=True)
        activate.write_text("deactivate() { :; }\n")
        web = self.root / "frontend/web"
        web.mkdir(parents=True)
        (web / "configure_auth.sh").write_text("mkdir -p dist\n")
        (web / "package.json").write_text('{"packageManager":"yarn@4.14.0"}')
        (self.root / "config.env").write_text(
            'AWS_ACCOUNT_ID="123456789012"\nAWS_REGION="us-east-1"\nENVIRONMENT="dev"\n'
            'ORG_PREFIX="test"\nAPP_PREFIX="wb"\nADMIN_EMAIL="admin@test.invalid"\n'
            'ADMIN_USER_ID="ADMIN"\nOIDC_CLIENT_ID="existing-client"\n'
            'OIDC_ISSUER_URL="https://login.test.invalid"\nSPOKE_ACCOUNT_ID="222222222222"\n'
        )
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        tool = bin_dir / "fake"
        tool.write_text(f"#!{sys.executable}\n" + r'''
import json, os, pathlib, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ['CALLS'], 'a') as log:
    log.write(json.dumps([name, *args]) + '\n')
if name == 'aws':
    op = ' '.join(args[:2])
    if args == ['--version']: print('aws-cli/2.20.0 Python/3.13.0')
    elif op == 'sts get-caller-identity': print('123456789012')
    elif op == 'organizations describe-organization': print('o-test123456')
    elif op == 'ssm get-parameter':
        if os.getenv('MISSING') == 'bootstrap': sys.exit(1)
        print('30')
    elif op == 'cloudformation describe-stacks':
        if os.getenv('MISSING') == 'frontend': sys.exit(1)
        print(json.dumps([
            {'OutputKey': 'cdnfqdnoutput', 'OutputValue': 'example.cloudfront.net'},
            {'OutputKey': 'cdncustomfqdnoutput', 'OutputValue': 'not available'},
            {'OutputKey': 'icfrontends3output', 'OutputValue': 'test-web'},
            {'OutputKey': 'cdndistributionidoutput', 'OutputValue': 'E123'},
        ]))
    elif op == 'ec2 describe-vpcs': print('None' if os.getenv('MISSING') == 'vpc' else 'vpc-123')
    elif op == 'secretsmanager describe-secret': print('{}')
    elif op == 'cloudformation list-stacks': print('test-wb-projects-dev')
    elif op in ('s3 sync', 'cloudfront create-invalidation'): print('{}')
    else:
        print('Unexpected AWS call: ' + op, file=sys.stderr)
        sys.exit(1)
elif name == 'cdk':
    if args == ['--version']: print('2.1133.0')
    elif args[0] == 'diff':
        print('Stack Test\nThere were no differences\n✨  Number of stacks with differences: 0')
    elif args[0] != 'deploy': sys.exit(1)
elif name == 'node': print('v24.0.0' if args == ['--version'] else 'yarn@4.14.0')
elif name == 'python':
    if args == ['--version']: print('Python 3.13.0')
    else: os.execv(sys.executable, [sys.executable, *args])
elif name == 'yarn': print('4.14.0')
elif name == 'uv': print('uv 0.11.3')
elif name == 'docker': print('Docker version 27.0.0')
''')
        tool.chmod(0o755)
        for name in ("aws", "cdk", "node", "python", "yarn", "uv", "docker", "corepack"):
            (bin_dir / name).symlink_to(tool)
        self.env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}",
                        CALLS=str(self.root / "calls.jsonl"), AWS_ACCESS_KEY_ID="test", AWS_SECRET_ACCESS_KEY="test")

    def run_deploy(self, *flags, missing=""):
        result = subprocess.run(
            ["bash", "deploy.sh", "--config", "config.env", "--yes", "--update-only", *flags],
            cwd=self.root, env=dict(self.env, MISSING=missing), text=True, capture_output=True,
        )
        calls = [json.loads(line) for line in (self.root / "calls.jsonl").read_text().splitlines()]
        return result, calls

    def test_update_deploys_but_preserves_identity_seed_and_spoke(self):
        result, calls = self.run_deploy()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(sum(call[:2] == ["cdk", "deploy"] for call in calls), 2)
        aws_operations = {tuple(call[1:3]) for call in calls if call[0] == "aws"}
        self.assertIn(("s3", "sync"), aws_operations)
        self.assertIn(("cloudfront", "create-invalidation"), aws_operations)
        self.assertFalse(any(call[:2] == ["cdk", "bootstrap"] for call in calls))
        for forbidden in (("dynamodb", "put-item"), ("cloudformation", "deploy"),
                          ("ssm", "put-parameter"), ("secretsmanager", "put-secret-value"),
                          ("iam", "create-service-linked-role")):
            self.assertNotIn(forbidden, aws_operations)

    def test_update_preview_does_not_deploy_or_write_prerequisites(self):
        result, calls = self.run_deploy("--diff")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(sum(call[:2] == ["cdk", "diff"] for call in calls), 2)
        self.assertFalse(any(call[0] == "cdk" and call[1] in {"deploy", "bootstrap"} for call in calls))
        self.assertFalse(any(call[0] == "aws" and call[1] in {"s3", "cloudfront", "dynamodb", "iam"} for call in calls))

    def test_missing_existing_resources_stop_before_cdk(self):
        for missing in ("bootstrap", "frontend", "vpc"):
            with self.subTest(missing=missing):
                (self.root / "calls.jsonl").write_text("")
                result, calls = self.run_deploy(missing=missing)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(any(call[0] == "cdk" and call[1] in {"diff", "deploy", "bootstrap"} for call in calls))

    def test_update_cannot_destroy(self):
        result = subprocess.run(["bash", "deploy.sh", "--update-only", "--destroy"],
                                cwd=self.root, env=self.env, text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot be combined", result.stdout)


if __name__ == "__main__":
    unittest.main()
