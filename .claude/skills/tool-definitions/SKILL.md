---
name: tool-definitions
description: Standard for defining tools that our agents call (Claude API tool use). Use this skill whenever creating, adding, editing, or reviewing a tool definition, tool schema, or tools.py — any dict with "name", "description", and "input_schema". Makes sure every tool has a clear, unique description so Claude always knows which tool fits which action.
---

# Writing Tool Definitions for Our Agents

Claude picks a tool by reading its `name` and `description`. When two descriptions are vague or overlap, Claude can pick the wrong tool or skip the right one. **Every tool must have a description that is clear, specific, and different from every other tool's description.**

## Required structure

Every tool follows this exact shape:

```python
tool = {
    "name": "tool_name",
    "description": "Plain English explanation of what this tool does",
    "input_schema": {
        "type": "object",
        "properties": {
            "param_name": {
                "type": "string",
                "description": "What this parameter is and what it accepts"
            }
        },
        "required": ["param_name"]
    }
}
```

- `name`: `snake_case`, verb first, specific (`lookup_order_status`, not `order` or `get_data`).
- `description`: plain English, see rules below.
- `input_schema`: always `"type": "object"` with `properties` and `required`.
- Every parameter has a `type` **and** a `description`. No exceptions.
- `required` lists only the parameters the tool truly cannot run without.

## Rules for the tool description

Write 2–4 sentences that answer:

1. **What it does** — the one action this tool performs.
2. **When to use it** — the situation or user request that calls for it.
3. **When NOT to use it** — name the sibling tool to use instead if there's one that sounds similar.
4. **What it returns** — what Claude gets back.

Rules:
- Start with an action verb: "Looks up…", "Creates…", "Cancels…".
- Be specific about the object it acts on (an *order*, a *refund*, a *support ticket*), not "data" or "info".
- Never reuse the same description, or a lightly reworded one, across tools.
- No marketing words ("powerful", "smart"). No jargon a new teammate wouldn't understand.

## Rules for parameter descriptions

Say what the value is, its format, and an example where helpful:
- Good: `"The customer's order ID, e.g. 'ORD-10482'. Found in the confirmation email."`
- Bad: `"The ID"`

Use `enum` when only certain values are allowed. Note units and formats (dates as `MM-DD-YYYY`, money in cents, etc.).

## Uniqueness check (do this every time)

Before finishing, compare the new tool against **every existing tool** in the file:

1. Read all tool names and descriptions side by side.
2. Ask: "If a user said ___, could Claude reasonably pick either of these?" If yes, the descriptions are too close.
3. Fix it by making the action and the trigger situation sharper, and add a "Do not use this for X — use `other_tool` instead" line to both tools.
4. If two tools really do the same thing, merge them into one.

## Example: two similar tools, told apart

```python
lookup_order_status = {
    "name": "lookup_order_status",
    "description": (
        "Looks up the current shipping and fulfillment status of a single existing order. "
        "Use when a customer asks where their order is or when it will arrive. "
        "Do not use this to change or cancel an order — use cancel_order for that. "
        "Returns the status, carrier, tracking number, and estimated delivery date."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "order_id": {
                "type": "string",
                "description": "The customer's order ID, e.g. 'ORD-10482'."
            }
        },
        "required": ["order_id"]
    }
}

cancel_order = {
    "name": "cancel_order",
    "description": (
        "Cancels an existing order that has not shipped yet. "
        "Use only when the customer clearly asks to cancel. "
        "Do not use this to check order status — use lookup_order_status for that. "
        "Returns whether the cancellation succeeded and, if not, why."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "order_id": {
                "type": "string",
                "description": "The ID of the order to cancel, e.g. 'ORD-10482'."
            },
            "reason": {
                "type": "string",
                "enum": ["changed_mind", "ordered_by_mistake", "too_slow", "other"],
                "description": "Why the customer is cancelling."
            }
        },
        "required": ["order_id"]
    }
}
```

## Final checklist

- [ ] Follows the exact structure above
- [ ] Name is `snake_case`, verb first, specific
- [ ] Description says what, when, when not, and what it returns
- [ ] Description is clearly different from every other tool's
- [ ] Every parameter has a `type` and a `description`
- [ ] `required` is correct
