# ruff: noqa
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

import datetime
import json
import os
import subprocess
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

from google import genai
from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.apps import App
from google.adk.code_executors import AgentEngineSandboxCodeExecutor
from google.adk.memory import VertexAiMemoryBankService
from google.adk.models import Gemini
from google.adk.tools import ToolContext
from google.adk.tools.preload_memory_tool import PreloadMemoryTool
from google.cloud import firestore, storage
from google.genai import types

from a2ui.basic_catalog.provider import BasicCatalog
from a2ui.schema.manager import A2uiSchemaManager

from .a2ui_utils import a2ui_callback

PROJECT_ID = "qwiklabs-gcp-02-390bef78edb6"
COLLECTION_NAME = "dev_tickets"
BUCKET_NAME = "devcraft-media-qwiklabs-gcp-02-390bef78edb6"
MEMORY_BANK_ID = "5937562901106655232"

os.environ.setdefault("GOOGLE_CLOUD_AGENT_ENGINE_ID", MEMORY_BANK_ID)

if os.path.exists("/tmp/sa_key.json") and "GOOGLE_APPLICATION_CREDENTIALS" not in os.environ:
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "/tmp/sa_key.json"


def _get_firestore_db():
    return firestore.Client(project=PROJECT_ID)


def _get_agent_engine_id() -> str:
    metadata_path = os.path.join(os.path.dirname(__file__), "..", "deployment_metadata.json")
    if os.path.exists(metadata_path):
        try:
            with open(metadata_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
                remote_id = meta.get("remote_agent_runtime_id")
                if remote_id:
                    return remote_id
        except Exception as e:
            print(f"Warning: Could not read deployment_metadata.json: {e}")
    return f"projects/{PROJECT_ID}/locations/us-east1/reasoningEngines/{MEMORY_BANK_ID}"


def get_memory_service():
    """Provides VertexAiMemoryBankService for deployed Agent Runtime instances."""
    return VertexAiMemoryBankService(
        project=PROJECT_ID,
        location="us-east1",
        agent_engine_id=MEMORY_BANK_ID,
    )


async def generate_memories_callback(callback_context: CallbackContext):
    """Callback triggered after each turn to persist session facts to Vertex AI Memory Bank."""
    try:
        await callback_context.add_session_to_memory()
    except Exception as e:
        print(f"Note: Memory service not active or unavailable in current runner context ({e})")
    return None


def generate_developer_asset_image(
    prompt: str,
    tool_context: ToolContext = None,
) -> str:
    """Generates an image asset for a developer project (architecture diagram, logo, or icon) using Gemini.

    Args:
        prompt: Detailed description of the developer image asset to generate.
        tool_context: ToolContext injected automatically by ADK framework.

    Returns:
        The public HTTPS Cloud Storage URL of the generated image.
    """
    try:
        genai_client = genai.Client(
            vertexai=True,
            project=PROJECT_ID,
            location="global",
        )

        res = genai_client.models.generate_content(
            model="gemini-3.1-flash-lite-image",
            contents=prompt.strip(),
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
            ),
        )

        img_bytes = None
        mime_type = "image/jpeg"
        if res.candidates and res.candidates[0].content and res.candidates[0].content.parts:
            for part in res.candidates[0].content.parts:
                if part.inline_data and part.inline_data.data:
                    img_bytes = part.inline_data.data
                    mime_type = part.inline_data.mime_type or "image/jpeg"
                    break

        if not img_bytes:
            return f"Failed to generate image for prompt: '{prompt}'."

        ext = "png" if "png" in mime_type.lower() else "jpg"
        timestamp = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
        object_name = f"devcraft_asset_{timestamp}.{ext}"

        # 1. Save artifact if tool_context is provided
        if tool_context and hasattr(tool_context, "save_artifact"):
            try:
                artifact_part = types.Part.from_bytes(data=img_bytes, mime_type=mime_type)
                tool_context.save_artifact(filename=object_name, artifact=artifact_part)
            except Exception as e:
                print(f"Warning: Failed to save artifact: {e}")

        # 2. Upload image bytes directly to public Cloud Storage bucket
        storage_client = storage.Client(project=PROJECT_ID)
        bucket = storage_client.bucket(BUCKET_NAME)
        blob = bucket.blob(object_name)
        blob.upload_from_string(img_bytes, content_type=mime_type)

        public_url = f"https://storage.googleapis.com/{BUCKET_NAME}/{object_name}"
        return public_url
    except Exception as e:
        return f"Error generating or uploading image: {e}"


