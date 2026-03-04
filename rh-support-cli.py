#!/usr/bin/env python3
import sys
import os

# Ensure the current directory is in sys.path so rh_support_lib can be imported
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rh_support_lib.main import main

if __name__ == "__main__":
    main()
