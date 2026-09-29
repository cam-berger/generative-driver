"""A worker's small MCP facade delegates every operation to the run owner."""
import argparse
import asyncio
import json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--home', required=True)
    parser.add_argument('--run', required=True)
    parser.add_argument('--assignment', required=True)
    args = parser.parse_args()
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent, ListToolsResult, CallToolResult
    from .client import call
    identity = {'run_id':args.run, 'assignment_id':args.assignment}

    async def tools(context, params):
        result = await asyncio.to_thread(call, 'tools', identity, args.home, autostart=False)
        if not result.get('ok'):
            raise ValueError(result.get('reason', 'Worker assignment unavailable'))
        return ListToolsResult(tools=[Tool(**item) for item in result['tools']])

    async def invoke(context, params):
        result = await asyncio.to_thread(call, 'tool', {**identity,'name':params.name,'arguments':params.arguments or {}}, args.home, autostart=False)
        return CallToolResult(content=[TextContent(type='text',text=json.dumps(result))])

    server = Server('generative-driver-stage', on_list_tools=tools, on_call_tool=invoke)

    async def run():
        async with stdio_server() as streams:
            await server.run(*streams, server.create_initialization_options())
    asyncio.run(run())


if __name__ == '__main__':
    main()
