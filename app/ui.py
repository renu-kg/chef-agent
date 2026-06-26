import os
import sys

# Ensure the containing project root is in python path to allow 'app' package imports
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Load .env file if it exists in the project root
env_path = os.path.join(project_root, ".env")
if os.path.exists(env_path):
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ[k.strip()] = v.strip()

import streamlit as st
import asyncio
import uuid
from app.agent import app
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

# Page styling and configurations for premium design aesthetics
st.set_page_config(
    page_title="ChefAgent - AI Kitchen Assistant",
    page_icon="🍳",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS for modern styling, smooth fonts, and premium card layouts
st.markdown("""
<style>
    .main {
        background-color: #f8fafc;
    }
    .stButton>button {
        background: linear-gradient(135deg, #FF4B2B 0%, #FF416C 100%);
        color: white;
        border: none;
        border-radius: 8px;
        padding: 10px 24px;
        font-weight: 600;
        transition: transform 0.2s ease, box-shadow 0.2s ease;
    }
    .stButton>button:hover {
        transform: translateY(-2px);
        box-shadow: 0 4px 15px rgba(255, 75, 43, 0.4);
        color: white;
    }
    .stTextArea>div>div>textarea {
        border-radius: 8px;
        border: 1px solid #CBD5E1;
    }
    .card {
        background-color: white;
        padding: 24px;
        border-radius: 12px;
        box-shadow: 0 4px 6px rgba(0, 0, 0, 0.05);
        margin-bottom: 20px;
    }
    .logo-title {
        font-family: 'Outfit', sans-serif;
        color: #0F172A;
        font-weight: 800;
    }
</style>
""", unsafe_allow_html=True)

# Helper to run ADK 2.0 runner's async run generator synchronously in Streamlit's event loop
def run_runner_sync(runner, user_id, session_id, new_message=None, invocation_id=None):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    events = []
    async def collect():
        async for event in runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=new_message,
            invocation_id=invocation_id
        ):
            events.append(event)
            
    loop.run_until_complete(collect())
    loop.close()
    return events

# Initialize persistent session states in Streamlit
if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())
if "runner" not in st.session_state:
    st.session_state.runner = Runner(
        app=app,
        session_service=InMemorySessionService(),
        auto_create_session=True
    )
if "interrupted" not in st.session_state:
    st.session_state.interrupted = False
if "interrupt_id" not in st.session_state:
    st.session_state.interrupt_id = None
if "events" not in st.session_state:
    st.session_state.events = []
if "recipe_name" not in st.session_state:
    st.session_state.recipe_name = None

st.markdown('<h1 class="logo-title">🍳 ChefAgent - AI Kitchen Assistant</h1>', unsafe_allow_html=True)
st.write("Optimize food waste, save cooking time, and follow beginner-friendly chef instructions.")
st.markdown("---")

# Profile details in the Sidebar
with st.sidebar:
    st.markdown("### 🧑‍🍳 Culinary Expert Profile")
    st.info(
        "⚡ **Zero Food Waste** active.\n\n"
        "🕒 **Quick Prep Priority** (<30 mins).\n\n"
        "💬 **Friendly Chef** tone active."
    )

col1, col2 = st.columns([1, 2])

with col1:
    st.markdown("### 📋 Kitchen Inventory")
    ingredients_input = st.text_area(
        "List your available ingredients:",
        placeholder="e.g. pasta, tomato, garlic, onion",
        height=150
    )
    
    cooking_time = st.slider(
        "Cooking Time Preference (minutes)",
        min_value=5,
        max_value=90,
        value=30,
        step=5
    )
    
    generate_clicked = st.button("🍳 Generate Recipe", use_container_width=True)

