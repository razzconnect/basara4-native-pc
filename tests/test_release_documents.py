"""The documents a player reads (README EN and ID, the bug-report guide, the audio guide, the two package READMEs) match
the launcher: no name of a button or option that is gone, no advice to send raw files from user\\ (they are not masked
and a crash dump holds the game's memory), the report flow of today (Save report; Tracer, F11 and Report; send only the
zip), and the English and Indonesian texts say the same things. Plain text checks, nothing is started."""
import re
import unittest

from paths import ROOT

ENGLISH = ("README.md", "packaging/basara4/README.txt")
INDONESIAN = ("README.id.md", "packaging/basara4/README.id.txt", "docs/BUG-SCAN-GUIDE.md", "docs/AUDIO-VERIFICATION.md")
ALL = ENGLISH + INDONESIAN

# What the launcher used to show and does not any more. "Map sticks only" is still a button, in the Controller window.
GONE = ("Copy Error", "Render debug trace", "Audio debug log", "Map controller", "Reset mapping", "Controller Utama")
RAW_FILES = re.compile(r"last_run\.log|prepare\.log|audio_debug\.log|render_traces|captures|\.dmp|core-\*", re.IGNORECASE)
SEND = re.compile(r"\b(send|attach|upload|post|kirim|kirimkan|lampirkan|sertakan|tempel|dikirim|terlampir)\b", re.IGNORECASE)
NOT = re.compile(r"\b(do not|don't|never|not|only the zip|jangan|tidak|bukan|hanya zip|hanya (?:\*\*)?zip)\b", re.IGNORECASE)


def text(name):
    return (ROOT / name).read_text(encoding="utf-8")


def paragraphs(name):
    """Paragraphs with their wrapped lines joined; every row of a table is a unit of its own."""
    units, current = [], []

    def flush():
        if current:
            units.append(" ".join(" ".join(current).split()))
            current.clear()

    for line in text(name).replace("\r\n", "\n").split("\n"):
        if not line.strip():
            flush()
        elif line.lstrip().startswith("|"):
            flush()
            units.append(" ".join(line.split()))
        else:
            current.append(line)
    flush()
    return units


class OldNamesTests(unittest.TestCase):
    def test_no_document_names_a_button_or_option_that_is_gone(self):
        for name in ALL:
            body = text(name)
            for old in GONE:
                self.assertNotIn(old, body, f"{name} still says {old!r}")

    def test_the_new_names_are_there(self):
        for name in ("README.md", "README.id.md", "packaging/basara4/README.txt", "packaging/basara4/README.id.txt"):
            body = text(name)
            for word in ("Tracer", "Report", "Controller Mapping", "Manage DLC"):
                self.assertIn(word, body, f"{name} does not say {word!r}")


class RawFilesTests(unittest.TestCase):
    def test_no_document_tells_the_player_to_send_raw_files_from_user(self):
        for name in ALL:
            for paragraph in paragraphs(name):
                for sentence in re.split(r"(?<=[.!?:;])\s+", paragraph):
                    if SEND.search(sentence) and RAW_FILES.search(sentence):
                        self.assertRegex(sentence, NOT, f"{name}: {sentence[:160]!r}")

    def test_the_flow_says_to_send_only_the_zip_and_why(self):
        for name in ("README.md", "README.id.md", "packaging/basara4/README.txt", "packaging/basara4/README.id.txt",
                     "docs/BUG-SCAN-GUIDE.md"):
            body = " ".join(text(name).split())  # the plain text files wrap their lines
            self.assertRegex(body, r"(?i)\bzip\b", name)
            self.assertRegex(body, r"(?i)only (?:\*\*)?(?:the|that) zip|hanya (?:\*\*)?zip", name)
            self.assertRegex(body, r"(?i)(not masked|not hidden|tidak disamarkan|tidak ikut disamarkan)", name)

    def test_the_two_reports_are_named(self):
        for name in ("README.md", "README.id.md", "packaging/basara4/README.txt", "packaging/basara4/README.id.txt",
                     "docs/BUG-SCAN-GUIDE.md"):
            body = text(name)
            self.assertIn("Save report", body, name)
            self.assertIn("F11", body, name)

    def test_no_document_asks_for_a_crash_dump(self):
        for name in ALL:
            for paragraph in paragraphs(name):
                for sentence in re.split(r"(?<=[.!?:;])\s+", paragraph):
                    if re.search(r"core-\*|\.dmp|dump crash|crash dump", sentence, re.IGNORECASE) and SEND.search(sentence):
                        self.assertRegex(sentence, NOT, f"{name}: {sentence[:160]!r}")


class SameInBothLanguagesTests(unittest.TestCase):
    def test_the_readmes_have_the_same_sections(self):
        count = lambda name: len(re.findall(r"^## ", text(name), re.MULTILINE))
        self.assertEqual(count("README.md"), count("README.id.md"))

    def test_the_package_readmes_cover_the_same_topics(self):
        topics = ("Save report", "Tracer", "F11", "Controller Mapping", "Manage DLC", "audio_wav", "9 MB", "20")
        for name in ("packaging/basara4/README.txt", "packaging/basara4/README.id.txt"):
            body = text(name)
            for topic in topics:
                self.assertIn(topic, body, f"{name} does not cover {topic!r}")

    def test_both_readmes_say_the_cleanup_keeps_twenty_sessions_and_leaves_audio_wav_alone(self):
        for name in ("README.md", "README.id.md", "packaging/basara4/README.txt", "packaging/basara4/README.id.txt"):
            body = " ".join(text(name).split())  # the plain text files wrap their lines
            self.assertRegex(body, r"(?i)20 (sessions|sesi)", name)
            self.assertIn("audio_wav", body, name)

    def test_the_texts_read_by_players_are_plain_ascii_where_the_package_expects_it(self):
        for name in ("packaging/basara4/README.txt", "packaging/basara4/README.id.txt"):
            raw = (ROOT / name).read_bytes()
            raw.decode("ascii")
            self.assertEqual(raw.count(b"\n"), raw.count(b"\r\n"), f"{name} must keep CRLF line ends")


if __name__ == "__main__":
    unittest.main()
