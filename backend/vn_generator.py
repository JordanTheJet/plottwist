# File: vn_generator.py
import asyncio
import json
import random
import re
import os
import time
from typing import Dict, List, Any, Optional, Tuple

# Import Google Gemini library for generation
try:
    import google.generativeai as genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False
    print("Google Generative AI package not available. To install: pip install google-generativeai")

# Enhanced logging for Gemini operations
def log_gemini(message, level="INFO"):
    """Log Gemini-related operations with timestamp and log level"""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    # Always print logs regardless of level
    print(f"[GEMINI][{timestamp}][{level}] {message}")

import tempfile
import replicate
import base64
import requests
from io import BytesIO
from PIL import Image

# Add this to the global variables section
# Cache for generated images to avoid regenerating them
IMAGE_CACHE = {
    "characters": {},
    "backgrounds": {}
}


# Global cache for story continuation
STORY_CACHE = {
    "book_analysis": None,      # Store book analysis for reference
    "generated_scenes": {},     # Cache of all generated scenes
    "scene_graph": {},          # Map showing connections between scenes
    "in_progress_scenes": set() # Set of scenes currently being generated
}

def initialize_gemini():
    """Initialize the Gemini API client if API key is available"""
    global GEMINI_AVAILABLE

    try:
        api_key = os.environ.get("GOOGLE_API_KEY")
        if api_key and GEMINI_AVAILABLE:
            log_gemini(f"Configuring Gemini with API key: {api_key[:4]}...{api_key[-4:]}")
            genai.configure(api_key=api_key)

            # Test if Gemini is working by getting available models
            log_gemini("Fetching available Gemini models...")
            models = genai.list_models()
            gemini_models = [m for m in models if "gemini" in m.name.lower()]

            if gemini_models:
                model_names = [m.name for m in gemini_models]
                log_gemini(f"✅ Gemini API initialized successfully", "INFO")
                log_gemini(f"Available models: {model_names}", "INFO")

                # Check for specific models we want to use
                if "gemini-1.5-pro" in model_names:
                    log_gemini("✅ Gemini 1.5 Pro is available (preferred model)", "INFO")
                elif "gemini-pro" in model_names:
                    log_gemini("⚠️ Gemini Pro is available (fallback model)", "WARNING")

                return True
            else:
                log_gemini("Gemini API initialized but no Gemini models found", "WARNING")
                return False
        else:
            if not api_key:
                log_gemini("GOOGLE_API_KEY not found in environment variables", "ERROR")
            elif not GEMINI_AVAILABLE:
                log_gemini("Gemini package not properly installed", "ERROR")
            return False
    except Exception as e:
        log_gemini(f"Error initializing Gemini API: {str(e)}", "ERROR")
        return False

async def generate_visual_novel(book_analysis: dict) -> dict:
    """
    Generate a visual novel script with branching paths from the book analysis
    Returns a structured visual novel script
    """
    try:
        # Store book analysis in global cache for future reference
        STORY_CACHE["book_analysis"] = book_analysis
        STORY_CACHE["generated_scenes"] = {}
        STORY_CACHE["scene_graph"] = {}
        STORY_CACHE["in_progress_scenes"] = set()

        # Track which model was used for generation
        generation_info = {
            "outline_model": "Gemini",
            "scene_models": {},
            "gemini_available": False
        }

        # Check if Gemini is available
        gemini_ready = False
        if GEMINI_AVAILABLE:
            log_gemini("\nChecking Gemini API availability...")
            gemini_ready = initialize_gemini()
            if gemini_ready:
                log_gemini("\n✅ Gemini API is available and will be used exclusively", "INFO")
                generation_info["gemini_available"] = True
            else:
                log_gemini("\n❌ Gemini API is not available, cannot proceed", "ERROR")
                return await generate_placeholder_script(book_analysis)
        else:
            log_gemini("\n❌ Gemini package not installed, cannot proceed", "ERROR")
            return await generate_placeholder_script(book_analysis)

        # Use Gemini for script generation
        script_data = await generate_initial_script_with_gemini(book_analysis)

        # Enhance with visual elements
        script_data = await enhance_visual_novel(script_data, book_analysis.get("characters", []))

        # Validate scene connections
        script_data = validate_and_fix_scene_connections(script_data)

        # Update the scene graph
        update_scene_graph(script_data)

        # Add generation info to the script data
        script_data["generation_info"] = generation_info

        # Print summary of which models were used
        log_gemini("\n📊 GENERATION SUMMARY:", "INFO")
        log_gemini(f"Gemini Available: {generation_info['gemini_available']}", "INFO")
        log_gemini(f"Outline Generated with: {generation_info['outline_model']}", "INFO")
        log_gemini("Scenes Generated with:", "INFO")
        for scene_id, model in generation_info.get("scene_models", {}).items():
            log_gemini(f"  - Scene {scene_id}: {model}", "INFO")

        return script_data

    except Exception as e:
        log_gemini(f"Error generating script: {str(e)}", "ERROR")
        log_gemini("Falling back to placeholder script", "WARNING")
        return await generate_placeholder_script(book_analysis)

def update_scene_graph(script_data):
    """Update global scene graph with the latest scene connections"""
    scene_graph = STORY_CACHE["scene_graph"]

    # Map all scenes
    for scene in script_data["scenes"]:
        scene_id = scene["id"]

        # Save to generated scenes cache
        STORY_CACHE["generated_scenes"][scene_id] = scene

        # Create entry in scene graph if not exists
        if scene_id not in scene_graph:
            scene_graph[scene_id] = {
                "outgoing": [],
                "incoming": []
            }

        # Find all outgoing connections from this scene
        for dialogue in scene["dialogue"]:
            if "choices" in dialogue:
                for choice in dialogue["choices"]:
                    if "nextScene" in choice and choice["nextScene"] != "exit":
                        # Add outgoing connection
                        if choice["nextScene"] not in [c["target"] for c in scene_graph[scene_id]["outgoing"]]:
                            scene_graph[scene_id]["outgoing"].append({
                                "target": choice["nextScene"],
                                "text": choice["text"]
                            })

                        # Add target scene to graph if not exists
                        if choice["nextScene"] not in scene_graph:
                            scene_graph[choice["nextScene"]] = {
                                "outgoing": [],
                                "incoming": []
                            }

                        # Add incoming connection to target
                        if scene_id not in [c["source"] for c in scene_graph[choice["nextScene"]]["incoming"]]:
                            scene_graph[choice["nextScene"]]["incoming"].append({
                                "source": scene_id,
                                "text": choice["text"]
                            })

    # Update the global cache
    STORY_CACHE["scene_graph"] = scene_graph

async def generate_initial_script_with_gemini(book_analysis: dict) -> dict:
    """
    Generate the initial visual novel script with the first set of scenes using only Gemini
    """
    log_gemini("Generating initial script skeleton with Gemini...", "INFO")

    # Initialize the visual novel script
    vn_script = {
        "title": book_analysis['metadata'].get('title', 'Untitled') + ": Interactive Edition",
        "scenes": []
    }

    # Initialize generation info
    generation_info = {
        "outline_model": "Gemini",
        "scene_models": {},
        "gemini_available": True
    }

    try:
        log_gemini("Using Gemini for script outline generation...", "INFO")
        outline = await generate_script_outline_with_gemini(book_analysis, scene_limit=5)
        generation_info["outline_model"] = "Gemini"

        # Process all planned scenes in parallel
        log_gemini(f"Processing {len(outline.get('scenes', [])[:5])} scenes in parallel...")
        tasks = []
        for scene_outline in outline.get("scenes", [])[:5]:
            tasks.append(generate_scene_from_outline_with_gemini(scene_outline, book_analysis))

        # Wait for all scenes to complete
        log_gemini("Waiting for all scene generation tasks to complete...")
        initial_scenes = await asyncio.gather(*tasks)

        # Check if we got valid scenes
        valid_scenes = [scene for scene in initial_scenes if scene]
        log_gemini(f"Generated {len(valid_scenes)} valid scenes out of {len(initial_scenes)} attempts")

        if valid_scenes:
            # Add scenes to the script
            for i, scene in enumerate(valid_scenes):
                vn_script["scenes"].append(scene)
                scene_id = scene.get("id", f"unknown_{i}")
                generation_info["scene_models"][scene_id] = "Gemini"
                log_gemini(f"Added scene {scene_id} to script", "INFO")

            log_gemini(f"Generated initial script with {len(vn_script['scenes'])} scenes using Gemini", "INFO")
            vn_script["generation_info"] = generation_info
            return vn_script
        else:
            log_gemini("Gemini failed to generate valid scenes, using placeholder", "ERROR")
            return await generate_placeholder_script(book_analysis)
    except Exception as e:
        log_gemini(f"Error using Gemini for initial script: {str(e)}", "ERROR")
        return await generate_placeholder_script(book_analysis)

