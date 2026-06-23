import asyncio
import os
import time
import uuid
from celery import shared_task, group, chord
from langgraph.prebuilt import create_react_agent
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, AIMessage
from django.conf import settings

from apps.incidents.chunking import (
    build_chunked_log_context,
    format_chunking_summary,
    log_chunking_summary,
)
from apps.incidents.events import publish_checkpoint
from apps.incidents.mcp_client import build_mcp_client
from apps.incidents.parser import extract_message_text

# --- 1. THE MUSCLE: Parallel Deterministic Fetchers ---

@shared_task
def fetch_datadog_metrics(server_id):
    time.sleep(1) # Simulating network IO
    return {"source": "Datadog", "cpu_usage": "99%", "status": "critical"}

@shared_task
def fetch_github_commits(server_id):
    time.sleep(1)
    return {"source": "GitHub", "recent_commit": "Update nginx config", "author": "katrina@newhire.com"}

@shared_task
def fetch_slack_alerts(server_id):
    time.sleep(1)
    return {"source": "Slack", "messages": ["API latency spiking", "502 Bad Gateway"]}


# --- 2. THE BRAIN: LangGraph ReAct agent + MCP tools ---

@shared_task
def run_mcp_enhanced_triage(aggregated_logs: list, server_id: str, run_id: str | None = None):
    """Chord callback: runs LangGraph agent with MCP tools attached."""
    run_id = run_id or str(uuid.uuid4())
    return asyncio.run(_async_agent_execution(aggregated_logs, server_id, run_id))


async def _async_agent_execution(aggregated_logs: list, server_id: str, run_id: str):
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY is not set")

    publish_checkpoint(
        run_id,
        "triage.start",
        f"Starting triage for {server_id}",
        server_id=server_id,
        mcp_transport=getattr(settings, "MCP_TRANSPORT", "sse"),
    )

    publish_checkpoint(run_id, "fetch.complete", "Context gathered from parallel fetchers")
    chunked_logs, chunk_payloads = build_chunked_log_context(aggregated_logs, run_id)
    log_chunking_summary(chunk_payloads)  # TESTING — comment out after validation
    publish_checkpoint(
        run_id,
        "chunk.complete",
        "Telemetry payloads prepared for LLM context limits",
        sources=format_chunking_summary(chunk_payloads),
    )

    llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", api_key=api_key)
    mcp_client = build_mcp_client()

    publish_checkpoint(run_id, "mcp.connect", "Connecting to HR MCP server")
    mcp_tools = await mcp_client.get_tools()
    publish_checkpoint(
        run_id,
        "mcp.tools",
        f"MCP tools ready ({len(mcp_tools)} available)",
        tool_count=len(mcp_tools),
    )

    prompt = f"""CRITICAL INCIDENT ALERT for Server {server_id}.
Pre-fetched logs (chunked for context limits):
{chunked_logs}

INSTRUCTIONS:
1. Find out who made the breaking commit from the logs.
2. Use your tools to look up that employee's manager.
3. Draft an emergency incident report to the manager explaining the issue.
4. If you see [TRIAGE_REF ...] markers, treat them as pointers to additional stored log data — summarize what you have inline first."""

    # -------------------------------------------------------------------------
    # ALTERNATIVE: Manual StateGraph + add_messages (commented — use when you need
    # custom nodes: human-in-the-loop, severity routing, extra state fields)
    # -------------------------------------------------------------------------
    # WHY add_messages: plain `messages: list` REPLACES the list each node update,
    # wiping prompt/history → Gemini error "contents are required".
    # WHEN to use manual graph instead of create_react_agent:
    #   - Approval gate before sending Slack/email
    #   - Branch on incident severity (page on-call vs auto-revert)
    #   - Separate classify / summarize / act nodes with different prompts
    #
    # from typing import Annotated, TypedDict
    # from langgraph.graph import StateGraph, END
    # from langgraph.graph.message import add_messages
    # from langgraph.prebuilt import ToolNode
    #
    # class AgentState(TypedDict):
    #     messages: Annotated[list, add_messages]  # APPEND, do not overwrite
    #
    # tool_node = ToolNode(mcp_tools)
    # llm_with_tools = llm.bind_tools(mcp_tools)
    #
    # async def call_model(state: AgentState):
    #     response = await llm_with_tools.ainvoke(state["messages"])
    #     return {"messages": [response]}
    #
    # def should_continue(state: AgentState):
    #     last = state["messages"][-1]
    #     if isinstance(last, AIMessage) and last.tool_calls:
    #         return "execute_tools"
    #     return END
    #
    # workflow = StateGraph(AgentState)
    # workflow.add_node("agent", call_model)
    # workflow.add_node("execute_tools", tool_node)
    # workflow.set_entry_point("agent")
    # workflow.add_conditional_edges("agent", should_continue)
    # workflow.add_edge("execute_tools", "agent")
    # app = workflow.compile()
    # final_state = await app.ainvoke({"messages": [HumanMessage(content=prompt)]})

    agent = create_react_agent(llm, mcp_tools)
    publish_checkpoint(run_id, "agent.invoke", "LangGraph ReAct agent running")
    final_state = await agent.ainvoke({"messages": [HumanMessage(content=prompt)]})
    publish_checkpoint(run_id, "agent.complete", "Agent finished reasoning loop")

    last_message = final_state["messages"][-1]
    if isinstance(last_message, AIMessage):
        output = extract_message_text(last_message.content)
    else:
        output = extract_message_text(getattr(last_message, "content", last_message))

    publish_checkpoint(
        run_id,
        "triage.complete",
        "Incident report ready",
        report_preview=output[:500],
    )

    print("\n\n--- FINAL AI AGENT OUTPUT ---")
    print(output)
    return {"run_id": run_id, "report": output}

# --- 3. THE TRIGGER: Launching the Canvas ---

def trigger_incident_workflow():
    server_id = "srv-production-01"
    run_id = str(uuid.uuid4())

    parallel_fetchers = group(
        fetch_datadog_metrics.s(server_id),
        fetch_github_commits.s(server_id),
        fetch_slack_alerts.s(server_id)
    )

    workflow = chord(parallel_fetchers)(run_mcp_enhanced_triage.s(server_id, run_id))
    print(f"Orchestration Canvas launched! Task ID: {workflow.id} Run ID: {run_id}")
    print(f"Live stream: /api/v1/incidents/runs/{run_id}/stream/")
    return {"task_id": workflow.id, "run_id": run_id}
