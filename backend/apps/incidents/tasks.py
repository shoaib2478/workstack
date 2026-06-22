import asyncio
import os
import time
from celery import shared_task, group, chord
from typing import TypedDict, List
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_core.messages import HumanMessage, BaseMessage
from django.conf import settings

# --- 1. THE MUSCLE: Parallel Deterministic Fetchers ---

@shared_task
def fetch_datadog_metrics(server_id):
    time.sleep(1) # Simulating network IO
    return {"source": "Datadog", "cpu_usage": "99%", "status": "critical"}

@shared_task
def fetch_github_commits(server_id):
    time.sleep(1)
    return {"source": "GitHub", "recent_commit": "Update nginx config", "author": "shuaib@workstack.dev"}

@shared_task
def fetch_slack_alerts(server_id):
    time.sleep(1)
    return {"source": "Slack", "messages": ["API latency spiking", "502 Bad Gateway"]}

# --- 2. THE BRAIN: LangGraph + MCP State Machine ---

class AgentState(TypedDict):
    messages: List[BaseMessage]

@shared_task
def run_mcp_enhanced_triage(aggregated_logs: List[dict], server_id: str):
    """Chord Callback: Runs the LangGraph Agent with MCP attached."""
    return asyncio.run(_async_agent_execution(aggregated_logs, server_id))

async def _async_agent_execution(aggregated_logs: List[dict], server_id: str):
    llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", api_key=os.environ["GEMINI_API_KEY"])
    server_path = os.path.join(settings.BASE_DIR, "mcp_daemons", "hr_server.py")
    
    async with MultiServerMCPClient() as mcp_client:
        # Connect LangGraph to our Django FastMCP Server via stdio
        await mcp_client.connect_to_server(
            "workstack_hr",
            command="python",
            args=[server_path]
        )
        
        mcp_tools = mcp_client.get_tools()
        tool_node = ToolNode(mcp_tools)
        llm_with_tools = llm.bind_tools(mcp_tools)
        
        def call_model(state: AgentState):
            return {"messages": [llm_with_tools.invoke(state["messages"])]}
        
        def should_continue(state: AgentState):
            if state["messages"][-1].tool_calls:
                return "execute_tools"
            return END
        
        workflow = StateGraph(AgentState)
        workflow.add_node("agent", call_model)
        workflow.add_node("execute_tools", tool_node)
        
        workflow.set_entry_point("agent")
        workflow.add_conditional_edges("agent", should_continue)
        workflow.add_edge("execute_tools", "agent")
        
        app = workflow.compile()
        
        prompt = f"""
        CRITICAL INCIDENT ALERT for Server {server_id}.
        Pre-fetched logs: {aggregated_logs}
        
        INSTRUCTIONS:
        1. Find out who made the breaking commit from the logs.
        2. Use your tools to look up that employee's manager.
        3. Draft an emergency incident report to the manager explaining the issue.
        """
        
        inputs = {"messages": [HumanMessage(content=prompt)]}
        final_output = await app.ainvoke(inputs)
        
        print("\n\n--- FINAL AI AGENT OUTPUT ---")
        print(final_output["messages"][-1].content)
        return final_output["messages"][-1].content

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