async def generate_script_outline_with_gemini(book_analysis: dict, scene_limit=10) -> dict:
    """Generate an outline for the script with planned scenes using Gemini"""
    log_gemini("\n🔍 USING GEMINI MODEL FOR SCRIPT OUTLINE GENERATION 🔍", "INFO")

    # Extract key elements from the book analysis
    characters = book_analysis.get("characters", [])
    character_info = ""

    # Create detailed character information for more authentic portrayal
    for char in characters[:7]:  # Limit to 7 important characters
        personality = char.get("personality", "")
        speech = char.get("speech_patterns", "")
        motivations = char.get("motivations", "")

        character_info += f"""
        - {char.get('name', 'Unknown')}: {char.get('role', 'A character')}
          * Description: {char.get('description', 'No description')}
          * Personality: {personality}
          * Speech patterns: {speech}
          * Motivations: {motivations}
          * Relationships: {char.get('relationships', 'Unknown')}
        """

    # Extract plot information
    plot_summary = book_analysis.get("plot", {}).get("summary", "A story with characters and challenges.")
    central_conflict = book_analysis.get("plot", {}).get("central_conflict", "A conflict that drives the narrative")
    key_points = book_analysis.get("plot", {}).get("key_points", [])
    branching_points = book_analysis.get("plot", {}).get("branching_points", [])

    # Create branching point information
    branching_info = ""
    for i, bp in enumerate(branching_points[:5]):  # Limit to 5 branching points
        options = ", ".join([f'"{opt}"' for opt in bp.get("options", [])])
        branching_info += f"""
        - Choice point {i+1}: {bp.get('description', 'A decision')}
          * Options: {options}
        """

    # Create a prompt that emphasizes slower story development and rich detail
    prompt = f"""
    Create a detailed outline for an interactive visual novel adaptation of this book:

    TITLE: {book_analysis['metadata'].get('title', 'Untitled')}

    KEY CHARACTERS:
    {character_info}

    PLOT SUMMARY:
    {plot_summary}

    CENTRAL CONFLICT:
    {central_conflict}

    KEY PLOT POINTS:
    {', '.join(key_points[:8])}

    POTENTIAL BRANCHING POINTS:
    {branching_info}

    THEMES: {', '.join(book_analysis.get('themes', ['adventure'])[:3])}
    TONE: {book_analysis.get('tone', 'neutral')}

    CRITICAL REQUIREMENTS FOR VISUAL NOVEL ADAPTATION:
    1. PACING: Create a SLOW, DELIBERATE story progression that allows readers to fully immerse
    2. SCENE DEVELOPMENT: Each scene should thoroughly establish setting, mood, and character emotions
    3. FORESHADOWING: Important plot elements (like the snake in Sherlock Holmes) must be properly established before appearing
    4. NARRATIVE DEPTH: Every scene should include 6-10 dialogue exchanges to develop characters and plot
    5. BRANCHING STRUCTURE: Create meaningful choice points with consequences

    FORMAT REQUIREMENTS:
    Create exactly {scene_limit} scenes that follow a clear narrative path with branching options.

    For each scene, provide:
    1. Scene ID (e.g., "scene_1", "scene_forest")
    2. Detailed description of what happens (4-5 sentences)
    3. Characters present
    4. Setting and atmosphere
    5. How it connects to other scenes

    Output in this JSON format:
    {{
      "scenes": [
        {{
          "id": "scene_id",
          "description": "Detailed description of what happens in this scene",
          "characters": ["char1", "char2"],
          "setting": "Detailed setting description",
          "atmosphere": "Mood and tone of the scene",
          "dialogue_count": 6-10,
          "connects_to": ["scene_id_1", "scene_id_2"]
        }},
        ...
      ]
    }}
    """

    try:
        # Configure Gemini model
        generation_config = {
            "temperature": 0.7,
            "top_p": 0.95,
            "top_k": 40,
            "max_output_tokens": 2500,
        }

        log_gemini(f"Configuring Gemini model with parameters: {generation_config}")

        # Try to use Gemini 1.5 Pro first, then fall back to Gemini Pro if needed
        model_name = "gemini-1.5-pro"
        try:
            log_gemini(f"Initializing Gemini model: {model_name}")
            model = genai.GenerativeModel(
                model_name=model_name,
                generation_config=generation_config
            )
        except Exception as e:
            log_gemini(f"Error initializing {model_name}: {str(e)}", "WARNING")
            model_name = "gemini-pro"
            log_gemini(f"Falling back to {model_name}", "WARNING")
            model = genai.GenerativeModel(
                model_name=model_name,
                generation_config=generation_config
            )

        # Generate the outline
        log_gemini(f"Generating script outline with {model_name}...")
        start_time = time.time()

        response = await asyncio.to_thread(
            model.generate_content,
            prompt
        )

        # Log generation time and response info
        generation_time = time.time() - start_time
        log_gemini(f"Generation completed in {generation_time:.2f} seconds")

        # Check if we have a valid response
        if not hasattr(response, 'text'):
            log_gemini("No text in response from Gemini", "ERROR")
            raise ValueError("Empty response from Gemini")

        # Parse the response
        outline_text = response.text
        log_gemini(f"Received response of {len(outline_text)} characters")

        # Extract JSON from the response (Gemini might wrap it in markdown code blocks)
        json_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', outline_text)
        if json_match:
            log_gemini("Found JSON code block in response")
            outline_text = json_match.group(1)
        else:
            log_gemini("No JSON code block found, using raw response", "WARNING")

        try:
            outline_data = json.loads(outline_text)
            scene_count = len(outline_data.get('scenes', []))
            log_gemini(f"Successfully parsed JSON with {scene_count} planned scenes")
            return outline_data
        except json.JSONDecodeError as e:
            # Try to fix common JSON issues
            log_gemini(f"JSON parsing error: {str(e)}", "WARNING")
            log_gemini("Attempting to repair JSON...")
            fixed_text = attempt_json_repair(outline_text)
            outline_data = json.loads(fixed_text)
            log_gemini("Successfully repaired and parsed JSON", "INFO")
            return outline_data

    except Exception as e:
        log_gemini(f"Error generating script outline with Gemini: {str(e)}", "ERROR")
        log_gemini("Returning fallback outline", "WARNING")
        # Return a basic outline if failed
        return {
            "scenes": [
                {
                    "id": "scene_1",
                    "description": "Introduction to the story and main characters",
                    "characters": ["protagonist"],
                    "setting": "The main setting of the story",
                    "atmosphere": "Establishes the tone of the narrative",
                    "dialogue_count": 8,
                    "connects_to": ["scene_2a", "scene_2b"]
                },
                {
                    "id": "scene_2a",
                    "description": "The protagonist takes an active approach",
                    "characters": ["protagonist", "supporting"],
                    "setting": "A location that presents challenges",
                    "atmosphere": "Tense and action-oriented",
                    "dialogue_count": 7,
                    "connects_to": ["scene_3"]
                }
            ]
        }

