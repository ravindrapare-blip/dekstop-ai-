import pathlib
import subprocess
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).with_name("copy-release-notes.sh")


class ReleaseNotesTest(unittest.TestCase):
    def run_copy(self, content):
        with tempfile.TemporaryDirectory() as directory:
            source = pathlib.Path(directory) / "source.md"
            output = pathlib.Path(directory) / "output.md"
            if content is not None:
                source.write_text(content)
            result = subprocess.run(
                ["bash", str(SCRIPT), str(source), str(output)],
                capture_output=True,
                text=True,
            )
            copied = output.read_text() if output.exists() else None
            return result.returncode, copied

    def test_missing(self):
        self.assertNotEqual(self.run_copy(None)[0], 0)

    def test_empty(self):
        self.assertNotEqual(self.run_copy("")[0], 0)

    def test_missing_models(self):
        self.assertNotEqual(self.run_copy("# Release\n\n## Changes\n")[0], 0)

    def test_embedded_heading_is_not_a_section(self):
        self.assertNotEqual(self.run_copy("Text says ## Models here\n")[0], 0)

    def test_models_section_preserves_prose(self):
        content = "# Release\n\n## Models\n\n- Added a model.\n"
        self.assertEqual(self.run_copy(content), (0, content))


if __name__ == "__main__":
    unittest.main()
