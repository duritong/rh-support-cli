"""Run all or selected tests with buffering and offline isolation."""

import unittest

from tests.support import SandboxSuite

if __name__ == "__main__":
    loader = unittest.TestLoader()
    loader.suiteClass = SandboxSuite
    unittest.main(module=None, defaultTest="tests", testLoader=loader, buffer=True)
