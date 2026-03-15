"""
Tool Registry — Consolidated for the Vision ReAct Agent.
"""

BROWSER_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "click_element",
            "description": "Click an interactive element by its ID. Use for buttons, links, checkboxes, and any other clickable element visible in the page state.",
            "parameters": {
                "type": "object",
                "properties": {
                    "element_id": {"type": "integer", "description": "The element ID from the current page state."}
                },
                "required": ["element_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "type_text",
            "description": "Type text into an input field. For search boxes use press_enter=true. For autocomplete/address fields use press_enter=false then press_key(ArrowDown) + press_key(Enter) to select a suggestion.",
            "parameters": {
                "type": "object",
                "properties": {
                    "element_id": {"type": "integer", "description": "The input field ID from the current page state."},
                    "text": {"type": "string", "description": "The text to type."},
                    "press_enter": {"type": "boolean", "description": "Press Enter after typing. True for search boxes; False for autocomplete fields."}
                },
                "required": ["element_id", "text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "select_option",
            "description": "Select an option from a native HTML <select> element by its visible label. Use only for browser-native dropdowns. For custom styled dropdowns use click_element instead.",
            "parameters": {
                "type": "object",
                "properties": {
                    "element_id": {"type": "integer", "description": "The <select> element ID."},
                    "value": {"type": "string", "description": "The visible label of the option to select."}
                },
                "required": ["element_id", "value"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "press_key",
            "description": "Press a keyboard key. Use ArrowDown/ArrowUp to navigate autocomplete suggestions, Enter to confirm, Escape to close a dropdown, Tab to move focus.",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "Key name: ArrowDown, ArrowUp, Enter, Tab, Escape, Backspace, Space."
                    }
                },
                "required": ["key"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "scroll_page",
            "description": "Scroll up or down to reveal more content. Use only when you need to reach interactive elements not yet visible. Stop after 3 scrolls and try a different approach.",
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {"type": "string", "enum": ["up", "down"], "description": "Scroll direction."}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "scroll_to_element",
            "description": "Scroll until a specific element is visible in the viewport. Use when the element ID is already in the page state but outside the visible area.",
            "parameters": {
                "type": "object",
                "properties": {
                    "element_id": {"type": "integer", "description": "The element ID to scroll to."}
                },
                "required": ["element_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "extract_hidden_text",
            "description": (
                "Scan the raw page text and return excerpts matching a query. "
                "ONLY use this to retrieve data that is NOT in the elements list: "
                "order confirmation numbers, booking codes, delivery status, error messages buried in text. "
                "NEVER use this to: check if a product exists, verify search results, "
                "find navigation targets, or look for elements — use the elements list for all of that."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The text to search for (case-insensitive)."}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "go_to_url",
            "description": "Navigate the browser to a URL. The only way to open a new page. Can also be used for site search if the URL format is known (e.g. site.com/search?q=...).",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The URL to navigate to."}
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "go_back",
            "description": "Navigate to the previous page in browser history.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "wait_for_page",
            "description": "Wait for page content to load. Use after navigation or triggering animations when content hasn't appeared yet.",
            "parameters": {
                "type": "object",
                "properties": {
                    "seconds": {"type": "number", "description": "Seconds to wait (0.5–10).", "minimum": 0.5, "maximum": 10}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ask_user",
            "description": "Ask the user a question. Use when critical information is missing and cannot be inferred, when confirmation is needed before an irreversible action, or when a captcha requires manual solving.",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "The question for the user."}
                },
                "required": ["question"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "mark_subtask_done",
            "description": (
                "Mark a subtask as done or unavailable. "
                "The checklist is pre-populated for you — you only need to update statuses.\n"
                "Call with status='done' immediately after successfully completing a subtask "
                "(added to cart, filed a form, found the info, etc.).\n"
                "Call with status='unavailable' after 2 failed search attempts — then move on.\n"
                "Always check the Task Checklist before searching: ✓ subtasks are done, never search for them again."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "subtask": {
                        "type": "string",
                        "description": "Name of the subtask, matching the name shown in the Task Checklist."
                    },
                    "status": {
                        "type": "string",
                        "enum": ["done", "unavailable"],
                        "description": "'done' — completed successfully; 'unavailable' — not found after 2 attempts."
                    }
                },
                "required": ["subtask", "status"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "finish_task",
            "description": "Call when the task is fully completed. Provide a concise summary of what was done.",
            "parameters": {
                "type": "object",
                "properties": {
                    "result": {"type": "string", "description": "Summary of what was accomplished."}
                },
                "required": ["result"]
            }
        }
    }
]