def geocode_address(address: str) -> str:
    """Converts a physical address or location name into geographic coordinates (latitude, longitude).

    Args:
        address: The physical address or location name to geocode (e.g. '1600 Amphitheatre Pkwy, Mountain View, CA').

    Returns:
        A formatted string with address, latitude, and longitude.
    """
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not api_key:
        return "Error: GOOGLE_MAPS_API_KEY environment variable is not set."

    try:
        encoded_address = urllib.parse.quote(address.strip())
        url = f"https://maps.googleapis.com/maps/api/geocode/json?address={encoded_address}&key={api_key}"

        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        if data.get("status") != "OK" or not data.get("results"):
            return f"Geocoding failed for address '{address}'. Status: {data.get('status')}"

        first_result = data["results"][0]
        location = first_result["geometry"]["location"]
        return str({
            "address": first_result.get("formatted_address"),
            "latitude": location.get("lat"),
            "longitude": location.get("lng"),
        })
    except Exception as e:
        return f"Error calling Geocoding API: {e}"


def find_nearby_places(
    latitude: float,
    longitude: float,
    place_type: str = "restaurant",
    radius_meters: float = 1000.0,
) -> str:
    """Finds nearby places of a given type around coordinates using the Places API (New).

    Args:
        latitude: Center latitude coordinate.
        longitude: Center longitude coordinate.
        place_type: Type of place to search for (e.g. 'restaurant', 'cafe', 'bar', 'park').
        radius_meters: Search radius in meters (defaults to 1000.0).

    Returns:
        A list of nearby places containing name, address, and location coordinates.
    """
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not api_key:
        return "Error: GOOGLE_MAPS_API_KEY environment variable is not set."

    try:
        url = "https://places.googleapis.com/v1/places:searchNearby"
        payload = json.dumps({
            "includedTypes": [place_type.strip().lower()],
            "maxResultCount": 5,
            "locationRestriction": {
                "circle": {
                    "center": {"latitude": latitude, "longitude": longitude},
                    "radius": float(radius_meters),
                }
            },
        }).encode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.location",
        }

        req = urllib.request.Request(url, data=payload, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        places = data.get("places", [])
        if not places:
            return f"No nearby places of type '{place_type}' found within {radius_meters}m."

        results = []
        for place in places:
            display_name = place.get("displayName", {}).get("text", "Unknown")
            address = place.get("formattedAddress", "Unknown")
            loc = place.get("location", {})
            results.append({
                "name": display_name,
                "address": address,
                "location": {
                    "latitude": loc.get("latitude"),
                    "longitude": loc.get("longitude"),
                },
            })
        return str(results)
    except Exception as e:
        return f"Error calling Places API (New): {e}"


def search_github_repositories(query: str, limit: int = 5) -> str:
    """Searches public GitHub repositories for open source projects or libraries matching a query.

    Args:
        query: Search term or keyword (e.g. 'agent framework', 'fastapi', 'vector database').
        limit: Max number of top repositories to return (defaults to 5).

    Returns:
        A list of matching GitHub repositories with name, stars, language, description, and URL.
    """
    try:
        encoded_query = urllib.parse.quote(query.strip())
        url = f"https://api.github.com/search/repositories?q={encoded_query}&per_page={min(limit, 10)}"

        headers = {
            "User-Agent": "DevCraft-Agent/1.0",
            "Accept": "application/vnd.github.v3+json",
        }

        # Read API key / token from environment variable if configured
        github_token = os.environ.get("GITHUB_TOKEN")
        if github_token:
            headers["Authorization"] = f"token {github_token}"

        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        items = data.get("items", [])
        if not items:
            return f"No GitHub repositories found for query: '{query}'"

        results = [
            {
                "name": item.get("full_name"),
                "stars": item.get("stargazers_count"),
                "language": item.get("language"),
                "description": item.get("description"),
                "url": item.get("html_url"),
            }
            for item in items
        ]
        return str(results)
    except Exception as e:
        return f"Error querying GitHub API: {e}"


def get_git_status_and_diff(staged_only: bool = False) -> str:
    """Inspects the local git repository status and uncommitted code diffs.

    Args:
        staged_only: If True, only returns diff for staged changes. Otherwise returns all uncommitted changes.

    Returns:
        A string containing git status summary and uncommitted diffs.
    """
    try:
        status_cmd = ["git", "status", "--short"]
        status_res = subprocess.run(status_cmd, capture_output=True, text=True, timeout=5)
        status_out = status_res.stdout.strip()

        diff_cmd = ["git", "diff", "--staged"] if staged_only else ["git", "diff"]
        diff_res = subprocess.run(diff_cmd, capture_output=True, text=True, timeout=5)
        diff_out = diff_res.stdout.strip()

        if not status_out and not diff_out:
            return "Git working directory is clean. No uncommitted changes found."

        summary = f"--- Git Status ---\n{status_out if status_out else 'No modified files in status'}\n\n--- Git Diff ---\n"
        if not diff_out:
            summary += "No unstaged diffs."
        else:
            if len(diff_out) > 3000:
                diff_out = diff_out[:3000] + "\n... [diff output truncated]"
            summary += diff_out

        return summary
    except Exception as e:
        return f"Error executing git command: {e}"


def list_dev_tickets(status: str = "") -> str:
    """Lists developer tickets from the Firestore database, optionally filtered by status.

    Args:
        status: Optional filter for ticket status (e.g. 'open', 'in_review', 'closed'). Leave empty to fetch all.

    Returns:
        A list of developer tickets with details.
    """
    try:
        db = _get_firestore_db()
        col = db.collection(COLLECTION_NAME)
        if status:
            query = col.where("status", "==", status.strip().lower())
            docs = query.stream()
        else:
            docs = col.stream()

        tickets = [d.to_dict() for d in docs]
        if not tickets:
            return f"No tickets found matching status: '{status}'" if status else "No developer tickets found."
        return str(tickets)
    except Exception as e:
        return f"Error querying Firestore: {e}"


def get_dev_ticket(ticket_id: str) -> str:
    """Retrieves a specific developer ticket from Firestore by its ticket ID (e.g. 'TICK-101').

    Args:
        ticket_id: The ID of the ticket to fetch (e.g. 'TICK-101').

    Returns:
        Ticket details if found, or an error message.
    """
    try:
        db = _get_firestore_db()
        doc = db.collection(COLLECTION_NAME).document(ticket_id.strip()).get()
        if doc.exists:
            return str(doc.to_dict())
        return f"Ticket '{ticket_id}' not found."
    except Exception as e:
        return f"Error reading ticket from Firestore: {e}"


def create_dev_ticket(
    ticket_id: str,
    title: str,
    author: str,
    priority: str = "medium",
    description: str = "",
) -> str:
    """Creates a new developer ticket in the Firestore database.

    Args:
        ticket_id: Unique identifier for the ticket (e.g., 'TICK-105').
        title: Short descriptive title of the issue or feature request.
        author: Developer creating the ticket.
        priority: Priority level ('low', 'medium', 'high', 'critical'). Defaults to 'medium'.
        description: Detailed explanation of the issue or task.

    Returns:
        Confirmation message of ticket creation.
    """
    try:
        db = _get_firestore_db()
        data = {
            "ticket_id": ticket_id.strip(),
            "title": title.strip(),
            "author": author.strip(),
            "assigned_to": "unassigned",
            "status": "open",
            "priority": priority.strip().lower(),
            "description": description.strip(),
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        db.collection(COLLECTION_NAME).document(ticket_id.strip()).set(data)
        return f"Successfully created ticket '{ticket_id}': {title}"
    except Exception as e:
        return f"Error writing ticket to Firestore: {e}"


def update_ticket_status(ticket_id: str, status: str) -> str:
    """Updates the status of an existing developer ticket in Firestore.

    Args:
        ticket_id: The ID of the ticket to update (e.g., 'TICK-101').
        status: The new status ('open', 'in_review', 'approved', 'closed').

    Returns:
        Confirmation message of status update.
    """
    try:
        db = _get_firestore_db()
        doc_ref = db.collection(COLLECTION_NAME).document(ticket_id.strip())
        if not doc_ref.get().exists:
            return f"Ticket '{ticket_id}' does not exist."
        doc_ref.update({"status": status.strip().lower()})
        return f"Successfully updated ticket '{ticket_id}' status to '{status}'."
    except Exception as e:
        return f"Error updating ticket status in Firestore: {e}"


def get_weather(query: str) -> str:
    """Simulates a web search. Use it get information on weather.

    Args:
        query: A string containing the location to get weather information for.

    Returns:
        A string with the simulated weather information for the queried location.
    """
    if "sf" in query.lower() or "san francisco" in query.lower():
        return "It's 60 degrees and foggy."
    return "It's 90 degrees and sunny."


def get_current_time(query: str) -> str:
    """Simulates getting the current time for a city.

    Args:
        city: The name of the city to get the current time for.

    Returns:
        A string with the current time information.
    """
    if "sf" in query.lower() or "san francisco" in query.lower():
        tz_identifier = "America/Los_Angeles"
    else:
        return f"Sorry, I don't have timezone information for query: {query}."

    tz = ZoneInfo(tz_identifier)
    now = datetime.datetime.now(tz)
    return f"The current time for query {query} is {now.strftime('%Y-%m-%d %H:%M:%S %Z%z')}"


schema_manager = A2uiSchemaManager(
    version="0.8",
    catalogs=[BasicCatalog.get_config("0.8")],
)

a2ui_instruction = schema_manager.generate_system_prompt(
    role_description=(
        "You are DevCraft, an expert developer assistant. You remember the user's stated preferences, "
        "facts, and specific instructions across conversations. "
        "CRITICAL: Always remember and apply all user corrections, feedback, and repeated preferences regarding "
        "Merge Request (MR) comments, code review style, pull requests, and coding standards. "
        "When a user provides feedback or corrects how MR comments should be phrased or formatted, "
        "strictly store and adhere to those MR comment rules in all subsequent interactions and sessions."
    ),
    workflow_description="Analyze the user request, run developer tools, and return structured UI when appropriate.",
    ui_description=(
        "Keep every surface tiny and flat: ONE Card > ONE Column > a few Text rows. "
        "Never nest a Card inside a Card. "
        "Use ONLY these components: Card, Column, Row, Text, and Image. Do not use "
        "Table or Heading (unsupported), or Buttons, actions, or forms (they do "
        "nothing in adk web). "
        "You may include one Image component, but only when you have a public https "
        "URL for the image (for example the URL an image tool returns after uploading "
        "to a public bucket). Set the Image url to that exact https link, for example "
        '{"Image": {"url": {"literalString": "https://..."}}}. Never point an '
        "Image at a bare filename, an artifact name, or a non-http(s) path. If you do "
        "not have a public URL, add a short Text line noting the image instead. "
        "No markdown in text; use the usageHint property ('h1', 'h2', 'body') for "
        "headings and emphasis. "
        "Output ONLY the raw A2UI JSON array — no prose, and never wrap it in "
        "<a2a_datapart_json> tags or 'kind'/'data'/'metadata' objects."
    ),
    include_schema=True,
    include_examples=True,
)


root_agent = Agent(
    name="root_agent",
    model=Gemini(
        model="gemini-2.5-flash",
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    code_executor=AgentEngineSandboxCodeExecutor(
        agent_engine_resource_name=_get_agent_engine_id()
    ),
    instruction=a2ui_instruction,
    tools=[
        PreloadMemoryTool(),
        generate_developer_asset_image,
        geocode_address,
        find_nearby_places,
        search_github_repositories,
        get_git_status_and_diff,
        list_dev_tickets,
        get_dev_ticket,
        create_dev_ticket,
        update_ticket_status,
        get_weather,
        get_current_time,
    ],
    after_model_callback=a2ui_callback,
    after_agent_callback=generate_memories_callback,
)

app = App(
    root_agent=root_agent,
    name="app",
)
