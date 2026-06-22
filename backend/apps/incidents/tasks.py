import asyncio
import os
import time
from celery import shared_task, group, chord
from langgraph.prebuilt import create_react_agent
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_core.messages import HumanMessage, AIMessage
from django.conf import settings


def extract_message_text(content) -> str:
    """Normalize AIMessage.content to plain text (Gemini may return block lists)."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text = block.get("text")
                if text and (block.get("type") in (None, "text") or "text" in block):
                    parts.append(text)
            elif hasattr(block, "text") and getattr(block, "text", None):
                parts.append(block.text)
        return "\n".join(parts)
    return str(content)


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
def run_mcp_enhanced_triage(aggregated_logs: list, server_id: str):
    """Chord callback: runs LangGraph agent with MCP tools attached."""
    return asyncio.run(_async_agent_execution(aggregated_logs, server_id))


async def _async_agent_execution(aggregated_logs: list, server_id: str):
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY is not set")

    llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", api_key=api_key)
    server_path = os.path.join(settings.BASE_DIR, "mcp_daemons", "hr_server.py")

    mcp_client = MultiServerMCPClient(
        {
            "workstack_hr": {
                "command": "python",
                "args": [server_path, "--transport", "stdio"],
                "transport": "stdio",
            }
        }
    )

    mcp_tools = await mcp_client.get_tools()

    prompt = f"""CRITICAL INCIDENT ALERT for Server {server_id}.
Pre-fetched logs: {aggregated_logs}

INSTRUCTIONS:
1. Find out who made the breaking commit from the logs.
2. Use your tools to look up that employee's manager.
3. Draft an emergency incident report to the manager explaining the issue."""

    # -------------------------------------------------------------------------
    # PRODUCTION DEFAULT: create_react_agent (ReAct loop + add_messages built-in)
    # -------------------------------------------------------------------------
    # LangGraph prebuilt agent already uses MessagesState + add_messages reducer
    # internally — message history APPENDS (Human → AI → Tool → AI), never wipes.
    # Use this for standard LLM + tools loops. See docs/LANGGRAPH_DEEP_DIVE.md §7.
    agent = create_react_agent(llm, mcp_tools)
    final_state = await agent.ainvoke({"messages": [HumanMessage(content=prompt)]})

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

    last_message = final_state["messages"][-1]
    if isinstance(last_message, AIMessage):
        output = extract_message_text(last_message.content)
    else:
        output = extract_message_text(getattr(last_message, "content", last_message))

    print("\n\n--- FINAL AI AGENT OUTPUT ---")
    print(output)
    return output

# --- 3. THE TRIGGER: Launching the Canvas ---

def trigger_incident_workflow():
    server_id = "srv-production-01"
    
    parallel_fetchers = group(
        fetch_datadog_metrics.s(server_id),
        fetch_github_commits.s(server_id),
        fetch_slack_alerts.s(server_id)
    )
    
    # Run fetchers, then pass their outputs to the triage agent
    workflow = chord(parallel_fetchers)(run_mcp_enhanced_triage.s(server_id))
    print(f"Orchestration Canvas launched! Task ID: {workflow.id}")