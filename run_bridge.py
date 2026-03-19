#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MQTT <-> MCP Bridge (Port Routing Matrix 포함)
Refactored into 'bridge_mcp' package.
"""
import sys
import os

# Ensure the current directory is in sys.path so we can import the package
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from bridge_mcp.main import main

if __name__ == "__main__":
    main()