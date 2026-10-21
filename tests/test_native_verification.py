"""Native digest and snapshot regressions without terminal output."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class NativeVerificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = tempfile.TemporaryDirectory()
        cls.root = Path(cls.work.name)
        source = cls.root / 'probe.c'
        source.write_text(r'''
#include <stdlib.h>
#include <unistd.h>
#include <fcntl.h>
#include <string.h>
#include <stdio.h>
static void *bounded_malloc(size_t size) {
    if (getenv("BOUND_ALLOC") && size > 65536) return NULL;
    return malloc(size);
}
static ssize_t changing_read(int fd, void *buf, size_t count) {
    ssize_t n = read(fd, buf, count);
    const char *target = getenv("CHANGE_PATH");
    if (target && n > 0) {
        unsetenv("CHANGE_PATH");
        if (getenv("REPLACE_PATH")) {
            char name[4096]; snprintf(name, sizeof name, "%s.new", target);
            int out = open(name, O_WRONLY|O_CREAT|O_TRUNC, 0600);
            if (out >= 0) { write(out, "replaced", 8); close(out); rename(name, target); }
        } else {
            int out = open(target, O_WRONLY);
            if (out >= 0) { pwrite(out, "changed!", 8, 0); close(out); }
        }
    }
    return n;
}
#define malloc bounded_malloc
#define read changing_read
#define main retrofetch_entry
#include "''' + str(ROOT / 'native/retrofetch.c') + r'''"
#undef main
#undef malloc
#undef read
int main(int argc, char **argv) {
    if (getenv("REUSE_SNAPSHOT")) {
        struct verified_file *first=verified_dependency(argv[1],0);
        struct verified_file *second=verified_dependency(argv[1],0);
        int ok=first && first==second && verification_valid();
        verification_clear(); return ok?0:3;
    }
    if (getenv("STALE_SNAPSHOT")) {
        if (!verified_dependency(argv[1],0)) return 3;
        int out=open(argv[1],O_WRONLY); pwrite(out,"changed!",8,0); close(out);
        int stale=!verification_valid(); verification_clear(); return stale?0:3;
    }
    unsigned char digest[32];
    if (argc != 2 || !file_digest(argv[1], digest)) return 2;
    for (int i=0; i<32; i++) printf("%02x", digest[i]);
    puts(""); return 0;
}
''')
        cls.probe = cls.root / 'probe'
        subprocess.run(['cc', '-D_GNU_SOURCE', '-std=c11', '-O2', str(source),
                        '-lcrypto', '-lm', '-o', str(cls.probe)], check=True,
                       capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / 'input'
        self.content = b'abcdefgh' * (256 * 1024)
        self.path.write_bytes(self.content)

    def run_probe(self, **environment):
        return subprocess.run([str(self.probe), str(self.path)], capture_output=True,
                              text=True, env=dict(os.environ, **environment))

    def test_hashing_uses_bounded_allocation(self):
        result = self.run_probe(BOUND_ALLOC='1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), hashlib.sha256(self.content).hexdigest())

    def test_in_place_mutation_is_rejected(self):
        self.assertEqual(self.run_probe(CHANGE_PATH=str(self.path)).returncode, 2)

    def test_path_replacement_is_rejected(self):
        self.assertEqual(self.run_probe(CHANGE_PATH=str(self.path), REPLACE_PATH='1').returncode, 2)

    def test_regular_dependency_symlink_remains_supported(self):
        target = self.path.with_name('target')
        self.path.rename(target)
        self.path.symlink_to(target)
        result = self.run_probe()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), hashlib.sha256(self.content).hexdigest())

    def test_nonregular_dependency_is_rejected(self):
        self.path.unlink()
        os.mkfifo(self.path)
        self.assertEqual(self.run_probe().returncode, 2)

    def test_snapshot_is_reused_within_invocation(self):
        self.assertEqual(self.run_probe(REUSE_SNAPSHOT='1').returncode, 0)

    def test_reused_snapshot_is_rechecked_before_rendering(self):
        self.assertEqual(self.run_probe(STALE_SNAPSHOT='1').returncode, 0)