async def generate_script_outline(book_analysis: dict, client, scene_limit=10) -> dict:
    print("\n📝 USING OPENAI MODEL FOR SCRIPT OUTLINE GENERATION 📝")
    """Generate an outline for the script with planned scenes"""

    # Extract key elements from the book analysis
    characters = book_analysis.get("characters", [])
    character_info = ""

    # Create detailed character information for more authentic portrayal
    for char in characters[:7]:  # Limit to 7 important characters
        personality = char.get("personality", "")
        speech = char.get("speech_patterns", "")
        motivations = char.get("motivations", "")

        character_info += f"""
        - {char.get('name', 'Unknown')}: {char.get('role', 'A character')}
          * Description: {char.get('description', 'No description')}
          * Personality: {personality}
          * Speech patterns: {speech}
          * Motivations: {motivations}
          * Relationships: {char.get('relationships', 'Unknown')}
        """

    # Extract plot information
    plot_summary = book_analysis.get("plot", {}).get("summary", "A story with characters and challenges.")
    central_conflict = book_analysis.get("plot", {}).get("central_conflict", "A conflict that drives the narrative")
    key_points = book_analysis.get("plot", {}).get("key_points", [])
    branching_points = book_analysis.get("plot", {}).get("branching_points", [])

    # Create branching point information
    branching_info = ""
    for i, bp in enumerate(branching_points[:5]):  # Limit to 5 branching points
        options = ", ".join([f'"{opt}"' for opt in bp.get("options", [])])
        branching_info += f"""
        - Choice point {i+1}: {bp.get('description', 'A decision')}
          * Options: {options}
        """

    # Create a prompt that emphasizes slower story development and rich detail
    prompt = f"""
    Create a detailed outline for an interactive visual novel adaptation of this book:

    TITLE: {book_analysis['metadata'].get('title', 'Untitled')}

    KEY CHARACTERS:
    {character_info}

    PLOT SUMMARY:
    {plot_summary}

    CENTRAL CONFLICT:
    {central_conflict}

    KEY PLOT POINTS:
    {', '.join(key_points[:8])}

    POTENTIAL BRANCHING POINTS:
    {branching_info}

    THEMES: {', '.join(book_analysis.get('themes', ['adventure'])[:3])}
    TONE: {book_analysis.get('tone', 'neutral')}

    CRITICAL REQUIREMENTS FOR VISUAL NOVEL ADAPTATION:
    1. PACING: Create a SLOW, DELIBERATE story progression that allows readers to fully immerse
    2. SCENE DEVELOPMENT: Each scene should thoroughly establish setting, mood, and character emotions
    3. FORESHADOWING: Important plot elements (like the snake in Sherlock Holmes) must be properly established before appearing
    4. NARRATIVE DEPTH: Every scene should include 6-10 dialogue exchanges to develop characters and plot
    5. BRANCHING STRUCTURE: Create meaningful choice points with consequences

    FORMAT REQUIREMENTS:
    Create exactly {scene_limit} scenes that follow a clear narrative path with branching options.

    For each scene, provide:
    1. Scene ID (e.g., "scene_1", "scene_forest")
    2. Detailed description of what happens (4-5 sentences)
    3. Characters present
    4. Setting and atmosphere
    5. How it connects to other scenes

    Output in this JSON format:
    {{
      "scenes": [
        {{
          "id": "scene_id",
          "description": "Detailed description of what happens in this scene",
          "characters": ["char1", "char2"],
          "setting": "Detailed setting description",
          "atmosphere": "Mood and tone of the scene",
          "dialogue_count": 6-10,
          "connects_to": ["scene_id_1", "scene_id_2"]
        }},
        ...
      ]
    }}
    """

    try:
        # Generate the outline
        response = await client.chat.completions.create(
            model="gpt-4-turbo",
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": "You are an expert narrative designer specializing in adapting literary works into interactive visual novels."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=2500
        )

        # Parse the response
        outline_text = response.choices[0].message.content
        outline_data = json.loads(outline_text)

        print(f"Generated script outline with {len(outline_data.get('scenes', []))} planned scenes")
        return outline_data

    except Exception as e:
        print(f"Error generating script outline: {str(e)}")
        # Return a basic outline if failed
        return {
            "scenes": [
                {
                    "id": "scene_1",
                    "description": "Introduction to the story and main characters",
                    "characters": ["protagonist"],
                    "setting": "The main setting of the story",
                    "atmosphere": "Establishes the tone of the narrative",
                    "dialogue_count": 8,
                    "connects_to": ["scene_2a", "scene_2b"]
                },
                {
                    "id": "scene_2a",
                    "description": "The protagonist takes an active approach",
                    "characters": ["protagonist", "supporting"],
                    "setting": "A location that presents challenges",
                    "atmosphere": "Tense and action-oriented",
                    "dialogue_count": 7,
                    "connects_to": ["scene_3"]
                }
            ]
        }

