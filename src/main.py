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

def save_message(tables_db, user_number, role, message, message_id):

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
# Get chat history
# =========================

def get_chat_history(tables_db, user_number):

    result = tables_db.list_rows(
        database_id=APPWRITE_DATABASE_ID,
        table_id=APPWRITE_TABLE_ID,
        queries=[
            Query.limit(100)
        ]
    )

    history = []

    for row in result.rows:

        data = row.data

        if data.get("user_number") == user_number:

            history.append({
                "role": data.get("role"),
                "message": data.get("message"),
                "created_at": row.created_at
            })

    history.sort(
        key=lambda x: x["created_at"]
    )

    return history[-20:]


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


        # Ignore status updates
        if not message:

            return context.res.text(
                "EVENT_RECEIVED",
                200
            )


        # Only text messages
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
        # Get previous history
        # -------------------------

        history = get_chat_history(
            tables_db,
            user_number
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
