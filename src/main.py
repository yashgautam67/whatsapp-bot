import os
import json
import urllib.request

from openai import OpenAI
from azure.identity import DefaultAzureCredential, get_bearer_token_provider

from appwrite.client import Client
from appwrite.services.tables_db import TablesDB
from appwrite.id import ID
from appwrite.query import Query


# =========================
# Azure AI Foundry
# =========================

AZURE_ENDPOINT = os.environ.get(
    "AZURE_AI_ENDPOINT",
    "https://samjho-ai.services.ai.azure.com/openai/v1"
)

AZURE_DEPLOYMENT = os.environ.get(
    "AZURE_AI_DEPLOYMENT",
    "gpt-5.6-luna"
)

token_provider = get_bearer_token_provider(
    DefaultAzureCredential(),
    "https://ai.azure.com/.default"
)

openai_client = OpenAI(
    base_url=AZURE_ENDPOINT,
    api_key=token_provider
)


# =========================
# WhatsApp
# =========================

WHATSAPP_ACCESS_TOKEN = os.environ["WHATSAPP_ACCESS_TOKEN"]
WHATSAPP_PHONE_NUMBER_ID = os.environ["WHATSAPP_PHONE_NUMBER_ID"]

WHATSAPP_API_URL = (
    f"https://graph.facebook.com/v23.0/"
    f"{WHATSAPP_PHONE_NUMBER_ID}/messages"
)


# =========================
# Appwrite Database
# =========================

APPWRITE_DATABASE_ID = os.environ["APPWRITE_DATABASE_ID"]
APPWRITE_TABLE_ID = os.environ["APPWRITE_TABLE_ID"]


# =========================
# Send WhatsApp message
# =========================

def send_whatsapp_message(to, text):

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {
            "preview_url": False,
            "body": text
        }
    }

    data = json.dumps(payload).encode("utf-8")

    request = urllib.request.Request(
        WHATSAPP_API_URL,
        data=data,
        headers={
            "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
            "Content-Type": "application/json"
        },
        method="POST"
    )

    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read().decode("utf-8")


# =========================
# Appwrite Client
# =========================

def get_appwrite_database(context):

    appwrite_key = context.req.headers.get("x-appwrite-key")

    client = (
        Client()
        .set_endpoint(os.environ["APPWRITE_FUNCTION_API_ENDPOINT"])
        .set_project(os.environ["APPWRITE_FUNCTION_PROJECT_ID"])
        .set_key(appwrite_key)
    )

    return TablesDB(client)


# =========================
# Save message
# =========================

def save_message(
    tables_db,
    user_number,
    role,
    message,
    message_id
):

    tables_db.create_row(
        database_id=APPWRITE_DATABASE_ID,
        table_id=APPWRITE_TABLE_ID,
        row_id=ID.unique(),
        data={
            "user_number": user_number,
            "role": role,
            "message": message,
            "message_id": message_id
        }
    )


# =========================
# Helper to parse Appwrite row
# =========================

def parse_row(row):

    # In Appwrite TablesDB, attributes are at top-level of row dict.
    # Support nested 'data' dict if present for backwards compatibility.
    data = row.get("data") if isinstance(row.get("data"), dict) else row

    role = data.get("role") or row.get("role")
    message = data.get("message") or row.get("message")
    user_number = data.get("user_number") or row.get("user_number")
    message_id = data.get("message_id") or row.get("message_id")
    created_at = row.get("$createdAt") or data.get("$createdAt", "")

    return {
        "role": role,
        "message": message,
        "user_number": user_number,
        "message_id": message_id,
        "created_at": created_at
    }


# =========================
# Fetch recent messages
# =========================

def fetch_recent_messages(tables_db, user_number, context):

    try:
        result = tables_db.list_rows(
            database_id=APPWRITE_DATABASE_ID,
            table_id=APPWRITE_TABLE_ID,
            queries=[
                Query.equal("user_number", [user_number]),
                Query.order_desc("$createdAt"),
                Query.limit(30)
            ]
        )
    except Exception as query_err:
        context.log(
            f"Query with user_number and order_desc failed ({str(query_err)}), falling back"
        )
        result = tables_db.list_rows(
            database_id=APPWRITE_DATABASE_ID,
            table_id=APPWRITE_TABLE_ID,
            queries=[
                Query.limit(100)
            ]
        )

    raw_rows = result.get("rows", [])
    parsed_rows = [parse_row(r) for r in raw_rows]

    # Filter to only this user's messages
    user_rows = [
        r for r in parsed_rows
        if not r["user_number"] or str(r["user_number"]) == str(user_number)
    ]

    return user_rows


# =========================
# Build chat history
# =========================