async def generate_scene_from_outline_with_gemini(scene_outline, book_analysis):
    print(f"\n🔍 USING GEMINI MODEL FOR SCENE GENERATION: {scene_outline.get('id', 'unknown')} 🔍")
    """Generate a full scene from its outline description using Gemini"""
    scene_id = scene_outline.get("id", f"scene_{random.randint(1000, 9999)}")

    # Prevent duplicate generation
    if scene_id in STORY_CACHE["generated_scenes"]:
        print(f"Scene {scene_id} already exists in cache, using cached version")
        return STORY_CACHE["generated_scenes"][scene_id]

    # Check if already being generated
    if scene_id in STORY_CACHE["in_progress_scenes"]:
        print(f"Scene {scene_id} is already being generated, waiting...")
        # Wait for it to appear in cache (with timeout)
        for _ in range(30):  # 30 second timeout
            await asyncio.sleep(1)
            if scene_id in STORY_CACHE["generated_scenes"]:
                return STORY_CACHE["generated_scenes"][scene_id]

        print(f"Timed out waiting for scene {scene_id}, will generate now")

    # Mark as in progress
    STORY_CACHE["in_progress_scenes"].add(scene_id)

    try:
        # Get detailed information about characters in this scene
        characters = []
        for char_id in scene_outline.get("characters", []):
            # Find the character in book analysis
            char_data = next((c for c in book_analysis.get("characters", []) if c.get("id") == char_id or c.get("name").lower() == char_id.lower()), None)
            if char_data:
                characters.append(char_data)

        # Create character information for the prompt
        character_info = ""
        for char in characters:
            personality = char.get("personality", "")
            speech = char.get("speech_patterns", "")
            character_info += f"""
            - {char.get('name', 'Unknown')}:
              * Role: {char.get('role', 'A character in the story')}
              * Description: {char.get('description', 'No description')}
              * Personality: {personality}
              * Speech patterns: {speech}
              * Motivations: {char.get('motivations', 'Unknown')}
            """

        # If no characters were found, add a note
        if not character_info:
            character_info = "No specific characters identified for this scene."

        # Get connections to other scenes
        connections = scene_outline.get("connects_to", [])
        connections_info = ", ".join(connections) if connections else "None specified"

        # Calculate desired dialogue count (6-10 lines by default)
        dialogue_count = scene_outline.get("dialogue_count", random.randint(6, 10))

        # Create a prompt for generating this specific scene
        prompt = f"""
        Generate a detailed scene for a visual novel with rich dialogue and atmosphere.

        SCENE INFORMATION:
        - ID: {scene_id}
        - Description: {scene_outline.get('description', 'A scene in the story')}
        - Setting: {scene_outline.get('setting', 'An important location')}
        - Atmosphere: {scene_outline.get('atmosphere', 'Creates a specific mood')}

        CHARACTERS PRESENT:
        {character_info}

        CONNECTIONS:
        This scene should connect to these scenes: {connections_info}

        IMPORTANT REQUIREMENTS:
        1. CREATE EXACTLY {dialogue_count} DIALOGUE EXCHANGES (not just lines) for a slow, immersive pace
        2. WRITE RICH, ENGAGING TEXT with detailed descriptions and natural dialogue
        3. MAINTAIN CHARACTER VOICE - each character should speak in their distinctive pattern
        4. INCLUDE DESCRIPTIVE NARRATION between dialogue to establish mood and setting
        5. CREATE MEANINGFUL CHOICES that connect to the specified scenes
        6. IF A CRITICAL PLOT ELEMENT (like a weapon, creature, or revelation) appears, PROPERLY FORESHADOW it

        FORMAT:
        Return a JSON object for this single scene following this exact structure:
        {{
          "id": "{scene_id}",
          "background": "Detailed description of the setting and visuals",
          "characters": [
            {{ "id": "character_id", "image": "Detailed character appearance" }}
          ],
          "dialogue": [
            {{
              "speaker": "Character Name",
              "text": "Rich, detailed dialogue that feels natural and reflects character's voice",
              "character": "character_id" (optional)
            }},
            {{
              "speaker": "Narrator",
              "text": "Descriptive narration that establishes mood, setting, and character emotions"
            }},
            ... ({dialogue_count} total dialogue entries)
            {{
              "speaker": "Character Name",
              "text": "Final choice prompt with depth and consequence",
              "character": "character_id" (optional),
              "choices": [
                {{ "text": "Meaningful choice with clear implication", "nextScene": "target_scene_id" }}
              ]
            }}
          ]
        }}

        FOCUS ON QUALITY: Create dialogue that is engaging, natural, and reflects the character's voice.
        """

        # Configure Gemini model
        generation_config = {
            "temperature": 0.8,
            "top_p": 0.95,
            "top_k": 40,
            "max_output_tokens": 4000,
        }

        # Get the Gemini model
        model = genai.GenerativeModel(
            model_name="gemini-1.5-pro",
            generation_config=generation_config
        )

        # Generate the scene
        response = await asyncio.to_thread(
            model.generate_content,
            prompt
        )

        # Parse the response
        scene_text = response.text

        # Extract JSON from the response (Gemini might wrap it in markdown code blocks)
        json_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', scene_text)
        if json_match:
            scene_text = json_match.group(1)

        try:
            scene_data = json.loads(scene_text)
            print(f"Successfully generated scene {scene_id} with {len(scene_data.get('dialogue', []))} dialogue lines using Gemini")

            # Save to cache
            STORY_CACHE["generated_scenes"][scene_id] = scene_data

            # Remove from in-progress set
            STORY_CACHE["in_progress_scenes"].remove(scene_id)

            return scene_data

        except json.JSONDecodeError as e:
            print(f"Error parsing scene JSON from Gemini: {e}")

            # Try to fix the JSON
            try:
                fixed_text = attempt_json_repair(scene_text)
                if fixed_text != scene_text:
                    scene_data = json.loads(fixed_text)
                    print(f"Fixed JSON for scene {scene_id} from Gemini")

                    # Save to cache
                    STORY_CACHE["generated_scenes"][scene_id] = scene_data

                    # Remove from in-progress set
                    STORY_CACHE["in_progress_scenes"].remove(scene_id)

                    return scene_data
            except:
                print(f"Failed to fix JSON for scene {scene_id} from Gemini")

            # Create a placeholder scene as fallback
            placeholder_scene = create_placeholder_scene(scene_id, scene_outline, book_analysis)

            # Save to cache
            STORY_CACHE["generated_scenes"][scene_id] = placeholder_scene

            # Remove from in-progress set
            STORY_CACHE["in_progress_scenes"].remove(scene_id)

            return placeholder_scene

    except Exception as e:
        print(f"Error generating scene {scene_id} with Gemini: {str(e)}")

        # Create a placeholder scene
        placeholder_scene = create_placeholder_scene(scene_id, scene_outline, book_analysis)

        # Save to cache
        STORY_CACHE["generated_scenes"][scene_id] = placeholder_scene

        # Remove from in-progress set
        if scene_id in STORY_CACHE["in_progress_scenes"]:
            STORY_CACHE["in_progress_scenes"].remove(scene_id)

        return placeholder_scene

async def generate_scene_from_outline(scene_outline, book_analysis, client):
    print(f"\n📝 USING OPENAI MODEL FOR SCENE GENERATION: {scene_outline.get('id', 'unknown')} 📝")
    """Generate a full scene from its outline description"""
    scene_id = scene_outline.get("id", f"scene_{random.randint(1000, 9999)}")

    # Prevent duplicate generation
    if scene_id in STORY_CACHE["generated_scenes"]:
        print(f"Scene {scene_id} already exists in cache, using cached version")
        return STORY_CACHE["generated_scenes"][scene_id]

    # Check if already being generated
    if scene_id in STORY_CACHE["in_progress_scenes"]:
        print(f"Scene {scene_id} is already being generated, waiting...")
        # Wait for it to appear in cache (with timeout)
        for _ in range(30):  # 30 second timeout
            await asyncio.sleep(1)
            if scene_id in STORY_CACHE["generated_scenes"]:
                return STORY_CACHE["generated_scenes"][scene_id]

        print(f"Timed out waiting for scene {scene_id}, will generate now")

    # Mark as in progress
    STORY_CACHE["in_progress_scenes"].add(scene_id)

    try:
        # Get detailed information about characters in this scene
        characters = []
        for char_id in scene_outline.get("characters", []):
            # Find the character in book analysis
            char_data = next((c for c in book_analysis.get("characters", []) if c.get("id") == char_id or c.get("name").lower() == char_id.lower()), None)
            if char_data:
                characters.append(char_data)

        # Create character information for the prompt
        character_info = ""
        for char in characters:
            personality = char.get("personality", "")
            speech = char.get("speech_patterns", "")
            character_info += f"""
            - {char.get('name', 'Unknown')}:
              * Role: {char.get('role', 'A character in the story')}
              * Description: {char.get('description', 'No description')}
              * Personality: {personality}
              * Speech patterns: {speech}
              * Motivations: {char.get('motivations', 'Unknown')}
            """

        # If no characters were found, add a note
        if not character_info:
            character_info = "No specific characters identified for this scene."

        # Get connections to other scenes
        connections = scene_outline.get("connects_to", [])
        connections_info = ", ".join(connections) if connections else "None specified"

        # Calculate desired dialogue count (6-10 lines by default)
        dialogue_count = scene_outline.get("dialogue_count", random.randint(6, 10))

        # Create a prompt for generating this specific scene
        prompt = f"""
        Generate a detailed scene for a visual novel with rich dialogue and atmosphere.

        SCENE INFORMATION:
        - ID: {scene_id}
        - Description: {scene_outline.get('description', 'A scene in the story')}
        - Setting: {scene_outline.get('setting', 'An important location')}
        - Atmosphere: {scene_outline.get('atmosphere', 'Creates a specific mood')}

        CHARACTERS PRESENT:
        {character_info}

        CONNECTIONS:
        This scene should connect to these scenes: {connections_info}

        IMPORTANT REQUIREMENTS:
        1. CREATE EXACTLY {dialogue_count} DIALOGUE EXCHANGES (not just lines) for a slow, immersive pace
        2. WRITE RICH, ENGAGING TEXT with detailed descriptions and natural dialogue
        3. MAINTAIN CHARACTER VOICE - each character should speak in their distinctive pattern
        4. INCLUDE DESCRIPTIVE NARRATION between dialogue to establish mood and setting
        5. CREATE MEANINGFUL CHOICES that connect to the specified scenes
        6. IF A CRITICAL PLOT ELEMENT (like a weapon, creature, or revelation) appears, PROPERLY FORESHADOW it

        FORMAT:
        Return a JSON object for this single scene following this exact structure:
        {{
          "id": "{scene_id}",
          "background": "Detailed description of the setting and visuals",
          "characters": [
            {{ "id": "character_id", "image": "Detailed character appearance" }}
          ],
          "dialogue": [
            {{
              "speaker": "Character Name",
              "text": "Rich, detailed dialogue that feels natural and reflects character's voice",
              "character": "character_id" (optional)
            }},
            {{
              "speaker": "Narrator",
              "text": "Descriptive narration that establishes mood, setting, and character emotions"
            }},
            ... ({dialogue_count} total dialogue entries)
            {{
              "speaker": "Character Name",
              "text": "Final choice prompt with depth and consequence",
              "character": "character_id" (optional),
              "choices": [
                {{ "text": "Meaningful choice with clear implication", "nextScene": "target_scene_id" }}
              ]
            }}
          ]
        }}

        FOCUS ON QUALITY: Create dialogue that is engaging, natural, and reflects the character's voice.
        """

        # Generate the scene
        response = await client.chat.completions.create(
            model="gpt-4-turbo",  # Using the most capable model for creative content
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": "You are a master writer of interactive fiction, specializing in creating immersive, literary-quality scenes with authentic dialogue."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.8,  # Higher temperature for more creative, varied output
            max_tokens=3500,  # Increased limit for richer content
            timeout=90  # Extended timeout
        )

        # Parse the response
        scene_text = response.choices[0].message.content

        try:
            scene_data = json.loads(scene_text)
            print(f"Successfully generated scene {scene_id} with {len(scene_data.get('dialogue', []))} dialogue lines")

            # Save to cache
            STORY_CACHE["generated_scenes"][scene_id] = scene_data

            # Remove from in-progress set
            STORY_CACHE["in_progress_scenes"].remove(scene_id)

            return scene_data

        except json.JSONDecodeError as e:
            print(f"Error parsing scene JSON: {e}")

            # Try to fix the JSON
            try:
                fixed_text = attempt_json_repair(scene_text)
                if fixed_text != scene_text:
                    scene_data = json.loads(fixed_text)
                    print(f"Fixed JSON for scene {scene_id}")

                    # Save to cache
                    STORY_CACHE["generated_scenes"][scene_id] = scene_data

                    # Remove from in-progress set
                    STORY_CACHE["in_progress_scenes"].remove(scene_id)

                    return scene_data
            except:
                print(f"Failed to fix JSON for scene {scene_id}")

            # Create a placeholder scene as fallback
            placeholder_scene = create_placeholder_scene(scene_id, scene_outline, book_analysis)

            # Save to cache
            STORY_CACHE["generated_scenes"][scene_id] = placeholder_scene

            # Remove from in-progress set
            STORY_CACHE["in_progress_scenes"].remove(scene_id)

            return placeholder_scene

    except Exception as e:
        print(f"Error generating scene {scene_id}: {str(e)}")

        # Create a placeholder scene
        placeholder_scene = create_placeholder_scene(scene_id, scene_outline, book_analysis)

        # Save to cache
        STORY_CACHE["generated_scenes"][scene_id] = placeholder_scene

        # Remove from in-progress set
        if scene_id in STORY_CACHE["in_progress_scenes"]:
            STORY_CACHE["in_progress_scenes"].remove(scene_id)

        return placeholder_scene

