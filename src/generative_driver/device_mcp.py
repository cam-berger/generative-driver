"""Serve the frozen device catalog using the installed MCP SDK."""
import argparse
import asyncio
import json
from pathlib import Path
import threading


def serve_tools(name, tools, invoke=None):
    from mcp import MCPError
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, ListToolsResult, CallToolResult, TextContent, INVALID_PARAMS
    from .tool_adapter import ToolAdapter
    adapter = ToolAdapter(tools, invoke)
    names = {tool['name'] for tool in adapter.tools}

    async def list_tools(context, params):
        return ListToolsResult(tools=[Tool(**definition) for definition in tools])

    async def call_tool(context, params):
        if params.name not in names:
            raise MCPError(INVALID_PARAMS, 'Unknown tool: '+params.name)
        cancel = threading.Event()
        try:
            result = await asyncio.to_thread(adapter.invoke, params.name,
                {} if params.arguments is None else params.arguments, cancel=cancel)
        except asyncio.CancelledError:
            cancel.set()
            raise
        return CallToolResult(content=[TextContent(type='text', text=json.dumps(result, allow_nan=False))],
                              structured_content=result, is_error=not result.get('ok', True))

    server = Server(name, on_list_tools=list_tools,
                    on_call_tool=call_tool if invoke is not None else None)

    async def run():
        async with stdio_server() as streams:
            await server.run(*streams, server.create_initialization_options())
    asyncio.run(run())


def main():
    parser = argparse.ArgumentParser(description='Generated device MCP catalog')
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--package')
    source.add_argument('--run')
    parser.add_argument('--assignment')
    parser.add_argument('--home')
    parser.add_argument('--session')
    args = parser.parse_args()
    if args.run:
        if not args.assignment:
            parser.error('--run requires --assignment')
        from .client import call
        from .tool_adapter import failure
        identity = {'run_id': args.run, 'assignment_id': args.assignment}
        catalog = call('mcp_tools', identity, args.home, autostart=False)
        if not catalog.get('ok'):
            parser.error(catalog.get('reason', 'Inactive MCP assignment'))

        def invoke_assigned(name, arguments, *, cancel=None, request_id=None):
            from .sdk import _call_with_cancellation
            result = _call_with_cancellation('mcp_tool',
                {**identity, 'name': name, 'arguments': arguments, 'request_id': request_id},
                args.home, cancel=cancel, cancel_method='mcp_cancel',
                cancel_params={**identity, 'request_id': request_id})
            if result.get('schema') == 'driver-result/1':
                return result
            return failure(name, 'managed_execution_failed', result.get('reason', 'Managed execution failed'), 'host')
        serve_tools('generative-driver-device', catalog['tools'], invoke_assigned)
        return
    from interface_runtime.package import verify_package
    from interface_runtime.evidence import read_json
    package = Path(args.package).resolve()
    checked = verify_package(package)
    if not checked['ok']:
        parser.error(str(checked['defects']))
    catalog = read_json(package/'mcp-tools.json')
    from .sdk import ManagedDriver
    driver = ManagedDriver(package, args.session, home=args.home) if args.session else None

    def invoke(name, arguments, *, cancel=None, request_id=None):
        if driver is not None:
            return driver.invoke(catalog['operations'][name], arguments, request_id=request_id, cancel=cancel)
        return {'schema': 'driver-result/1', 'operation': catalog['operations'][name],
                'ok': False, 'outputs': {}, 'warnings': [], 'requested_command_dispatched': False,
                'error': {'code': 'managed_session_required', 'fault': 'operator',
                          'message': 'Configure an owner-managed device session before calling this tool.'}}

    serve_tools('generative-driver-device', catalog['tools'], invoke)


if __name__ == '__main__':
    main()
