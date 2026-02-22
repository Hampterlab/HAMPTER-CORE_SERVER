#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HAMPTER Manager
Refactored into 'mcp_manager' package.
"""
import sys
import os

# Ensure the current directory is in sys.path so we can import the package
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from mcp_manager.main import main

if __name__ == "__main__":
    main()