def create_placeholder_scene(scene_id, scene_outline, book_analysis):
    """Create a placeholder scene when generation fails"""
    # Find characters for this scene
    characters = []
    for char_id in scene_outline.get("characters", []):
        # Look up in book analysis
        char = next((c for c in book_analysis.get("characters", []) if c.get("id") == char_id or c.get("name").lower() == char_id.lower()), None)
        if char:
            characters.append({
                "id": char.get("id", char_id),
                "image": f"A character representing {char.get('name', 'a person')}"
            })

    # If no characters were found, add a generic one
    if not characters:
        characters.append({
            "id": "character",
            "image": "A person relevant to this scene"
        })

    # Create dialogue based on the scene description
    description = scene_outline.get("description", "A scene in the story")
    setting = scene_outline.get("setting", "An important location")
    atmosphere = scene_outline.get("atmosphere", "Creates a specific mood")

    # Create dialogue array
    dialogue = [
        {
            "speaker": "Narrator",
            "text": f"{setting}. {atmosphere}."
        },
        {
            "speaker": "Narrator",
            "text": description
        }
    ]

    # Add character dialogue if characters exist
    if characters:
        char = characters[0]
        dialogue.append({
            "speaker": char.get("id", "Character"),
            "text": "We need to proceed carefully in this situation.",
            "character": char.get("id")
        })

    # Add choices based on connections
    connections = scene_outline.get("connects_to", [])
    choices = []

    for conn in connections:
        choices.append({
            "text": f"Continue to {conn}",
            "nextScene": conn
        })

    # If no connections, add a generic choice
    if not choices:
        choices.append({
            "text": "Continue",
            "nextScene": "scene_next"
        })

    # Add the choices to dialogue
    dialogue.append({
        "speaker": "Narrator",
        "text": "What will you do next?",
        "choices": choices
    })

    # Create the placeholder scene
    placeholder_scene = {
        "id": scene_id,
        "background": setting,
        "characters": characters,
        "dialogue": dialogue
    }

    return placeholder_scene

async def enhance_visual_novel(script_data, characters):
    """Add visual elements to the script using AI-generated images"""
    print("Enhancing visual novel with AI-generated images...")

    # Check if Replicate API token is available
    replicate_api_token = os.environ.get("REPLICATE_API_TOKEN")
    use_ai_images = replicate_api_token is not None

    if use_ai_images:
        print("Using Replicate for AI image generation")
    else:
        print("REPLICATE_API_TOKEN not found, using SVG placeholders instead")

    # Generate backgrounds for different settings if needed
    await generate_backgrounds(script_data, use_ai_images)

    # Generate character images
    await generate_character_images(script_data, characters, use_ai_images)

    print("Visual enhancement complete")
    return script_data

async def generate_backgrounds(script_data, use_ai_images):
    """Generate background images for scenes"""
    # Default SVG backgrounds for fallback
    default_backgrounds = {
        "main": "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 800 600'><rect width='800' height='600' fill='%23243b55'/><path d='M0 450 Q 400 400 800 450 L 800 600 L 0 600 Z' fill='%23141e30'/></svg>",
        "secondary": "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 800 600'><rect width='800' height='600' fill='%232c3e50'/><path d='M0 450 Q 400 400 800 450 L 800 600 L 0 600 Z' fill='%23141e30'/></svg>",
        "dark": "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 800 600'><rect width='800' height='600' fill='%231a1a2e'/><path d='M0 450 Q 400 400 800 450 L 800 600 L 0 600 Z' fill='%230f0f1a'/></svg>",
        "light": "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 800 600'><rect width='800' height='600' fill='%23e0e0e0'/><path d='M0 450 Q 400 400 800 450 L 800 600 L 0 600 Z' fill='%23c0c0c0'/></svg>",
        "forest": "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 800 600'><rect width='800' height='600' fill='%23234010'/><path d='M0 450 Q 400 400 800 450 L 800 600 L 0 600 Z' fill='%23132010'/></svg>"
    }

    scene_types = list(default_backgrounds.keys())
    unique_backgrounds = set()

    # First pass: collect all unique background descriptions
    for scene in script_data["scenes"]:
        # Extract background description if available, otherwise use scene ID
        background_desc = scene.get("background", "")
        if isinstance(background_desc, str) and not background_desc.startswith("data:"):
            unique_backgrounds.add(background_desc)

    # Generate AI backgrounds for unique descriptions
    if use_ai_images and unique_backgrounds:
        print(f"Generating {len(unique_backgrounds)} unique AI backgrounds")

        for background_desc in unique_backgrounds:
            # Skip if already in cache
            if background_desc in IMAGE_CACHE["backgrounds"]:
                continue

            # Skip if it's an empty or very short description
            if len(background_desc) < 10:
                continue

            try:
                # Generate an AI image for this background
                prompt = f"A detailed atmospheric scene: {background_desc}. Suitable as a visual novel background, high quality, detailed."

                # Try to get from cache first
                if background_desc in IMAGE_CACHE["backgrounds"]:
                    print(f"Using cached background for: {background_desc[:30]}...")
                else:
                    print(f"Generating background for: {background_desc[:30]}...")
                    image_url = await generate_image_with_replicate(prompt)
                    if image_url:
                        # Convert to data URI for embedding
                        data_uri = await url_to_data_uri(image_url)
                        IMAGE_CACHE["backgrounds"][background_desc] = data_uri
            except Exception as e:
                print(f"Error generating background image: {str(e)}")

    # Second pass: assign backgrounds to scenes
    for i, scene in enumerate(script_data["scenes"]):
        background_desc = scene.get("background", "")

        # If it's already a data URI, keep it
        if isinstance(background_desc, str) and background_desc.startswith("data:"):
            continue

        # Use AI-generated background if available
        if use_ai_images and background_desc in IMAGE_CACHE["backgrounds"]:
            scene["background"] = IMAGE_CACHE["backgrounds"][background_desc]
        else:
            # Fall back to SVG placeholder
            scene_type = scene_types[i % len(scene_types)]
            scene["background"] = default_backgrounds[scene_type]

