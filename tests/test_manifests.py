import os
import json
import unittest

class TestManifests(unittest.TestCase):
    def setUp(self):
        self.repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

    def test_manifest_json(self):
        p = os.path.join(self.repo_root, "manifest.json")
        self.assertTrue(os.path.exists(p), "manifest.json must exist")
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data.get("name"), "adaptive-orchestrator")
        self.assertIn("version", data)
        self.assertIn("subagents", data)
        self.assertIn("templates", data)

        for sub in data["subagents"]:
            sub_p = os.path.join(self.repo_root, sub)
            self.assertTrue(os.path.exists(sub_p), f"Subagent file {sub} must exist")

        for tmpl in data["templates"]:
            tmpl_p = os.path.join(self.repo_root, tmpl)
            self.assertTrue(os.path.exists(tmpl_p), f"Template file {tmpl} must exist")

    def test_plugin_and_skills_json(self):
        p_plug = os.path.join(self.repo_root, "plugin.json")
        p_skill = os.path.join(self.repo_root, "skills.json")
        self.assertTrue(os.path.exists(p_plug))
        self.assertTrue(os.path.exists(p_skill))

        with open(p_plug, "r", encoding="utf-8") as f:
            d_plug = json.load(f)
        with open(p_skill, "r", encoding="utf-8") as f:
            d_skill = json.load(f)

        self.assertEqual(d_plug.get("id"), "adaptive-orchestrator")
        self.assertTrue(len(d_skill.get("skills", [])) > 0)

    def test_subagent_definitions(self):
        p = os.path.join(self.repo_root, "subagents", "subagents-definition.json")
        self.assertTrue(os.path.exists(p))
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        agents = data.get("subagents", [])
        names = {a["name"] for a in agents}
        self.assertEqual(names, {"explorer-researcher", "implementer", "reviewer-verifier", "challenger-auditor"})

    def test_skill_md_frontmatter(self):
        p = os.path.join(self.repo_root, "SKILL.md")
        self.assertTrue(os.path.exists(p))
        with open(p, "r", encoding="utf-8") as f:
            lines = [f.readline() for _ in range(10)]
        header = "".join(lines)
        self.assertTrue(header.startswith("---"))
        self.assertIn("name: adaptive-orchestrator", header)

if __name__ == "__main__":
    unittest.main()
