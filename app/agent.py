# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Chef Agent - Graph Workflow implementation using ADK 2.0.

This agent parses a kitchen inventory from a raw user request, selects or generates
a recipe based on those ingredients, asks the user for confirmation via a RequestInput
interrupt, and finally routes to generate detailed instructions or cancels the flow.
"""

import os
import json
import re
from typing import Optional, List, Any
import google.auth
from pydantic import BaseModel, Field

# ADK 2.0 Imports
from google.adk.apps import App
from google.adk.workflow import Workflow, Edge, START, node
from google.adk.agents.context import Context
from google.adk.events import RequestInput
from google.genai import types

# Establish Google Cloud environment variables for standard Vertex AI usage.
# We include a fallback mechanism to prevent initialization errors if default
# credentials are not configured in local test environments.
try:
    _, project_id = google.auth.default()
    os.environ["GOOGLE_CLOUD_PROJECT"] = project_id
except Exception:
    os.environ["GOOGLE_CLOUD_PROJECT"] = "mock-project-id"

os.environ["GOOGLE_CLOUD_LOCATION"] = "global"
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "True"


# =====================================================================
# 1. State Model Definition
# =====================================================================

class KitchenState(BaseModel):
    """
    Pydantic model representing the global shared state of the Kitchen Workflow.
    All properties are declared with Field metadata and matched by node arguments.
    """
    raw_input: Optional[str] = Field(
        None, 
        description="The raw input text describing ingredients or kitchen state."
    )
    structured_inventory: Optional[List[str]] = Field(
        None, 
        description="List of clean, structured food ingredients extracted from raw input."
    )
    selected_recipe: Optional[str] = Field(
        None, 
        description="The recipe select by the chef agent (e.g. 'Egg Fried Rice')."
    )
    final_instructions: Optional[str] = Field(
        None, 
        description="The final output instructions or meal preparation directions."
    )


# =====================================================================
# 2. Workflow Nodes
# =====================================================================

@node(name="parse_ingredients")
def parse_ingredients(ctx: Context, node_input: str) -> str:
    """
    Parses the initial raw text input to identify clean, individual ingredients.
    
    Reads the initial entry text from the node_input, updates raw_input in the
    state schema, parses the items, and saves them to structured_inventory.
    """
    # 1. Capture the raw input in state
    raw_text = str(node_input)
    ctx.state["raw_input"] = raw_text
    
    # 2. Attempt to parse ingredients
    ingredients = []
    
    # Tries using Gemini via google-genai library if configured
    try:
        from google.genai import Client
        client = Client()
        prompt = (
            "You are a parser. Extract a list of food ingredients from this text: "
            f"'{raw_text}'. Return ONLY a JSON list of strings (e.g., ['egg', 'rice'])."
        )
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )
        # Parse the JSON response
        response_text = response.text or ""
        json_match = re.search(r"\[.*\]", response_text, re.DOTALL)
        if json_match:
            ingredients = json.loads(json_match.group(0))
    except Exception as e:
        # Raise authentication or API key errors to notify user
        if any(keyword in str(e) for keyword in ("API key", "API_KEY", "credentials", "authenticated")):
            raise e
        # Fallback to local heuristic parsing if offline
        cleaned = raw_text.replace("and", ",").replace(".", ",")
        for part in cleaned.split(","):
            item = part.strip().strip(".-*").strip()
            if item:
                ingredients.append(item)
                
    # 3. Security Validation Gate: Verify that no expired or hazardous ingredients or combinations are processed
    hazardous_keywords = {
        "expired", "rotten", "moldy", "poison", "bleach", "ammonia", 
        "toxic", "spoiled", "arsenic", "cyanide", "chemical", "detergent"
    }
    
    ingredients_lower = [i.lower() for i in ingredients]
    
    # Check for direct hazardous or expired keywords in parsed items
    for idx, item in enumerate(ingredients):
        item_lower = ingredients_lower[idx]
        if any(bad_word in item_lower for bad_word in hazardous_keywords):
            raise ValueError(
                f"Security Validation Alert: Expired or hazardous ingredient detected: '{item}'. "
                "Processing aborted for safety."
            )
            
    # Check for hazardous combination: Bleach + Ammonia (chloramine gas hazard)
    if any("bleach" in i for i in ingredients_lower) and any("ammonia" in i for i in ingredients_lower):
        raise ValueError(
            "Security Validation Alert: Extremely dangerous chemical combination detected (Bleach + Ammonia). "
            "Processing aborted for safety."
        )

    # 4. Store parsed inventory in state
    ctx.state["structured_inventory"] = ingredients
    return f"Successfully structured {len(ingredients)} ingredients."


@node(name="generate_recipe")
def generate_recipe(ctx: Context, structured_inventory: List[str]) -> str:
    """
    Suggests a recipe based on structured_inventory.
    
    Uses parameter binding to retrieve structured_inventory automatically
    from the global state schema.
    """
    recipe = None
    ingredients_str = ", ".join(structured_inventory)
    
    # Attempt to use Gemini to suggest a creative recipe
    try:
        from google.genai import Client
        client = Client()
        prompt = (
            "Suggest a single meal recipe name that can be made using some or all "
            f"of these ingredients: {ingredients_str}. Return ONLY the recipe name "
            "(e.g., 'Tomato Basil Pasta')."
        )
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )
        recipe = response.text.strip().strip("'\"") if response.text else None
    except Exception as e:
        if any(keyword in str(e) for keyword in ("API key", "API_KEY", "credentials", "authenticated")):
            raise e
        
    # Heuristic fallback if API call fails
    if not recipe:
        inv_lower = [i.lower() for i in structured_inventory]
        if any("rice" in r for r in inv_lower) and any("egg" in r for r in inv_lower):
            recipe = "Classic Egg Fried Rice"
        elif any("pasta" in r for r in inv_lower) or any("noodle" in r for r in inv_lower):
            recipe = "Spiced Tomato Pasta"
        else:
            recipe = "Chef's Garden Salad"
            
    # Save the selected recipe to global state
    ctx.state["selected_recipe"] = recipe
    return f"Chef selected recipe: {recipe}"


@node(name="get_user_confirmation", rerun_on_resume=True)
def get_user_confirmation(ctx: Context, selected_recipe: str) -> Any:
    """
    Asks the user for recipe confirmation using a RequestInput interrupt.
    
    If resuming, retrieves user confirmation from resume_inputs and routes 
    the flow. Otherwise, returns a RequestInput object to pause execution.
    """
    interrupt_id = "confirm_recipe"
    
    # Check if we have received a response for this interrupt during resume
    if interrupt_id in ctx.resume_inputs:
        user_response = ctx.resume_inputs[interrupt_id]
        
        # Handle dict or string response payloads
        response_str = ""
        if isinstance(user_response, dict):
            response_str = str(user_response.get("result", "") or user_response.get("confirm", "")).lower()
        else:
            response_str = str(user_response).lower()
            
        # Inspect user confirmation and set conditional route
        if any(yes in response_str for yes in ("yes", "y", "confirm", "approve", "true")):
            ctx.route = "yes"
            return "Recipe approved by user."
        else:
            ctx.route = "no"
            return "Recipe rejected by user."
            
    # Interrupt and request user confirmation
    return RequestInput(
        interrupt_id=interrupt_id,
        message=f"The chef selected: '{selected_recipe}'. Do you confirm this recipe? (yes/no)",
        response_schema={"type": "string"}
    )


@node(name="finalize_instructions")
def finalize_instructions(ctx: Context, selected_recipe: str) -> str:
    """
    Generates step-by-step cooking instructions for the approved recipe.
    """
    instructions = None
    try:
        from google.genai import Client
        client = Client()
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=f"Generate detailed step-by-step cooking instructions for: {selected_recipe}.",
        )
        instructions = response.text
    except Exception as e:
        if any(keyword in str(e) for keyword in ("API key", "API_KEY", "credentials", "authenticated")):
            raise e
        
    if not instructions:
        instructions = (
            f"--- Cooking Instructions for {selected_recipe} ---\n"
            f"1. Preparation: Clean and chop your inventory ingredients.\n"
            f"2. Method: Heat your pan/pot and cook the items to perfection.\n"
            f"3. Finish: Plating and serving hot!"
        )
        
    ctx.state["final_instructions"] = instructions
    return instructions


@node(name="cancel_recipe")
def cancel_recipe(ctx: Context) -> str:
    """
    Handles rejection flow when user declines the chef's suggestion.
    """
    instructions = "Recipe generation canceled by the user. Please restart with a new ingredient list."
    ctx.state["final_instructions"] = instructions
    return instructions


# =====================================================================
# 3. Graph Workflow Assembly
# =====================================================================

kitchen_workflow = Workflow(
    name="kitchen_workflow",
    state_schema=KitchenState,
    edges=[
        # START node transfers raw input into parse_ingredients
        (START, parse_ingredients),
        # parse_ingredients feeds structured inventory to generate_recipe
        (parse_ingredients, generate_recipe),
        # generate_recipe transitions to get_user_confirmation node
        (generate_recipe, get_user_confirmation),
        # Conditional edge: Route 'yes' maps to finalize_instructions
        Edge(from_node=get_user_confirmation, to_node=finalize_instructions, route="yes"),
        # Conditional edge: Route 'no' maps to cancel_recipe
        Edge(from_node=get_user_confirmation, to_node=cancel_recipe, route="no"),
    ]
)


# =====================================================================
# 4. App Definition
# =====================================================================

# Export 'app' as the main entry point to satisfy reasoning engines and runner loading.
app = App(
    root_agent=kitchen_workflow,
    name="chef-agent-app",
)