async def generate_character_images(script_data, characters, use_ai_images):
    """Generate character images based on descriptions"""
    # Default SVG character template
    colors = ["f9d5e5", "b06ab3", "6a0572", "d1d1e0", "800000", "333333", "e6ccb2", "7b7554", "c0d6df", "4a6fa5"]

    # Create a map of character IDs to descriptions
    character_descriptions = {}
    for char in characters:
        char_id = char.get("id", "")
        # Combine name, description, and any physical attributes for better image generation
        description = f"{char.get('name', 'Character')}: {char.get('description', '')}"
        if 'personality' in char:
            description += f". Personality: {char['personality']}"
        character_descriptions[char_id] = description

    # Get unique character IDs from all scenes
    unique_characters = set()
    for scene in script_data["scenes"]:
        for char in scene.get("characters", []):
            unique_characters.add(char.get("id", ""))

    # Generate AI character images
    if use_ai_images:
        print(f"Generating {len(unique_characters)} unique AI character images")

        for char_id in unique_characters:
            # Skip if already in cache
            if char_id in IMAGE_CACHE["characters"]:
                continue

            # Get description from the character info
            description = character_descriptions.get(char_id, f"Character {char_id}")

            try:
                # Try to get from cache first
                if char_id in IMAGE_CACHE["characters"]:
                    print(f"Using cached character image for {char_id}")
                else:
                    print(f"Generating character image for {char_id}: {description[:30]}...")
                    # Enhance prompt for better character images
                    prompt = f"Portrait of {description}. Full-body portrait, high-quality, detailed, visual novel style, well-lit, clear features, expressive pose."

                    image_url = await generate_image_with_replicate(prompt)
                    if image_url:
                        # Convert to data URI for embedding
                        data_uri = await url_to_data_uri(image_url)
                        IMAGE_CACHE["characters"][char_id] = data_uri
            except Exception as e:
                print(f"Error generating character image: {str(e)}")

    # Now update all character references in all scenes
    for scene in script_data["scenes"]:
        for char in scene.get("characters", []):
            char_id = char.get("id", "")

            # If we have an AI-generated image, use it
            if use_ai_images and char_id in IMAGE_CACHE["characters"]:
                char["image"] = IMAGE_CACHE["characters"][char_id]
            else:
                # Fall back to SVG placeholder
                color_idx = hash(char_id) % len(colors)
                char["image"] = f"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 250'><rect x='35' y='20' width='30' height='30' rx='15' fill='%23{colors[color_idx]}'/><rect x='30' y='50' width='40' height='60' fill='%23{colors[(color_idx+1) % len(colors)]}'/><rect x='25' y='110' width='50' height='50' fill='%23{colors[(color_idx+2) % len(colors)]}'/><rect x='25' y='110' width='20' height='70' rx='5' fill='%23{colors[(color_idx+2) % len(colors)]}'/><rect x='55' y='110' width='20' height='70' rx='5' fill='%23{colors[(color_idx+2) % len(colors)]}'/></svg>"

async def generate_image_with_replicate(prompt):
    """Generate an image using Replicate API"""
    try:
        # Run the SDXL Lightning model with the provided prompt
        output = replicate.run(
            "bytedance/sdxl-lightning-4step:5599ed30703defd1d160a25a63321b4dec97101d98b4674bcc56e41f62f35637",
            input={
                "width": 1024,
                "height": 1024,
                "prompt": prompt,
                "scheduler": "K_EULER",
                "num_outputs": 1,
                "guidance_scale": 0,
                "negative_prompt": "worst quality, low quality, blurry, distorted features",
                "num_inference_steps": 4
            }
        )

        # The output is a list of URLs
        if output and len(output) > 0:
            return output[0]  # Return the first image URL

        return None
    except Exception as e:
        print(f"Error generating image with Replicate: {str(e)}")
        return None

async def url_to_data_uri(url):
    """Convert an image URL to a data URI for embedding in HTML"""
    try:
        response = requests.get(url)
        if response.status_code == 200:
            # Get the image and convert it to base64
            image_content = response.content
            image = Image.open(BytesIO(image_content))

            # Resize for reasonable file size
            max_size = (800, 800)
            image.thumbnail(max_size)

            # Convert to JPEG with reasonable quality
            buffer = BytesIO()
            image.save(buffer, format="JPEG", quality=85)
            base64_image = base64.b64encode(buffer.getvalue()).decode('utf-8')

            # Create the data URI
            return f"data:image/jpeg;base64,{base64_image}"
        else:
            print(f"Failed to download image: {response.status_code}")
            return None
    except Exception as e:
        print(f"Error converting image to data URI: {str(e)}")
        return None

def validate_and_fix_scene_connections(script_data):
    """
    Validate and fix scene connections to ensure all nextScene references
    point to valid scenes and all scenes are reachable
    """
    print("Validating scene connections...")

    # Get all scene IDs
    scene_ids = set(scene["id"] for scene in script_data["scenes"])
    print(f"Found {len(scene_ids)} scenes: {', '.join(scene_ids)}")

    # Find all nextScene references in choices
    next_scene_refs = []

    for scene in script_data["scenes"]:
        for dialogue in scene["dialogue"]:
            if "choices" in dialogue:
                for choice in dialogue["choices"]:
                    if "nextScene" in choice:
                        next_scene_refs.append({
                            "source_scene": scene["id"],
                            "choice_text": choice["text"],
                            "next_scene": choice["nextScene"]
                        })

    print(f"Found {len(next_scene_refs)} nextScene references")

    # Check for invalid references
    invalid_refs = [ref for ref in next_scene_refs if ref["next_scene"] not in scene_ids and ref["next_scene"] != "exit"]
    print(f"Found {len(invalid_refs)} invalid nextScene references")

    # Fix invalid references
    for ref in invalid_refs:
        print(f"Invalid reference from {ref['source_scene']} -> {ref['next_scene']} in choice '{ref['choice_text']}'")

        # Try to find a similar scene ID
        similar_ids = [sid for sid in scene_ids if ref["next_scene"] in sid or sid in ref["next_scene"]]

        # Update the reference in the script
        for scene in script_data["scenes"]:
            if scene["id"] == ref["source_scene"]:
                for dialogue in scene["dialogue"]:
                    if "choices" in dialogue:
                        for choice in dialogue["choices"]:
                            if choice.get("text") == ref["choice_text"] and choice.get("nextScene") == ref["next_scene"]:
                                if similar_ids:
                                    choice["nextScene"] = similar_ids[0]
                                    print(f"  Fixed by changing to {similar_ids[0]}")
                                else:
                                    # Default to the first scene as fallback
                                    choice["nextScene"] = list(scene_ids)[0]
                                    print(f"  Fixed by changing to {list(scene_ids)[0]} (fallback)")

    # Check for unreachable scenes
    reachable = set(["scene_intro", "scene_1"])  # Assuming scene_intro or scene_1 is the starting point

    # If neither scene_intro nor scene_1 exists, use the first scene
    if not any(id in scene_ids for id in reachable):
        reachable = set([next(iter(scene_ids))])

    # Keep expanding the set of reachable scenes until no new scenes are added
    old_size = 0
    while len(reachable) > old_size:
        old_size = len(reachable)

        for ref in next_scene_refs:
            if ref["source_scene"] in reachable and ref["next_scene"] != "exit":
                reachable.add(ref["next_scene"])

    unreachable = scene_ids - reachable

    if unreachable:
        print(f"Warning: Found {len(unreachable)} unreachable scenes: {', '.join(unreachable)}")
        # Add connections to unreachable scenes
        for scene_id in unreachable:
            # Add a way to reach this scene from a random reachable scene
            source_scene_id = random.choice(list(reachable))
            print(f"  Adding connection from {source_scene_id} to unreachable scene {scene_id}")

            # Find the source scene
            for scene in script_data["scenes"]:
                if scene["id"] == source_scene_id:
                    # Add a new choice to the last dialogue if it has choices
                    for dialogue in reversed(scene["dialogue"]):
                        if "choices" in dialogue:
                            dialogue["choices"].append({
                                "text": f"Explore a different path",
                                "nextScene": scene_id
                            })
                            break

    return script_data

