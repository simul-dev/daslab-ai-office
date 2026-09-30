"""Profile and project lineage checks; host never executes generated scripts."""
import json
import tempfile
import unittest
from pathlib import Path

from office.network_prototypes import PROFILE
from office.prototypes import DEFAULT_PROFILE, FILES, PrototypeWorkspace


class NetworkPrototypeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.proto = PrototypeWorkspace(self.root, self.root / "data")
        self.addCleanup(self.proto.close)
        self.folder = self.root / "data" / "runs" / "network-one"

    def test_network_scaffold_has_static_profile_contract_not_employee_completion(self):
        context = self.proto.prepare(self.folder, project_id="a" * 32, profile=PROFILE)
        receipt = self.proto.finish(self.folder)
        self.assertTrue(receipt["ready"], receipt)
        self.assertEqual(receipt["profile"], PROFILE)
        self.assertEqual(receipt["project_id"], "a" * 32)
        self.assertEqual(context["profile"], PROFILE)
        self.assertTrue(receipt["seed_only"])
        self.assertEqual(receipt["changed_files"], [])
        self.assertFalse(receipt["model_verified"])
        self.assertFalse(receipt["browser_verified"])
        self.assertFalse(receipt["applied_to_live"])
        self.assertEqual({p.name for p in (self.folder / "workspace").iterdir()}, set(FILES))
        self.assertFalse((self.root / "static").exists())

    def test_only_same_project_and_profile_can_inherit_pinned_workspace(self):
        self.proto.prepare(self.folder, project_id="a" * 32, profile=PROFILE)
        original = self.proto.finish(self.folder)
        for project, profile in (("b" * 32, PROFILE), ("a" * 32, DEFAULT_PROFILE)):
            target = self.root / "data" / "runs" / (project + profile)
            with self.subTest(project=project, profile=profile), self.assertRaisesRegex(ValueError, "different project or profile"):
                self.proto.prepare(target, self.folder, project_id=project, profile=profile)
            self.assertFalse((target / "workspace").exists())
        target = self.root / "data" / "runs" / "network-next"
        context = self.proto.prepare(target, self.folder, project_id="a" * 32, profile=PROFILE)
        self.assertEqual(context["source"], str(self.folder))
        self.assertEqual(self.proto.finish(target)["artifacts"], original["artifacts"])
        self.assertEqual(self.proto.identity(target), {"project_id": "a" * 32, "profile": PROFILE})

    def test_legacy_metadata_only_belongs_to_original_inventory_project(self):
        self.proto.prepare(self.folder)
        path = self.folder / "prototype-baseline.json"
        baseline = json.loads(path.read_text(encoding="utf-8"))
        for field in ("project_id", "profile", "seed"):
            baseline.pop(field)
        path.write_text(json.dumps(baseline), encoding="utf-8")
        self.assertEqual(self.proto.identity(self.folder), {"project_id": "daslab-growth", "profile": DEFAULT_PROFILE})
        self.assertTrue(self.proto.finish(self.folder)["seed_only"])
        target = self.root / "data" / "runs" / "legacy-next"
        self.proto.prepare(target, self.folder)
        self.assertEqual(self.proto.finish(target)["profile"], DEFAULT_PROFILE)
        for project, profile in (("a" * 32, DEFAULT_PROFILE), ("daslab-growth", PROFILE)):
            with self.assertRaises(ValueError):
                self.proto.prepare(self.root / "data" / "other", self.folder, project_id=project, profile=profile)

    def test_identity_and_profile_dom_contract_are_checked(self):
        for kwargs in ({"project_id": "../escape"}, {"project_id": ""}, {"profile": "not-registered"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.proto.prepare(self.folder, **kwargs)
        self.proto.prepare(self.folder, project_id="a" * 32, profile=PROFILE)
        html = self.folder / "workspace/index.html"
        html.write_text(html.read_text(encoding="utf-8").replace('id="network-map"', 'id="missing-map"'), encoding="utf-8")
        self.assertFalse(self.proto.finish(self.folder)["ready"])
        path = self.folder / "prototype-baseline.json"
        baseline = json.loads(path.read_text(encoding="utf-8"))
        baseline["profile"] = "untrusted-profile"
        path.write_text(json.dumps(baseline), encoding="utf-8")
        self.assertFalse(self.proto.finish(self.folder)["ready"])


if __name__ == "__main__":
    unittest.main()