def build_chat_history(user_rows, current_message_id, context):

    history = []

    for item in user_rows:

        # Exclude the current incoming message if already in the list
        if current_message_id and item.get("message_id") == current_message_id:
            continue

        role = item.get("role")
        message = item.get("message")

        # Only valid AI roles
        if role not in ["user", "assistant", "system", "developer"]:
            continue

        if not message:
            continue

        history.append(item)

    # Sort chronologically from oldest to newest
    history.sort(key=lambda x: x.get("created_at", ""))

    # Deduplicate consecutive identical messages or duplicate message IDs
    seen_message_ids = set()
    clean_history = []
    for item in history:
        msg_id = item.get("message_id")
        if msg_id:
            if msg_id in seen_message_ids:
                continue
            seen_message_ids.add(msg_id)

        if (
            clean_history
            and clean_history[-1]["role"] == item["role"]
            and clean_history[-1]["message"] == item["message"]
        ):
            continue

        clean_history.append(item)

    # Keep last 20 messages for context
    trimmed_history = clean_history[-20:]

    context.log(
        f"History found for user: {len(trimmed_history)} messages"
    )

    for item in trimmed_history:
        context.log(
            f"HISTORY → {item['role']}: {item['message']}"
        )

    return trimmed_history


# =========================
# Appwrite Function
# =========================

def main(context):

    # -------------------------
    # WhatsApp webhook verify
    # -------------------------

    if context.req.method == "GET":

        mode = context.req.query.get("hub.mode")
        token = context.req.query.get("hub.verify_token")
        challenge = context.req.query.get("hub.challenge")

        verify_token = os.environ.get("VERIFY_TOKEN")

        if mode == "subscribe" and token == verify_token:

            context.log(
                "WhatsApp webhook verification successful"
            )

            return context.res.text(
                challenge or ""
            )

        return context.res.text(
            "Forbidden",
            403
        )


    # -------------------------
    # Only POST
    # -------------------------

    if context.req.method != "POST":

        return context.res.text(
            "Method Not Allowed",
            405
        )


    try:

        # -------------------------
        # Parse WhatsApp event
        # -------------------------

        body = context.req.body_json or {}

        message = (
            body
            .get("entry", [{}])[0]
            .get("changes", [{}])[0]
            .get("value", {})
            .get("messages", [None])[0]
        )


        # -------------------------
        # Ignore status updates
        # -------------------------

        if not message:

            return context.res.text(
                "EVENT_RECEIVED",
                200
            )


        # -------------------------
        # Only text messages
        # -------------------------

        if message.get("type") != "text":

            return context.res.text(
                "EVENT_RECEIVED",
                200
            )


        user_number = message.get("from")

        user_message = (
            message
            .get("text", {})
            .get("body", "")
            .strip()
        )

        whatsapp_message_id = message.get("id")


        context.log(
            f"WhatsApp message from {user_number}: {user_message}"
        )


        # -------------------------
        # Appwrite database
        # -------------------------

        tables_db = get_appwrite_database(context)


        # -------------------------
        # Fetch recent messages
        # -------------------------

        recent_messages = fetch_recent_messages(
            tables_db,
            user_number,
            context
        )


        # -------------------------
        # Check duplicate webhook
        # -------------------------

        is_duplicate = any(
            r.get("message_id") == whatsapp_message_id
            for r in recent_messages
        )

        if is_duplicate:
            context.log(
                f"Duplicate webhook detected for message_id {whatsapp_message_id}. Skipping."
            )
            return context.res.json({
                "ok": True,
                "status": "duplicate_skipped"
            })


        # -------------------------
        # Build prior history
        # -------------------------

        history = build_chat_history(
            recent_messages,
            whatsapp_message_id,
            context
        )


        # -------------------------
        # Save user message
        # -------------------------

        save_message(
            tables_db,
            user_number,
            "user",
            user_message,
            whatsapp_message_id
        )


        # -------------------------
        # Build AI conversation
        # -------------------------

        ai_input = []

        for item in history:

            ai_input.append({
                "role": item["role"],
                "content": item["message"]
            })


        ai_input.append({
            "role": "user",
            "content": user_message
        })


        # -------------------------
        # Send history to Azure AI
        # -------------------------

        response = openai_client.responses.create(
            model=AZURE_DEPLOYMENT,
            input=ai_input
        )

        ai_reply = response.output_text.strip()


        context.log(
            f"AI reply: {ai_reply}"
        )


        # -------------------------
        # Save AI reply
        # -------------------------

        save_message(
            tables_db,
            user_number,
            "assistant",
            ai_reply,
            f"assistant-{whatsapp_message_id}"
        )


        # -------------------------
        # Send reply to WhatsApp
        # -------------------------

        send_whatsapp_message(
            user_number,
            ai_reply
        )


        return context.res.json({
            "ok": True
        })


    except Exception as error:

        context.error(
            f"Function error: {str(error)}"
        )

        return context.res.json({
            "ok": False,
            "error": "Internal server error"
        }, 500)