# Helper function to attempt to repair broken JSON
def attempt_json_repair(json_text):
    """Attempt to fix common JSON errors"""
    # Try to fix unclosed quotes
    json_text = re.sub(r'([^\\])"([^"]*)$', r'\1"\2"', json_text)

    # Try to fix missing closing braces
    open_braces = json_text.count('{')
    close_braces = json_text.count('}')
    if open_braces > close_braces:
        json_text += '}' * (open_braces - close_braces)

    # Try to fix missing closing brackets
    open_brackets = json_text.count('[')
    close_brackets = json_text.count(']')
    if open_brackets > close_brackets:
        json_text += ']' * (open_brackets - close_brackets)

    return json_text

# Placeholder script generator for when AI fails
async def generate_placeholder_script(book_analysis: dict) -> dict:
    """Fallback script generator"""
    print("Using placeholder script generator as fallback")

    # Create placeholder for visual novel script
    title = book_analysis["metadata"].get("title", "Adventure")
    vn_script = {
        "title": f"{title}: An Interactive Adventure",
        "scenes": []
    }

    # Generate SVG backgrounds for different settings
    backgrounds = {
        "main": "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 800 600'><rect width='800' height='600' fill='%23243b55'/><path d='M0 450 Q 400 400 800 450 L 800 600 L 0 600 Z' fill='%23141e30'/></svg>",
        "secondary": "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 800 600'><rect width='800' height='600' fill='%232c3e50'/><path d='M0 450 Q 400 400 800 450 L 800 600 L 0 600 Z' fill='%23141e30'/></svg>",
        "dark": "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 800 600'><rect width='800' height='600' fill='%231a1a2e'/><path d='M0 450 Q 400 400 800 450 L 800 600 L 0 600 Z' fill='%230f0f1a'/></svg>"
    }

    # Generate SVG character images for each character
    character_images = {}
    colors = ["f9d5e5", "b06ab3", "6a0572", "d1d1e0", "800000", "333333", "e6ccb2", "7b7554", "c0d6df", "4a6fa5"]

    characters = book_analysis.get("characters", [])
    if not characters:
        characters = [
            {"id": "protagonist", "name": "Protagonist", "description": "The main character"},
            {"id": "supporting", "name": "Supporting Character", "description": "A helpful friend"},
            {"id": "antagonist", "name": "Antagonist", "description": "The opposition"}
        ]

    for i, character in enumerate(characters):
        color_idx = i % len(colors)
        character_images[character["id"]] = f"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 250'><rect x='35' y='20' width='30' height='30' rx='15' fill='%23{colors[color_idx]}'/><rect x='30' y='50' width='40' height='60' fill='%23{colors[(color_idx+1) % len(colors)]}'/><rect x='25' y='110' width='50' height='50' fill='%23{colors[(color_idx+2) % len(colors)]}'/><rect x='25' y='110' width='20' height='70' rx='5' fill='%23{colors[(color_idx+2) % len(colors)]}'/><rect x='55' y='110' width='20' height='70' rx='5' fill='%23{colors[(color_idx+2) % len(colors)]}'/></svg>"

    # Get character names
    character_names = {char["id"]: char["name"] for char in characters}

    # Create intro scene
    intro_scene = {
        "id": "scene_1",
        "background": backgrounds["main"],
        "characters": [],
        "dialogue": [
            {
                "speaker": "Narrator",
                "text": f"Welcome to the world of {title}."
            },
            {
                "speaker": "Narrator",
                "text": book_analysis.get("plot", {}).get("summary", "An exciting adventure awaits!")
            }
        ]
    }

    # Add character introductions
    for character in characters[:2]:  # Limit to first 2 characters to keep it simple
        intro_scene["dialogue"].append({
            "speaker": "Narrator",
            "text": f"Meet {character['name']}, {character['description']}."
        })

    # Add choice to the intro
    intro_scene["dialogue"].append({
        "speaker": "Narrator",
        "text": "How would you like to begin this adventure?",
        "choices": [
            {"text": "With courage and determination", "nextScene": "scene_2"},
            {"text": "With caution and planning", "nextScene": "scene_3"},
            {"text": "Let fate decide my path", "nextScene": "scene_4"}
        ]
    })

    vn_script["scenes"].append(intro_scene)

    # Create main character path
    main_char = characters[0] if characters else {"id": "protagonist", "name": "Protagonist"}

    main_scene = {
        "id": "scene_2",
        "background": backgrounds["main"],
        "characters": [
            {"id": main_char["id"], "image": character_images[main_char["id"]]}
        ],
        "dialogue": [
            {
                "speaker": character_names.get(main_char["id"], "Protagonist"),
                "text": "I need to face this challenge head-on.",
                "character": main_char["id"]
            },
            {
                "speaker": "Narrator",
                "text": "With determination guiding your steps, you move forward."
            },
            {
                "speaker": character_names.get(main_char["id"], "Protagonist"),
                "text": "What path should I take?",
                "character": main_char["id"],
                "choices": [
                    {"text": "The direct approach", "nextScene": "scene_5"},
                    {"text": "Seek allies first", "nextScene": "scene_6"},
                    {"text": "Gather more information", "nextScene": "scene_7"}
                ]
            }
        ]
    }

    vn_script["scenes"].append(main_scene)

    # Add a few more scenes to ensure there's enough content
    planning_scene = {
        "id": "scene_3",
        "background": backgrounds["secondary"],
        "characters": [
            {"id": main_char["id"], "image": character_images[main_char["id"]]}
        ],
        "dialogue": [
            {
                "speaker": character_names.get(main_char["id"], "Protagonist"),
                "text": "I need to plan carefully before proceeding.",
                "character": main_char["id"]
            },
            {
                "speaker": "Narrator",
                "text": "Taking your time to consider options might reveal hidden paths."
            },
            {
                "speaker": character_names.get(main_char["id"], "Protagonist"),
                "text": "What should I focus on first?",
                "character": main_char["id"],
                "choices": [
                    {"text": "Study the situation", "nextScene": "scene_8"},
                    {"text": "Prepare equipment", "nextScene": "scene_9"},
                    {"text": "Consult with others", "nextScene": "scene_10"}
                ]
            }
        ]
    }

    vn_script["scenes"].append(planning_scene)

    # Add fate scene
    fate_scene = {
        "id": "scene_4",
        "background": backgrounds["dark"],
        "characters": [],
        "dialogue": [
            {
                "speaker": "Narrator",
                "text": "You surrender to the flow of the story, letting fate guide your journey."
            },
            {
                "speaker": "Narrator",
                "text": "Sometimes the most interesting paths are those we don't choose ourselves."
            },
            {
                "speaker": "Narrator",
                "text": "As you drift with the current of the narrative, you find yourself drawn to...",
                "choices": [
                    {"text": "A mysterious encounter", "nextScene": "scene_7"},
                    {"text": "An unexpected opportunity", "nextScene": "scene_8"},
                    {"text": "A moment of revelation", "nextScene": "scene_9"}
                ]
            }
        ]
    }

    vn_script["scenes"].append(fate_scene)

    # Add several more placeholder scenes for other paths
    basic_scenes = [
        {"id": "scene_5", "background": backgrounds["main"]},
        {"id": "scene_6", "background": backgrounds["secondary"]},
        {"id": "scene_7", "background": backgrounds["dark"]},
        {"id": "scene_8", "background": backgrounds["secondary"]},
        {"id": "scene_9", "background": backgrounds["main"]},
        {"id": "scene_10", "background": backgrounds["dark"]}
    ]

    # Populate basic scenes with simple content
    for i, scene_info in enumerate(basic_scenes):
        scene = {
            "id": scene_info["id"],
            "background": scene_info["background"],
            "characters": [],
            "dialogue": [
                {
                    "speaker": "Narrator",
                    "text": f"Your journey continues along this path..."
                },
                {
                    "speaker": "Narrator",
                    "text": "What would you like to do next?",
                    "choices": [
                        {"text": "Return to the beginning", "nextScene": "scene_1"},
                        {"text": "Continue on this path", "nextScene": "scene_1"}
                    ]
                }
            ]
        }

        # Add a character to some scenes
        if i % 2 == 0 and characters:
            char = characters[i % len(characters)]
            scene["characters"].append({
                "id": char["id"],
                "image": character_images[char["id"]]
            })

            # Add character dialogue
            scene["dialogue"].insert(1, {
                "speaker": character_names.get(char["id"], "Character"),
                "text": "This path has its own challenges and rewards.",
                "character": char["id"]
            })

        vn_script["scenes"].append(scene)

    return vn_script

