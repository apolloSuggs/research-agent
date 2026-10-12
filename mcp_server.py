"""mcp_server.py: the two data tools behind one MCP server (exam tasks 2.1, 2.2, 2.4).

MCP (Model Context Protocol) is a standard way to offer tools to an agent from a separate program.
The Agent SDK starts this file as a child process and talks to it over stdio (its standard input and
output). That channel carries the protocol messages, so nothing else may print to stdout.

The rules live HERE, in code, so they hold for any client (our coordinator, Claude Code, the Inspector):
  - the account tool only serves the customer verified in the session (session_store),
  - transient errors are retried, everything else is reported at once (errors.py),
  - every result carries grounding metadata, every failure is a structured error (tools.py).

Environment (set by the client):
  SESSION_ID        which stored session to read. Without it, nothing is verified, so account reads are denied.
  FAULT_INJECT      testing only, e.g. "account:timeout" or "account:blip,policy:timeout".
  RETRY_BASE_DELAY  seconds before the first retry (default 0.2; doubles each time).

Run it by hand:  .venv/bin/python mcp_server.py
"""

import json
import os

import anyio
import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

import session_store
from tools import TOOLS, parse_faults, run_tool

SESSION_ID = os.environ.get("SESSION_ID")
FAULTS = parse_faults(os.environ.get("FAULT_INJECT"))
RETRY = {"base_delay": float(os.environ.get("RETRY_BASE_DELAY", "0.2"))}


async def list_tools(context, params):
    return types.ListToolsResult(tools=[
        types.Tool(name=t["name"], description=t["description"], input_schema=t["input_schema"]) for t in TOOLS
    ])


async def handle_call(context, params):
    # Load the session fresh on every call: another process may have verified the customer since.
    session = session_store.load(SESSION_ID) if SESSION_ID else None
    result, is_error = run_tool(params.name, params.arguments or {}, session, FAULTS, RETRY)
    return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(result))], is_error=is_error)


server = Server("research-agent-data", on_list_tools=list_tools, on_call_tool=handle_call)


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    anyio.run(main)
