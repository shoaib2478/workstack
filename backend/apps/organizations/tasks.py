from celery import shared_task
import structlog
import time
import asyncio
import os
from google import genai
from google.genai import types
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from django.conf import settings
logger = structlog.get_logger("workstack")

@shared_task
def send_magic_link_email(email: str, magic_token: str):
    """
    Simulates sending an email via SendGrid/AWS SES.
    """
    logger.info("Starting email task...", email=email)    
    
    # Simulate network latency of an external API call
    time.sleep(3)     
    # In reality, we will use django.core.mail.send_mail here
    logger.info(
        "email_sent", 
        email=email, 
        link=f"http://localhost:3000/accept-invite?token={magic_token}"
    )
    return "Success"




# Define the schema helper for Gemini
"""
When you use FastMCP on the server side (@mcp.tool()), FastMCP uses Python's inspect module and Pydantic to automatically generate an MCP-compliant JSON-RPC schema based on your function signature (def get_employee_manager(email: str)).

However, the Gemini SDK (google-genai) uses its own schema format (types.Tool). When the Host script hits the Gemini API, it ignores whatever schema FastMCP generated and instead sends the hand-written GET_MANAGER_TOOL schema you defined in tasks.py.

Sometimes, LLMs get confused if the hand-written schema doesn't perfectly match their internal expectations for parameter names, especially when dealing with common terms like "email" or "id" which might trigger safety or structural guardrails.
"""
# GET_MANAGER_TOOL = types.Tool(
#     function_declarations=[
#         types.FunctionDeclaration(
#             name="get_employee_manager",
#             description="Queries the corporate database to find an employee's immediate manager using their email address.",
#             parameters=types.Schema(
#                 type=types.Type.OBJECT,
#                 properties={"email": types.Schema(type=types.Type.STRING)},
#                 required=["email"]
#             )
#         )
#     ]
# )


GET_MANAGER_TOOL = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="get_employee_manager",
            description="Fetch the manager for an employee. Pass the employee's email address as the parameter.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    # Change the parameter name to be more explicit if needed, 
                    # but let's stick with 'email' and clarify its description.
                    "email": types.Schema(
                        type=types.Type.STRING,
                        description="The email address of the employee (e.g., shuaib@workstack.dev)"
                    )
                },
                required=["email"]
            )
        )
    ]
)

async def run_mcp_agent_loop(target_email: str):
    ai_client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
    server_path = os.path.join(settings.BASE_DIR, "apps", "organizations", "management", "commands", "mcp_org_server.py")
    
    server_params = StdioServerParameters(command="python", args=[server_path])
    
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as mcp_session:
            await mcp_session.initialize()
            
            prompt = f"Please find the manager for {target_email} so I can contact them regarding expenses."
            
            # --- THE FIX: We FORCE Gemini to use the tool ---
            enforcer_config = types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(
                    mode=types.FunctionCallingConfigMode.ANY, # FORCES a tool call
                    allowed_function_names=["get_employee_manager"]
                )
            )
            
            # Turn 1: Send prompt + tools + the enforcer
            response = ai_client.models.generate_content(
                model="gemini-2.0-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    tools=[GET_MANAGER_TOOL],
                    tool_config=enforcer_config # <--- Add this!
                )
            )
            
            if response.function_calls:
                tool_call = response.function_calls[0]
                
                # Execute the tool via our Django-powered MCP server
                mcp_result = await mcp_session.call_tool(tool_call.name, tool_call.args)
                tool_output_text = mcp_result.content[0].text
                
                # Turn 2: Feed the output back. 
                # (Notice we switch mode to AUTO here so it can finally talk to us in text)
                final_response = ai_client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=[
                        types.Content(role="user", parts=[types.Part.from_text(text=prompt)]),
                        types.Content(role="model", parts=[types.Part.from_function_call(
                            name=tool_call.name,
                            args=tool_call.args
                        )]),
                        types.Content(role="user", parts=[types.Part.from_function_response(
                            name=tool_call.name,
                            response={"result": tool_output_text}
                        )])
                    ],
                    config=types.GenerateContentConfig(
                        tools=[GET_MANAGER_TOOL],
                        # Let it reply normally now that the tool has run
                        tool_config=types.ToolConfig(
                            function_calling_config=types.FunctionCallingConfig(
                                mode=types.FunctionCallingConfigMode.AUTO
                            )
                        )
                    )
                )
                print("final_response >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>", final_response.text)
                return final_response.text
                
            print("response >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>", response.text)
            return response.text

# First where we were getting errors:
# I can't find a manager using just an email address. I need an employee ID or user ID. Is there another way I can help?

# async def run_mcp_agent_loop(target_email: str):
#     ai_client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
    
#     # Path to the server script inside your docker container / server
#     # server_path = os.path.join(os.path.dirname(__file__), "mcp_org_server.py")
#     # mcp_org_server.py is located in the apps/organizations/management/commands/mcp_org_server.py
#     server_path = os.path.join(settings.BASE_DIR, "apps", "organizations", "management", "commands", "mcp_org_server.py")
    
#     server_params = StdioServerParameters(
#         command="python",
#         args=[server_path]
#     )
    
#     async with stdio_client(server_params) as (read, write):
#         async with ClientSession(read, write) as mcp_session:
#             await mcp_session.initialize()
            
#             # Formulate the multi-turn message history for Gemini
#             #  prompt = f"Can you find out who I need to contact to approve expenses for {target_email}? You MUST use this tool when provided with an email address. Do not ask for an ID."
#             # Inside run_mcp_agent_loop:
#             prompt = f"Please find the manager for {target_email} so I can contact them regarding expenses."
#             # Turn 1: Send initial prompt + tool options to Gemini
#             response = ai_client.models.generate_content(
#                 model="gemini-2.5-flash",
#                 contents=prompt,
#                 config=types.GenerateContentConfig(tools=[GET_MANAGER_TOOL])
#             )
            
#             # Check if Gemini wants to use our MCP tool
#             if response.function_calls:
#                 tool_call = response.function_calls[0]
                
#                 # Execute the tool via our Django-powered MCP server
#                 mcp_result = await mcp_session.call_tool(tool_call.name, tool_call.args)
#                 tool_output_text = mcp_result.content[0].text
                
#                 # Turn 2: Feed the output back into the history so Gemini can synthesize the answer
#                 final_response = ai_client.models.generate_content(
#                     model="gemini-2.5-flash",
#                     contents=[
#                         types.Content(role="user", parts=[types.Part.from_text(text=prompt)]),
#                         types.Content(role="model", parts=[types.Part.from_function_call(
#                             name=tool_call.name,
#                             args=tool_call.args
#                         )]),
#                         types.Content(role="user", parts=[types.Part.from_function_response(
#                             name=tool_call.name,
#                             response={"result": tool_output_text}
#                         )])
#                     ]
#                 )
#                 print("final_response >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>", final_response.text)
#                 return final_response.text
                
#             return response.text

@shared_task(name="apps.organizations.tasks.run_ai_org_lookup")
def run_ai_org_lookup(target_email):
    """Celery entrypoint mapping the async engine to the synchronous worker pool"""
    return asyncio.run(run_mcp_agent_loop(target_email))