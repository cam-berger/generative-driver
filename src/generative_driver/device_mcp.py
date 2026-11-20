"""Serve tools over MCP on standard input and output with the installed MCP SDK
(docs/features/package-format/03-package.md, Behaviour 03-B13).

`serve_tools` is the server of every tool the kit serves. Run as a module, this serves the generated tools of one
reuse assignment, which it reaches through the configurator's gateway; `generative-driver serve` is the one way to
serve a package to an agent."""
import argparse
import asyncio
import json
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
    parser = argparse.ArgumentParser(description='The generated tools of one reuse assignment, served over MCP')
    # Neither is required of the parser, so that an option it does not know is refused as one.
    parser.add_argument('--run')
    parser.add_argument('--assignment')
    parser.add_argument('--home')
    args = parser.parse_args()
    if not args.run or not args.assignment:
        parser.error('--run and --assignment are required')
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
    serve_tools(catalog['server'], catalog['tools'], invoke_assigned)


if __name__ == '__main__':
    main()