# Create a placeholder scene when generation fails
async def generate_placeholder_scene(scene_id, book_analysis):
    """Generate a simple placeholder scene when Gemini generation fails"""
    print(f"Creating placeholder scene for {scene_id}")

    # Get characters from book analysis
    characters = book_analysis.get("characters", [])
    char_ids = [char.get("id", f"char_{i}") for i, char in enumerate(characters[:2])]

    # Create a basic scene with minimal content
    scene = {
        "id": scene_id,
        "background": "library",
        "characters": [{"id": char_id, "image": "default"} for char_id in char_ids],
        "dialogue": [
            {
                "speaker": "Narrator",
                "text": "The story continues...",
                "character": None
            },
            {
                "speaker": characters[0].get("name", "Character") if characters else "Character",
                "text": "We should proceed carefully from here.",
                "character": char_ids[0] if char_ids else "protagonist"
            },
            {
                "speaker": characters[1].get("name", "Friend") if len(characters) > 1 else "Friend",
                "text": "I agree. Let's consider our options.",
                "character": char_ids[1] if len(char_ids) > 1 else "supporting"
            },
            {
                "speaker": "Narrator",
                "text": "What will you do?",
                "character": None,
                "choices": [
                    {
                        "text": "Continue the adventure",
                        "nextScene": f"scene_{random.randint(1000, 9999)}"
                    },
                    {
                        "text": "Take a different path",
                        "nextScene": f"scene_{random.randint(1000, 9999)}"
                    }
                ]
            }
        ],
        "generated_with": "Placeholder"
    }

    # Store in cache
    STORY_CACHE["generated_scenes"][scene_id] = scene

    # Update generation info
    if "generation_info" in STORY_CACHE:
        if "scene_models" not in STORY_CACHE["generation_info"]:
            STORY_CACHE["generation_info"]["scene_models"] = {}
        STORY_CACHE["generation_info"]["scene_models"][scene_id] = "Placeholder"

    return scene

# New function for runtime scene generation
async def generate_next_scene(next_scene_id, client=None):
    """
    Generate a new scene at runtime if it doesn't exist yet
    This is called by the frontend when a scene is needed but not yet generated
    """
    # Check if we already have this scene in cache
    if next_scene_id in STORY_CACHE["generated_scenes"]:
        return STORY_CACHE["generated_scenes"][next_scene_id]

    # Check if this scene is referenced in the scene graph
    scene_graph = STORY_CACHE["scene_graph"]
    if next_scene_id in scene_graph:
        # Get info about incoming connections to help generate context
        incoming = scene_graph[next_scene_id]["incoming"]

        # Create a simple outline for this scene
        scene_outline = {
            "id": next_scene_id,
            "description": f"Continuation of the story from {', '.join([conn['source'] for conn in incoming])}",
            "characters": [],  # Will be populated based on context
            "setting": "A location appropriate to the story progression",
            "atmosphere": "Consistent with the narrative tone",
            "dialogue_count": random.randint(7, 10),
            "connects_to": []  # Will be filled dynamically
        }

        # Get the book analysis from cache
        book_analysis = STORY_CACHE["book_analysis"]

        # Try to determine characters based on incoming scenes
        for conn in incoming:
            source_id = conn["source"]
            if source_id in STORY_CACHE["generated_scenes"]:
                source_scene = STORY_CACHE["generated_scenes"][source_id]
                for char in source_scene.get("characters", []):
                    char_id = char.get("id")
                    if char_id and char_id not in scene_outline["characters"]:
                        scene_outline["characters"].append(char_id)

        # Use Gemini for scene generation
        if GEMINI_AVAILABLE:
            try:
                print(f"Generating scene {next_scene_id} with Gemini...")
                scene = await generate_scene_from_outline_with_gemini(scene_outline, book_analysis)
                if scene:
                    # Update generation info
                    if "generation_info" in STORY_CACHE:
                        if "scene_models" not in STORY_CACHE["generation_info"]:
                            STORY_CACHE["generation_info"]["scene_models"] = {}
                        STORY_CACHE["generation_info"]["scene_models"][next_scene_id] = "Gemini"

                    # Add model info to the scene
                    scene["generated_with"] = "Gemini"
                    return scene
                print("Gemini generation failed, using placeholder scene")
                return await generate_placeholder_scene(next_scene_id, book_analysis)
            except Exception as e:
                print(f"Error generating scene with Gemini: {str(e)}, using placeholder scene")
                return await generate_placeholder_scene(next_scene_id, book_analysis)
        else:
            print("Gemini not available, using placeholder scene")
            return await generate_placeholder_scene(next_scene_id, book_analysis)

    # If we don't have any info about this scene, create a generic one
    print(f"No context available for scene {next_scene_id}, creating generic scene")

    # Create a simple outline
    scene_outline = {
        "id": next_scene_id,
        "description": "Continuation of the adventure",
        "characters": [],
        "setting": "A location within the story world",
        "atmosphere": "Consistent with the narrative",
        "dialogue_count": 8,
        "connects_to": ["scene_next", "scene_alt"]
    }

    # Get the book analysis from cache
    book_analysis = STORY_CACHE["book_analysis"]

    # Use Gemini for generic scene generation
    if GEMINI_AVAILABLE:
        try:
            print(f"Generating generic scene {next_scene_id} with Gemini...")
            scene = await generate_scene_from_outline_with_gemini(scene_outline, book_analysis)
            if scene:
                # Update generation info
                if "generation_info" in STORY_CACHE:
                    if "scene_models" not in STORY_CACHE["generation_info"]:
                        STORY_CACHE["generation_info"]["scene_models"] = {}
                    STORY_CACHE["generation_info"]["scene_models"][next_scene_id] = "Gemini"

                # Add model info to the scene
                scene["generated_with"] = "Gemini"
                return scene
            print("Gemini generation failed, using placeholder scene")
            return await generate_placeholder_scene(next_scene_id, book_analysis)
        except Exception as e:
            print(f"Error generating generic scene with Gemini: {str(e)}, using placeholder scene")
            return await generate_placeholder_scene(next_scene_id, book_analysis)
    else:
        print("Gemini not available, using placeholder scene")
        return await generate_placeholder_scene(next_scene_id, book_analysis)
