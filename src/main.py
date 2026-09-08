import os
import json
import urllib.request

from openai import OpenAI
from azure.identity import DefaultAzureCredential, get_bearer_token_provider


# =========================
# Azure AI Foundry
# =========================

AZURE_ENDPOINT = os.environ.get(
    "AZURE_AI_ENDPOINT",
    "https://samjho-ai.services.ai.azure.com/openai/v1"
)

AZURE_DEPLOYMENT = os.environ.get(
    "AZURE_AI_DEPLOYMENT",
    "gpt-5-mini"
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
            context.log("WhatsApp webhook verification successful")
            return context.res.text(challenge or "")

        return context.res.text("Forbidden", 403)


    # -------------------------
    # Incoming WhatsApp message
    # -------------------------

    if context.req.method != "POST":
        return context.res.text("Method Not Allowed", 405)


    try:

        body = context.req.body_json or {}

        message = (
            body
            .get("entry", [{}])[0]
            .get("changes", [{}])[0]
            .get("value", {})
            .get("messages", [None])[0]
        )

        # Ignore status updates and other webhook events
        if not message:
            return context.res.text("EVENT_RECEIVED", 200)


        # Only handle text messages
        if message.get("type") != "text":
            return context.res.text("EVENT_RECEIVED", 200)


        user_number = message.get("from")
        user_message = (
            message.get("text", {})
            .get("body", "")
            .strip()
        )


        context.log(
            f"WhatsApp message from {user_number}: {user_message}"
        )


        # -------------------------
        # Send message to Azure AI
        # -------------------------

        response = openai_client.responses.create(
            model=AZURE_DEPLOYMENT,
            input=user_message
        )

        ai_reply = response.output_text.strip()


        context.log(f"AI reply: {ai_reply}")


        # -------------------------
        # Send AI reply to WhatsApp
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