with col2:
    st.markdown("### 🍽️ Recipe Planning & Instructions")
    
    # Process initial workflow invocation
    if generate_clicked:
        if not os.environ.get("GEMINI_API_KEY"):
            st.error("🔑 Gemini API Key is missing. Please create a `.env` file in the project root with `GEMINI_API_KEY` configured.")
        elif not ingredients_input.strip():
            st.warning("Please type in at least one ingredient.")
        else:
            with st.spinner("Chef is checking inventory safety and planning a recipe..."):
                # Clear previous session and state
                st.session_state.session_id = str(uuid.uuid4())
                st.session_state.interrupted = False
                st.session_state.interrupt_id = None
                st.session_state.recipe_name = None
                st.session_state.events = []
                
                # Build raw input text incorporating the prep time preference
                raw_input_text = f"{ingredients_input} (optimize for {cooking_time} mins prep/cooking time)"
                new_msg = types.Content(parts=[types.Part(text=raw_input_text)])
                
                # Execute the ADK workflow
                try:
                    events = run_runner_sync(
                        st.session_state.runner,
                        "streamlit_user",
                        st.session_state.session_id,
                        new_message=new_msg
                    )
                    st.session_state.events = events
                    
                    # Scan generated events to check if we hit a RequestInput interrupt
                    for event in events:
                        if event.content and event.content.parts:
                            for part in event.content.parts:
                                if part.function_call and part.function_call.name == "adk_request_input":
                                    st.session_state.interrupted = True
                                    st.session_state.interrupt_id = part.function_call.id
                                    st.session_state.confirm_message = part.function_call.args.get("message")
                except Exception as e:
                    st.error(f"⚠️ Validation Failed: {e}")
                    
    # Render accumulated steps and final cooking instruction formatting
    for event in st.session_state.events:
        if event.author == "kitchen_workflow" and event.output:
            output_text = str(event.output)
            
            # Format outputs depending on which node yielded them
            if "Successfully structured" in output_text:
                st.info(f"🔍 **Ingredients Structured**: {output_text}")
            elif "Chef selected recipe:" in output_text:
                st.success(f"🍴 **Chef Selection**: {output_text}")
            elif "Recipe approved" in output_text or "Recipe rejected" in output_text:
                st.markdown(f"💬 *{output_text}*")
            elif "Cooking Instructions" in output_text or output_text.startswith("---"):
                st.markdown(f"### 📖 Step-By-Step Cooking Guide\n{output_text}")
            else:
                st.write(output_text)
                
    # Human-In-The-Loop (HITL) prompt section
    if st.session_state.interrupted:
        st.warning(f"🤔 **Confirmation**: {st.session_state.confirm_message}")
        
        # Confirmation buttons layout
        confirm_col1, confirm_col2 = st.columns(2)
        
        with confirm_col1:
            if st.button("✅ Yes, sounds delicious!", use_container_width=True):
                with st.spinner("Finalizing instructions..."):
                    resume_msg = types.Content(
                        parts=[
                            types.Part(
                                function_response=types.FunctionResponse(
                                    id=st.session_state.interrupt_id,
                                    name="adk_request_input",
                                    response={"result": "yes"}
                                )
                            )
                        ]
                    )
                    resume_events = run_runner_sync(
                        st.session_state.runner,
                        "streamlit_user",
                        st.session_state.session_id,
                        new_message=resume_msg
                    )
                    st.session_state.events.extend(resume_events)
                    st.session_state.interrupted = False
                    st.session_state.interrupt_id = None
                    st.rerun()
                    
        with confirm_col2:
            if st.button("❌ No, choose something else", use_container_width=True):
                with st.spinner("Cancelling flow..."):
                    resume_msg = types.Content(
                        parts=[
                            types.Part(
                                function_response=types.FunctionResponse(
                                    id=st.session_state.interrupt_id,
                                    name="adk_request_input",
                                    response={"result": "no"}
                                )
                            )
                        ]
                    )
                    resume_events = run_runner_sync(
                        st.session_state.runner,
                        "streamlit_user",
                        st.session_state.session_id,
                        new_message=resume_msg
                    )
                    st.session_state.events.extend(resume_events)
                    st.session_state.interrupted = False
                    st.session_state.interrupt_id = None
                    st.rerun()
