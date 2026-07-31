# Vibe Coding Agent - Plugin System
# Copyright (c) 2026 xingluosama
#
# This package contains the plugin framework infrastructure
# (PluginManager, PluginContext, PluginSecurity).
# Actual plugin files belong in the plugins/ directory.

from plugin_system.context import PluginContext, SimpleLogger
from plugin_system.manager import PluginManager, PluginInfo, HOOK_NAMES
from plugin_system.security import PluginSecurity, SecurityIssue, Severity, PluginImportBlocker, StrictImportBlocker, ResourceLimiter
